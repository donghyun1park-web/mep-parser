"""
cfd_run.py — 생성된 케이스를 WSL OpenFOAM 으로 실행하고 결과를 회수

Windows에서 한 명령으로: WSL 홈(~/cfd_runs/<name>)에 복사(/mnt/c 9p 느림 회피) →
OpenFOAM 환경 source 후 Allrun(포그라운드) → 로그 스트리밍 → 로그·postProcessing·마지막
time 디렉토리만 Windows 케이스로 회수(GB 방지).

사용:
  python cfd_run.py case_pilot
  python cfd_run.py case_pilot --keep-mesh   # polyMesh 도 회수(디버깅)

모듈로도 사용(cfd_studio 등):
  from cfd_run import run_case, check_openfoam
  r = run_case("case_pilot", progress_cb=my_line_handler)   # r["ok"], r["error"]

전제: WSL2 + Ubuntu-24.04 + OpenFOAM v2606. 없으면 원클릭 설치기를 안내.
"""
import argparse
import base64
from datetime import datetime, timezone
import glob
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import uuid

try:
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")
except (AttributeError, OSError):
    pass

OF_BASHRC = "/usr/share/openfoam/etc/bashrc"
WSL_DISTRO_ENV = "MEP_CFD_WSL_DISTRO"
OPENFOAM_BASHRC_ENV = "MEP_CFD_OPENFOAM_BASHRC"
RUNTIME_COMMANDS = (
    "blockMesh", "topoSet", "createPatch", "checkMesh",
    "buoyantBoussinesqSimpleFoam", "buoyantBoussinesqPimpleFoam",
    "simpleFoam", "pimpleFoam", "postProcess",
)
BODY_FITTED_COMMANDS = (
    "surfaceCheck", "surfaceFeatureExtract", "snappyHexMesh", "foamToVTK",
)
# These are deliberately separate from RUNTIME_COMMANDS. Missing MPI must not
# block the safe serial solver path, but it does block a parallel benchmark.
MPI_COMMANDS = ("mpirun", "decomposePar", "reconstructPar")
ALL_OPENFOAM_COMMANDS = RUNTIME_COMMANDS + BODY_FITTED_COMMANDS + MPI_COMMANDS


def _wsl_args(cmd, distro=None):
    """Build a WSL command without interpolating a distro into shell text."""
    full = ["wsl"]
    if distro:
        full.extend(["-d", distro])
    full.extend(["-e", "bash", "-c", cmd])
    return full


def _wsl(cmd, distro=None, timeout=None):
    """WSL bash -c 로 명령 실행(캡처)."""
    proc = subprocess.run(_wsl_args(cmd, distro), capture_output=True, timeout=timeout)
    proc.stdout = _decode_wsl_output(proc.stdout)
    proc.stderr = _decode_wsl_output(proc.stderr)
    return proc


MPI_RUNTIME_SMOKE_CONTRACT = "mpi_runtime_smoke.v1"
_MPI_SMOKE_REMOTE_PREFIX = "/tmp/mep-cfd-mpi-smoke-"
_MPI_SMOKE_ENV_NAME = "MEP_CFD_MPI_SMOKE_TOKEN"


def _wsl_script_args(script, distro=None):
    """Pass a multi-line WSL script as base64, avoiding Windows quote loss.

    ``wsl.exe`` has a separate Windows command-line parser before bash sees its
    arguments.  A base64 payload keeps the smoke wrapper's PID/PGID quoting
    intact and is used only for Studio-owned diagnostic scripts.
    """
    encoded = base64.b64encode(str(script).encode("utf-8")).decode("ascii")
    command = "printf %s " + shlex.quote(encoded) + " | base64 -d | bash"
    return _wsl_args(command, distro)


def _positive_int(value, default=None, *, minimum=1):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed >= minimum else default


def _normalise_mpi_smoke_ranks(values):
    ranks = []
    for value in values or ():
        parsed = _positive_int(value, minimum=2)
        if parsed is None:
            raise ValueError("MPI smoke rank는 2 이상의 정수여야 합니다.")
        if parsed not in ranks:
            ranks.append(parsed)
    if not ranks:
        raise ValueError("MPI smoke에 실행할 rank가 없습니다.")
    return tuple(ranks)


def _normalise_mpi_smoke_overrides(overrides):
    """Allow only one-run Open MPI environment overrides, never global config."""
    result = {}
    for name, value in dict(overrides or {}).items():
        key = str(name or "")
        text = str(value or "")
        if not re.fullmatch(r"OMPI_[A-Za-z0-9_]+", key):
            raise ValueError("MPI smoke 환경 변수는 OMPI_ 접두어만 허용합니다.")
        if "\x00" in text or "\n" in text or "\r" in text:
            raise ValueError("MPI smoke 환경 변수 값에 줄바꿈을 넣을 수 없습니다.")
        result[key] = text
    return result


def _mpi_smoke_remote_dir(token):
    if not re.fullmatch(r"[0-9a-f]{32}", str(token or "")):
        raise ValueError("MPI smoke 토큰 형식이 올바르지 않습니다.")
    return _MPI_SMOKE_REMOTE_PREFIX + token


def _mpi_smoke_identity_script():
    return "\n".join((
        "set -eu",
        "printf 'distro\\t%s\\n' \"${WSL_DISTRO_NAME:-}\"",
        "printf 'kernel\\t%s\\n' \"$(uname -r)\"",
        "printf 'mpirun_path\\t%s\\n' \"$(command -v mpirun 2>/dev/null || true)\"",
        "printf 'mpirun_version\\t%s\\n' \"$(LC_ALL=C mpirun --version 2>/dev/null | head -n 1 || true)\"",
        "printf 'ompi_info_version\\t%s\\n' \"$(LC_ALL=C ompi_info --version 2>/dev/null | head -n 1 || true)\"",
        "printf 'effective_cpu_count\\t%s\\n' \"$(nproc 2>/dev/null || true)\"",
    ))


def _mpi_smoke_identity(distro=None):
    """Read identity only; this does not create an MPI rank."""
    result = {
        "distro": str(distro or ""), "kernel": "", "mpirun_path": "",
        "mpirun_version": "", "ompi_info_version": "",
        "effective_cpu_count": None, "probe_error": "",
    }
    try:
        proc = subprocess.run(
            _wsl_script_args(_mpi_smoke_identity_script(), distro),
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        result["probe_error"] = str(exc)
        return result
    if proc.returncode != 0:
        result["probe_error"] = (proc.stderr or proc.stdout or "WSL probe failed").strip()[:1000]
        return result
    for line in (proc.stdout or "").splitlines():
        key, sep, value = line.partition("\t")
        if not sep:
            continue
        if key in result:
            result[key] = value.strip()
    result["effective_cpu_count"] = _positive_int(
        result.get("effective_cpu_count"), default=None
    )
    return result


def _mpi_smoke_active_processes(distro=None):
    """Return active MPI launcher/rank candidates before a smoke starts."""
    script = "\n".join((
        "set -eu",
        "ps -eo pid=,ppid=,pgid=,sid=,comm=,args= | "
        "awk '$5 ~ /^(mpirun|orterun|prte|prterun|orted)$/ { print }'",
    ))
    try:
        proc = subprocess.run(
            _wsl_script_args(script, distro), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return [f"probe-error: {exc}"]
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "WSL process probe failed").strip()
        return [f"probe-error: {detail[:500]}"]
    return [line.strip() for line in (proc.stdout or "").splitlines() if line.strip()]


def _mpi_smoke_command(ranks, token, environment_overrides):
    env = [f"{_MPI_SMOKE_ENV_NAME}={token}"]
    env.extend(f"{key}={value}" for key, value in environment_overrides.items())
    parts = ["env"] + [shlex.quote(value) for value in env]
    parts.extend(("mpirun", "-np", str(ranks), "hostname"))
    return " ".join(parts)


def _mpi_smoke_cleanup_lines():
    """Bash fragment that kills only a token-verified private process group."""
    return (
        "cleanup_status=CLEAN\n"
        "term_sent=0\n"
        "kill_sent=0\n"
        "leader=''\n"
        "pgid=''\n"
        "if [ -s \"$workdir/leader.pid\" ] && [ -s \"$workdir/pgid\" ]; then\n"
        "  leader=$(tr -d '[:space:]' < \"$workdir/leader.pid\")\n"
        "  pgid=$(tr -d '[:space:]' < \"$workdir/pgid\")\n"
        "  case \"$leader:$pgid\" in (*[!0-9:]*|'') cleanup_status=UNVERIFIED ;; esac\n"
        "  if [ \"$cleanup_status\" = CLEAN ]; then\n"
        "    members=$(ps -eo pid=,pgid= | awk -v target=\"$pgid\" '$2 == target { print $1 }')\n"
        "    if [ -n \"$members\" ]; then\n"
        "      verified=0\n"
        "      for pid in $members; do\n"
        "        if [ -r \"/proc/$pid/environ\" ] && tr '\\0' '\\n' < \"/proc/$pid/environ\" | grep -Fxq \"MEP_CFD_MPI_SMOKE_TOKEN=$token\"; then verified=1; fi\n"
        "      done\n"
        "      if [ \"$verified\" != 1 ]; then\n"
        "        cleanup_status=UNVERIFIED\n"
        "      else\n"
        "        kill -TERM -- \"-$pgid\" 2>/dev/null || true\n"
        "        term_sent=1\n"
        "        sleep \"$cleanup_grace_seconds\"\n"
        "        members=$(ps -eo pid=,pgid= | awk -v target=\"$pgid\" '$2 == target { print $1 }')\n"
        "        if [ -n \"$members\" ]; then\n"
        "          kill -KILL -- \"-$pgid\" 2>/dev/null || true\n"
        "          kill_sent=1\n"
        "          sleep 0.3\n"
        "        fi\n"
        "        members=$(ps -eo pid=,pgid= | awk -v target=\"$pgid\" '$2 == target { print $1 }')\n"
        "        [ -z \"$members\" ] || cleanup_status=RESIDUAL\n"
        "      fi\n"
        "    fi\n"
        "  fi\n"
        "else\n"
        "  cleanup_status=UNVERIFIED\n"
        "fi\n"
    )


def _mpi_smoke_wrapper_script(workdir, token, *, ranks, timeout_seconds,
                              cleanup_grace_seconds, environment_overrides=None):
    """Build one bounded, private-session ``mpirun hostname`` wrapper.

    It never invokes ``wsl --terminate`` or ``wsl --shutdown``.  Those commands
    can destroy unrelated engineering jobs in the same distro, so a failed
    ownership check is intentionally reported as ``UNVERIFIED`` instead.
    """
    ranks = _positive_int(ranks, minimum=2)
    timeout_seconds = _positive_int(timeout_seconds, default=10)
    cleanup_grace_seconds = _positive_int(cleanup_grace_seconds, default=3)
    environment_overrides = _normalise_mpi_smoke_overrides(environment_overrides)
    if ranks is None:
        raise ValueError("MPI smoke rank는 2 이상의 정수여야 합니다.")
    if not str(workdir).startswith(_MPI_SMOKE_REMOTE_PREFIX):
        raise ValueError("MPI smoke 작업 경로가 안전한 형식이 아닙니다.")
    _mpi_smoke_remote_dir(token)
    command = _mpi_smoke_command(ranks, token, environment_overrides)
    inner = "\n".join((
        "set -eu",
        'workdir="$1"',
        'printf "%s\\n" "$$" > "$workdir/leader.pid"',
        'ps -o pgid= -p "$$" | awk \'{print $1}\' > "$workdir/pgid"',
        'ps -o sid= -p "$$" | awk \'{print $1}\' > "$workdir/sid"',
        "exec " + command,
    ))
    return "\n".join((
        "set -u",
        "umask 077",
        "workdir=" + shlex.quote(str(workdir)),
        "token=" + shlex.quote(str(token)),
        "timeout_seconds=" + str(timeout_seconds),
        "cleanup_grace_seconds=" + str(cleanup_grace_seconds),
        'mkdir -p "$workdir"',
        "set +e",
        "timeout -k \"$cleanup_grace_seconds\" \"$timeout_seconds\" setsid bash -c "
        + shlex.quote(inner) + " bash \"$workdir\" > \"$workdir/stdout.txt\" 2> \"$workdir/stderr.txt\"",
        "runner_rc=$?",
        # The smoke wrapper must always emit its structured cleanup receipt.
        # A non-critical diagnostic command (for example no matching `ps`
        # row after a successful run) must not make the outer WSL process
        # exit before the receipt is printed.
        "set +e",
        "timed_out=0",
        'case "$runner_rc" in 124|137) timed_out=1 ;; esac',
        _mpi_smoke_cleanup_lines().rstrip("\n"),
        "stdout_b64=$(head -c 16384 \"$workdir/stdout.txt\" 2>/dev/null | base64 | tr -d '\\n')",
        "stderr_b64=$(head -c 16384 \"$workdir/stderr.txt\" 2>/dev/null | base64 | tr -d '\\n')",
        "retained=1",
        'if [ "$runner_rc" = 0 ] && [ "$cleanup_status" = CLEAN ]; then',
        '  rm -f "$workdir/leader.pid" "$workdir/pgid" "$workdir/sid" "$workdir/stdout.txt" "$workdir/stderr.txt"',
        '  if rmdir "$workdir" 2>/dev/null; then retained=0; fi',
        "fi",
        "printf 'mep_cfd_mpi_smoke\\treturncode\\t%s\\n' \"$runner_rc\"",
        "printf 'mep_cfd_mpi_smoke\\ttimed_out\\t%s\\n' \"$timed_out\"",
        "printf 'mep_cfd_mpi_smoke\\tcleanup_status\\t%s\\n' \"$cleanup_status\"",
        "printf 'mep_cfd_mpi_smoke\\tterm_sent\\t%s\\n' \"$term_sent\"",
        "printf 'mep_cfd_mpi_smoke\\tkill_sent\\t%s\\n' \"$kill_sent\"",
        "printf 'mep_cfd_mpi_smoke\\tretained\\t%s\\n' \"$retained\"",
        "printf 'mep_cfd_mpi_smoke\\tstdout_b64\\t%s\\n' \"$stdout_b64\"",
        "printf 'mep_cfd_mpi_smoke\\tstderr_b64\\t%s\\n' \"$stderr_b64\"",
    )) + "\n"


def _mpi_smoke_external_cleanup_script(workdir, token, cleanup_grace_seconds):
    """Generate a second-session cleanup command for a timed-out wrapper."""
    _mpi_smoke_remote_dir(token)
    grace = _positive_int(cleanup_grace_seconds, default=3)
    return "\n".join((
        "set -u",
        "workdir=" + shlex.quote(str(workdir)),
        "token=" + shlex.quote(str(token)),
        "cleanup_grace_seconds=" + str(grace),
        _mpi_smoke_cleanup_lines().rstrip("\n"),
        "printf 'mep_cfd_mpi_smoke\\tcleanup_status\\t%s\\n' \"$cleanup_status\"",
        "printf 'mep_cfd_mpi_smoke\\tterm_sent\\t%s\\n' \"$term_sent\"",
        "printf 'mep_cfd_mpi_smoke\\tkill_sent\\t%s\\n' \"$kill_sent\"",
    )) + "\n"


def _mpi_smoke_fields(text):
    fields = {}
    for line in (text or "").splitlines():
        prefix, key, value = (line.split("\t", 2) + ["", "", ""])[:3]
        if prefix == "mep_cfd_mpi_smoke" and key:
            fields[key] = value
    return fields


def _mpi_smoke_b64_text(value):
    try:
        return base64.b64decode(str(value or ""), validate=False).decode(
            "utf-8", errors="replace"
        )
    except (ValueError, UnicodeError):
        return ""


def _mpi_smoke_cleanup_from_fields(fields):
    status = str(fields.get("cleanup_status") or "UNVERIFIED").upper()
    if status not in ("CLEAN", "RESIDUAL", "UNVERIFIED"):
        status = "UNVERIFIED"
    return {
        "status": status,
        "term_sent": str(fields.get("term_sent") or "") == "1",
        "kill_sent": str(fields.get("kill_sent") or "") == "1",
        "remote_workdir_retained": str(fields.get("retained") or "1") != "0",
    }


def _run_mpi_smoke_trial(*, distro, ranks, timeout_seconds,
                         cleanup_grace_seconds, environment_overrides):
    """Run one rank count through the private WSL wrapper and collect evidence."""
    token = uuid.uuid4().hex
    workdir = _mpi_smoke_remote_dir(token)
    script = _mpi_smoke_wrapper_script(
        workdir, token, ranks=ranks, timeout_seconds=timeout_seconds,
        cleanup_grace_seconds=cleanup_grace_seconds,
        environment_overrides=environment_overrides,
    )
    started = time.monotonic()
    outer_timeout = timeout_seconds + cleanup_grace_seconds + 8
    try:
        proc = subprocess.run(
            _wsl_script_args(script, distro), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=outer_timeout,
        )
    except subprocess.TimeoutExpired:
        cleanup = {"status": "UNVERIFIED", "term_sent": False,
                   "kill_sent": False, "remote_workdir_retained": True}
        try:
            recovered = subprocess.run(
                _wsl_script_args(
                    _mpi_smoke_external_cleanup_script(
                        workdir, token, cleanup_grace_seconds
                    ), distro,
                ), capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=10,
            )
            cleanup = _mpi_smoke_cleanup_from_fields(
                _mpi_smoke_fields(recovered.stdout)
            )
        except (OSError, subprocess.TimeoutExpired):
            pass
        return {
            "ranks": ranks, "returncode": None,
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "timed_out": True, "hostname_line_count": 0,
            "hostname_lines": [], "stdout": "", "stderr": "",
            "cleanup": cleanup, "remote_workdir": workdir,
        }
    fields = _mpi_smoke_fields(proc.stdout)
    stdout = _mpi_smoke_b64_text(fields.get("stdout_b64"))
    stderr = _mpi_smoke_b64_text(fields.get("stderr_b64"))
    try:
        runner_returncode = int(fields.get("returncode"))
    except (TypeError, ValueError):
        runner_returncode = None
    lines = [line.strip() for line in stdout.splitlines() if line.strip()]
    return {
        "ranks": ranks,
        "returncode": runner_returncode,
        "wrapper_returncode": proc.returncode,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "timed_out": str(fields.get("timed_out") or "") == "1",
        "hostname_line_count": len(lines), "hostname_lines": lines,
        "stdout": stdout, "stderr": stderr,
        "cleanup": _mpi_smoke_cleanup_from_fields(fields),
        "remote_workdir": workdir,
    }


def _write_json_atomic(path, payload, *, prefix=".mep-cfd-"):
    target = os.path.abspath(os.fspath(path))
    os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        prefix=prefix, suffix=".tmp", dir=os.path.dirname(target) or ".", text=True,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temporary, target)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    return target


def _sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run_mpi_runtime_smoke(output_path, *, distro=None, timeout_seconds=10,
                          cleanup_grace_seconds=3, ranks=(2, 4),
                          environment_overrides=None):
    """Run bounded, token-owned MPI hostname smoke trials and write evidence.

    This is deliberately independent from ``run_case``: an Open MPI launcher
    that cannot create ranks must never reuse the normal solver ``Popen`` path.
    A timeout is not silently treated as a failed command; it becomes
    ``MPI_RANK_SPAWN_HANG`` and keeps parallel execution disabled.
    """
    import cfd_diagnostics

    rank_values = _normalise_mpi_smoke_ranks(ranks)
    timeout_seconds = _positive_int(timeout_seconds, default=10)
    cleanup_grace_seconds = _positive_int(cleanup_grace_seconds, default=3)
    overrides = _normalise_mpi_smoke_overrides(environment_overrides)
    identity = _mpi_smoke_identity(distro)
    active = _mpi_smoke_active_processes(distro)
    trials = []
    reason_code = ""
    status = "NOT_RUN"
    if identity.get("probe_error") or not identity.get("mpirun_path"):
        status = "BLOCKED"
        reason_code = "MPI_RUNTIME_UNAVAILABLE"
    elif active:
        status = "BLOCKED"
        reason_code = "MPI_PREEXISTING_PROCESS"
    else:
        for ranks_value in rank_values:
            trial = _run_mpi_smoke_trial(
                distro=distro, ranks=ranks_value, timeout_seconds=timeout_seconds,
                cleanup_grace_seconds=cleanup_grace_seconds,
                environment_overrides=overrides,
            )
            trials.append(dict(trial))
            one = cfd_diagnostics.evaluate_mpi_runtime_smoke(
                trials, required_ranks=(ranks_value,)
            )
            if one["status"] != "PASS":
                status = one["status"]
                reason_code = one["reason_code"]
                break
        else:
            decision = cfd_diagnostics.evaluate_mpi_runtime_smoke(
                trials, required_ranks=rank_values
            )
            status = decision["status"]
            reason_code = decision["reason_code"]
    payload = {
        "schema_version": 1,
        "contract": MPI_RUNTIME_SMOKE_CONTRACT,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "reason_code": reason_code,
        "identity": identity,
        "requested_ranks": list(rank_values),
        "timeout_seconds": timeout_seconds,
        "cleanup_grace_seconds": cleanup_grace_seconds,
        "environment_overrides": overrides,
        "preexisting_processes": active,
        "trials": trials,
    }
    saved_path = _write_json_atomic(output_path, payload, prefix=".mpi-runtime-smoke-")
    result = dict(payload)
    result["artifact_path"] = saved_path
    result["artifact_sha256"] = _sha256_file(saved_path)
    return result


def win_to_wsl(path, distro=None):
    """Windows 경로 → WSL 경로(wslpath). 실패 시 /mnt 규칙 폴백."""
    p = os.path.abspath(path)
    r = _wsl(f"wslpath -u {shlex.quote(p)}", distro=distro)
    if r.returncode == 0 and r.stdout.strip():
        return r.stdout.strip()
    drive = p[0].lower()
    return "/mnt/" + drive + p[2:].replace("\\", "/")


def _decode_wsl_output(raw):
    """Decode WSL output without losing Windows-side UTF-16 API errors.

    Commands launched inside Linux normally use UTF-8, while errors emitted by
    ``wsl.exe`` itself are UTF-16 on some Windows builds.  Keeping the raw bytes
    until this boundary makes stable diagnostics such as E_ACCESSDENIED visible
    to the capability gate.
    """
    if isinstance(raw, str):
        return raw.replace("\x00", "")
    if not raw:
        return ""
    payload = bytes(raw)
    if payload.startswith((b"\xff\xfe", b"\xfe\xff")):
        text = payload.decode("utf-16", errors="replace")
    elif b"\x00" in payload[:200]:
        # The API normally uses UTF-16 LE, but avoid guessing LE if an API or
        # test fixture provides a BE stream without a byte-order mark.
        even_nuls = payload[::2].count(0)
        odd_nuls = payload[1::2].count(0)
        encoding = "utf-16-be" if even_nuls > odd_nuls else "utf-16-le"
        text = payload.decode(encoding, errors="replace")
    else:
        text = payload.decode("utf-8", errors="replace")
    return text.replace("\x00", "").lstrip("\ufeff")


def _decode_wsl_listing(raw):
    """Decode `wsl --list --quiet`, which is UTF-16 on many Windows builds."""
    return _decode_wsl_output(raw)


def _list_wsl_distros():
    try:
        proc = subprocess.run(["wsl", "--list", "--quiet"], capture_output=True)
    except (OSError, FileNotFoundError):
        return []
    if proc.returncode != 0:
        return []
    text = _decode_wsl_listing(proc.stdout)
    return [line.strip().replace("\x00", "") for line in text.splitlines()
            if line.strip().replace("\x00", "")]


def _probe_openfoam(distro=None, requested_bashrc=None):
    """Return raw, line-oriented capability data from one WSL distribution."""
    command_checks = "".join(
        f'printf "cmd\\t{cmd}\\t"; command -v {shlex.quote(cmd)} 2>/dev/null || true; '
        'printf "\\n"; '
        for cmd in ALL_OPENFOAM_COMMANDS
    )
    if requested_bashrc:
        selection = (
            f"bashrc={shlex.quote(requested_bashrc)}; "
            '[ -f "$bashrc" ] || bashrc=""; '
        )
    else:
        # Prefer the validated current profile.  The distro `openfoam` package
        # can coexist with (or forward to) versioned installations, so never
        # rely on whichever unversioned shell happens to be first on PATH.
        selection = (
            'bashrc=""; '
            'for p in /usr/lib/openfoam/openfoam2606/etc/bashrc '
            '/opt/openfoam2606/etc/bashrc /usr/share/openfoam/etc/bashrc '
            '/usr/lib/openfoam/openfoam*/etc/bashrc /opt/openfoam*/etc/bashrc; do '
            '[ -f "$p" ] && { bashrc="$p"; break; }; done; '
        )
    script = (
        selection
        +
        'if [ -n "$bashrc" ]; then . "$bashrc" >/dev/null 2>&1 || true; fi; '
        "pkg=$(dpkg-query -W -f='${Version}' openfoam2606 2>/dev/null || "
        "dpkg-query -W -f='${Version}' openfoam 2>/dev/null || true); "
        'printf "distro\\t%s\\n" "${WSL_DISTRO_NAME:-}"; '
        'printf "bashrc\\t%s\\n" "$bashrc"; '
        'printf "version\\t%s\\n" "${WM_PROJECT_VERSION:-}"; '
        'printf "package\\t%s\\n" "$pkg"; '
        'printf "kernel\\t%s\\n" "$(uname -r 2>/dev/null || true)"; '
        'cpu=$(nproc 2>/dev/null || getconf _NPROCESSORS_ONLN 2>/dev/null || true); '
        'printf "cpu\t%s\n" "$cpu"; '
        'mpi_version=$(LC_ALL=C mpirun --version 2>/dev/null | head -n 1 || true); '
        'printf "mpi_version\t%s\n" "$mpi_version"; '
        'ompi_info_version=$(LC_ALL=C ompi_info --version 2>/dev/null | head -n 1 || true); '
        'printf "ompi_info_version\t%s\n" "$ompi_info_version"; '
        + command_checks
    )
    try:
        proc = _wsl(script, distro=distro)
    except (OSError, FileNotFoundError) as exc:
        return {"wsl_available": False, "returncode": None, "error": str(exc),
                "distro": distro or "", "bashrc": "", "version": "",
                "package_version": "", "kernel": "", "commands": {},
                "effective_cpu_count": None, "effective_cpu_source": "WSL nproc",
                "mpi_version": "", "ompi_info_version": ""}

    error = (proc.stderr or "").strip()
    if proc.returncode and not error:
        error = (proc.stdout or "").strip()
    data = {"wsl_available": True, "returncode": proc.returncode,
            "error": error,
            "distro": distro or "", "bashrc": "", "version": "",
            "package_version": "", "kernel": "", "commands": {},
            "effective_cpu_count": None, "effective_cpu_source": "WSL nproc",
            "mpi_version": "", "ompi_info_version": ""}
    for line in (proc.stdout or "").splitlines():
        parts = line.split("\t", 2)
        if len(parts) < 2:
            continue
        key, value = parts[0], parts[1].strip()
        if key == "cmd" and len(parts) == 3:
            data["commands"][value] = parts[2].strip()
        elif key == "distro":
            data["distro"] = value or data["distro"]
        elif key == "bashrc":
            data["bashrc"] = value
        elif key == "version":
            data["version"] = value
        elif key == "package":
            data["package_version"] = value
        elif key == "kernel":
            data["kernel"] = value
        elif key == "cpu":
            try:
                cpu = int(value)
                data["effective_cpu_count"] = cpu if cpu > 0 else None
            except ValueError:
                pass
        elif key == "mpi_version":
            data["mpi_version"] = value
        elif key == "ompi_info_version":
            data["ompi_info_version"] = value
    return data


def _capability_result(probe, available_distros, selection):
    commands = {cmd: (probe.get("commands") or {}).get(cmd, "")
                for cmd in ALL_OPENFOAM_COMMANDS}
    missing_runtime = [cmd for cmd in RUNTIME_COMMANDS if not commands[cmd]]
    missing_body = [cmd for cmd in BODY_FITTED_COMMANDS if not commands[cmd]]
    missing_parallel = [cmd for cmd in MPI_COMMANDS if not commands[cmd]]
    try:
        effective_cpu_count = int(probe.get("effective_cpu_count"))
        if effective_cpu_count <= 0:
            effective_cpu_count = None
    except (TypeError, ValueError):
        effective_cpu_count = None
    version = probe.get("version") or ""
    package_version = probe.get("package_version") or ""
    shown_version = version or package_version
    if re.search(r"(?:^|v)2606(?:$|[.\-])", shown_version, re.I):
        profile = "openfoam-v2606"
    elif re.search(r"(?:^|v)1912(?:$|[.\-])", shown_version, re.I):
        profile = "openfoam-v1912-legacy"
    else:
        profile = ""
    compatible = bool(profile)
    has_distro = bool(probe.get("distro"))
    has_bashrc = bool(probe.get("bashrc"))

    reason_code = ""
    if not probe.get("wsl_available"):
        status = "wsl_missing"
        summary = "Windows WSL을 찾지 못했습니다."
        fix = "프로젝트 폴더의 `install_openfoam2606.bat`를 더블클릭하세요."
    elif probe.get("returncode") not in (0, None):
        detail = _decode_wsl_output(probe.get("error") or "")
        if "E_ACCESSDENIED" in detail.upper() or "액세스가 거부" in detail:
            status = "wsl_access_denied"
            reason_code = "WSL_ACCESS_DENIED"
            summary = "Windows가 WSL 접근을 거부했습니다. OpenFOAM 계산은 시작하지 않았습니다."
            fix = (
                "1) Windows에 다시 로그인하거나 PC를 재시작한 뒤 ‘환경 다시 검사’를 누르세요. "
                "2) 계속되면 IT 담당자에게 진단 코드 WSL_ACCESS_DENIED와 `wsl --status` 결과를 "
                "전달하여 WSL 서비스·배포판 접근 권한을 확인 요청하세요. "
                "이 프로그램은 WSL을 자동으로 재시작하거나 설치하지 않습니다."
            )
        else:
            status = "wsl_probe_failed"
            summary = "WSL 실행 환경 상태를 읽지 못했습니다."
            fix = "WSL 서비스 상태를 확인한 뒤 환경 검사를 다시 실행하세요."
    elif not has_distro:
        status = "distribution_missing"
        summary = "설치된 WSL Linux 배포판을 찾지 못했습니다."
        fix = "프로젝트 폴더의 `install_openfoam2606.bat`를 더블클릭하세요."
    elif not has_bashrc:
        status = "openfoam_missing"
        summary = f"{probe.get('distro')}에 OpenFOAM 환경 파일이 없습니다."
        fix = "프로젝트 폴더의 `install_openfoam2606.bat`를 더블클릭하세요."
    elif missing_runtime:
        status = "runtime_tools_missing"
        summary = "OpenFOAM 실행 도구 일부를 찾지 못했습니다: " + ", ".join(missing_runtime)
        fix = "OpenFOAM 패키지를 다시 설치한 뒤 환경 검사를 다시 실행하세요."
    elif not compatible:
        status = "unsupported_version"
        shown = version or package_version or "알 수 없음"
        summary = f"OpenFOAM {shown}은 현재 v1912 케이스와 호환 확인되지 않았습니다."
        fix = "OpenFOAM v2606 런타임을 설치하거나 검증된 v1912 legacy 프로필을 선택하세요."
    else:
        status = "ready"
        summary = "OpenFOAM 계산 환경이 준비되었습니다."
        fix = ""

    ok = status == "ready"
    mpi_tools_available = not missing_parallel
    parallel_ready = bool(
        ok and mpi_tools_available and effective_cpu_count is not None
        and effective_cpu_count >= 2
    )
    return {
        "schema_version": 1,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "ok": ok,
        "status": status,
        "reason_code": reason_code,
        "summary": summary,
        "fix": fix,
        "selection": selection,
        "distro": probe.get("distro") or "",
        "kernel": probe.get("kernel") or "",
        "available_distros": list(available_distros),
        "bashrc": probe.get("bashrc") or "",
        "version": version,
        "package_version": package_version,
        "compatible_profile": profile,
        "thermal_detailed_ready": ok and profile == "openfoam-v2606",
        "commands": commands,
        "missing_runtime_commands": missing_runtime,
        "missing_body_fitted_commands": missing_body,
        "missing_parallel_commands": missing_parallel,
        "mpi_tools_available": mpi_tools_available,
        "mpi_version": probe.get("mpi_version") or "",
        "ompi_info_version": probe.get("ompi_info_version") or "",
        "effective_cpu_count": effective_cpu_count,
        "effective_cpu_source": probe.get("effective_cpu_source") or "WSL nproc",
        "parallel_ready": parallel_ready,
        "body_fitted_ready": ok and not missing_body,
        "error_detail": _decode_wsl_output(probe.get("error") or "")[:1000],
    }


def diagnose_openfoam(distro=None):
    """Diagnose WSL/OpenFOAM and select a compatible distribution safely."""
    configured = distro or os.environ.get(WSL_DISTRO_ENV, "").strip() or None
    configured_bashrc = os.environ.get(OPENFOAM_BASHRC_ENV, "").strip() or None
    available = _list_wsl_distros()
    if configured:
        probe = (_probe_openfoam(configured, configured_bashrc)
                 if configured_bashrc else _probe_openfoam(configured))
        return _capability_result(probe, available, "configured")

    default_probe = (_probe_openfoam(None, configured_bashrc)
                     if configured_bashrc else _probe_openfoam())
    default_result = _capability_result(default_probe, available, "default")
    if default_result["ok"]:
        return default_result

    tried = {default_result.get("distro")}
    fallback = default_result
    for candidate in available:
        if candidate in tried:
            continue
        result = _capability_result(_probe_openfoam(candidate), available, "automatic")
        if result["ok"]:
            return result
        if not fallback.get("bashrc") and result.get("bashrc"):
            fallback = result
    return fallback


def _remote_run_dir(case, name):
    """Return a deterministic, shell-safe WSL work directory.

    Case names intentionally allow Korean and spaces in the UI.  Using that
    display name directly in a shell command broke copy/run/recovery for exactly
    the people this application targets.  An ASCII slug plus a path hash keeps
    the display name untouched while making the internal directory unambiguous.
    """
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(name)).strip("._-")[:48] or "case"
    digest = hashlib.sha256(os.path.normcase(case).encode("utf-8")).hexdigest()[:10]
    return f"~/cfd_runs/{slug}_{digest}"


def _result_relpaths(root, *, keep_mesh=False):
    """Return result-only paths, leaving case inputs and time 0 untouched."""
    relpaths = []
    for name in os.listdir(root):
        path = os.path.join(root, name)
        if ((os.path.isdir(path) and _is_float_dir(name))
                or name == "postProcessing"
                or name == "VTK"
                or name == ".mep_cfd_last_recovery"
                or (name.startswith("log.") and os.path.isfile(path))):
            relpaths.append(name)
    if keep_mesh and os.path.exists(os.path.join(root, "constant", "polyMesh")):
        relpaths.append(os.path.join("constant", "polyMesh"))
    return relpaths


def _latest_local_time_name(case):
    values = []
    for name in os.listdir(case):
        path = os.path.join(case, name)
        if not os.path.isdir(path):
            continue
        try:
            value = float(name)
        except ValueError:
            continue
        if value > 0:
            values.append((value, name))
    return max(values, default=(None, None))[1]


def _restart_fingerprint(case, restart_name):
    """Fingerprint the exact local restart inputs mirrored to WSL."""
    case = os.path.abspath(case)
    required_static = ("Allrun", "system/controlDict")
    if any(not os.path.isfile(os.path.join(case, item))
           for item in required_static):
        return None
    restart_fields = [
        field for field in ("U", "T", "p", "p_rgh", "k", "omega")
        if os.path.isfile(os.path.join(case, str(restart_name), field))
    ]
    if len(restart_fields) < 2:
        return None
    candidates = [
        "Allrun", "mesh_manifest.json", "thermal_input.json",
        "thermal_restart_input.json", "system/controlDict",
        "system/fvSchemes", "system/fvSolution", "constant/fvOptions",
    ]
    for field in ("U", "T", "p", "p_rgh", "k", "omega", "alphat", "nut"):
        candidates.append(os.path.join(str(restart_name), field))
    digest = hashlib.sha256()
    digest.update(("restart=" + str(restart_name) + "\n").encode("utf-8"))
    for relative in candidates:
        path = os.path.join(case, relative)
        if not os.path.isfile(path):
            continue
        digest.update(relative.replace("\\", "/").encode("utf-8") + b"\0")
        if relative == "thermal_restart_input.json":
            # Preparing the same continuation again refreshes only this audit
            # timestamp. It must not invalidate an otherwise identical remote
            # checkpoint, while all physical/numerical settings remain hashed.
            try:
                with open(path, encoding="utf-8") as handle:
                    restart_contract = json.load(handle)
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                return None
            restart_contract.pop("created_at", None)
            digest.update(json.dumps(
                restart_contract, ensure_ascii=False, sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8"))
            continue
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    return digest.hexdigest()


def _remote_checkpoint_status(run_dir, fingerprint, restart_base, distro=None):
    """Return active/recoverable state for a verified unfinished WSL case."""
    destination = str(run_dir).rstrip("/")
    if not re.fullmatch(r"~/cfd_runs/[A-Za-z0-9_.-]+", destination):
        return {"status": "missing"}
    if not re.fullmatch(r"[0-9a-f]{64}", str(fingerprint or "")):
        return {"status": "missing"}
    command = (
        f"cd {destination} 2>/dev/null || exit 3; "
        "here=$(readlink -f .); "
        "for p in $(pgrep -f '[b]uoyantBoussinesqPimpleFoam' 2>/dev/null); do "
        "[ \"$(readlink -f /proc/$p/cwd 2>/dev/null)\" = \"$here\" ] "
        "&& echo ACTIVE && exit 0; done; "
        "latest=$(for d in [0-9]*; do [ -d \"$d\" ] && printf '%s\\n' \"$d\"; "
        "done | sort -g | tail -1); "
        "marker=$(cat .mep_cfd_resume_fingerprint 2>/dev/null || true); "
        f"if [ \"$marker\" != {shlex.quote(fingerprint)} ]; then "
        "awk -v latest=\"$latest\" -v base="
        f"{float(restart_base):.17g} 'BEGIN{{exit !(latest+0>base+0)}}' "
        "&& printf 'CONFLICT %s\\n' \"$latest\" || echo MISSING; exit 0; fi; "
        "awk -v latest=\"$latest\" -v base="
        f"{float(restart_base):.17g} 'BEGIN{{exit !(latest+0>base+0)}}' "
        "&& printf 'RECOVERABLE %s\\n' \"$latest\" || echo MISSING"
    )
    try:
        # This probe protects an unfinished remote checkpoint from destructive
        # restaging, so a wedged Windows WSL client must fail closed instead of
        # being treated as a missing directory.
        result = _wsl(command, distro=distro, timeout=20)
    except subprocess.TimeoutExpired:
        return {
            "status": "unavailable",
            "error": "WSL 체크포인트 상태 확인이 20초 안에 끝나지 않았습니다.",
        }
    output = (result.stdout or "").strip().splitlines()
    marker = output[-1].strip() if output else ""
    if marker == "ACTIVE":
        return {"status": "active"}
    conflict = re.fullmatch(r"CONFLICT\s+([0-9]+(?:\.[0-9]+)?)", marker)
    if result.returncode == 0 and conflict:
        return {
            "status": "conflict",
            "latest_time": float(conflict.group(1)),
            "error": "입력 지문이 다른 WSL 최신 체크포인트가 있어 자동 재복사를 중단했습니다.",
        }
    match = re.fullmatch(r"RECOVERABLE\s+([0-9]+(?:\.[0-9]+)?)", marker)
    if result.returncode == 0 and match:
        return {"status": "recoverable", "latest_time": float(match.group(1))}
    return {"status": "missing"}


def _stage_case_command(wsl_case, run_dir, restart_name=None,
                        resume_fingerprint=None):
    """Build a bounded WSL staging command without copying stale result times.

    Continuation cases retain up to 28 local time snapshots for time-window
    statistics.  Copying all of them across the Windows/WSL boundary and then
    deleting 27 made every 60 s CFD chunk pay unnecessary fixed I/O cost.
    """
    source = shlex.quote(str(wsl_case).rstrip("/"))
    destination = str(run_dir).rstrip("/")
    if not re.fullmatch(r"~/cfd_runs/[A-Za-z0-9_.-]+", destination):
        raise ValueError("WSL 작업 경로가 안전한 프로젝트 내부 형식이 아닙니다.")
    command = (
        f"mkdir -p ~/cfd_runs && rm -rf {destination} && mkdir -p {destination} && "
        f"find {source} -regextype posix-extended -mindepth 1 -maxdepth 1 "
        f"! -regex '.*/[0-9]+(\\.[0-9]+)?' "
        f"! -name postProcessing ! -name VTK ! -name 'log.*' "
        f"-exec cp -a -t {destination} -- {{}} + && "
        f"test -d {source}/0 && cp -a {source}/0 {destination}/"
    )
    if restart_name:
        restart = shlex.quote(str(restart_name))
        command += (
            f" && test -d {source}/{restart}"
            f" && cp -a {source}/{restart} {destination}/"
        )
        if resume_fingerprint is not None:
            if not re.fullmatch(r"[0-9a-f]{64}", str(resume_fingerprint)):
                raise ValueError("WSL 재개 입력 지문 형식이 올바르지 않습니다.")
            command += (
                " && printf '%s\\n' "
                f"{shlex.quote(str(resume_fingerprint))} > "
                f"{destination}/.mep_cfd_resume_fingerprint"
            )
    return command


def _select_recovery_times(names, limit=7):
    """Keep audit anchors plus a dense recent tail for time-window statistics."""
    numeric = sorted(
        (str(name).strip() for name in names if _is_float_dir(str(name).strip())),
        key=lambda name: (float(name), name),
    )
    limit = max(3, int(limit or 0))
    if len(numeric) <= limit:
        return numeric
    selected = {
        numeric[0],
        numeric[(len(numeric) - 1) // 2],
        numeric[-1],
    }
    # Fill the remaining budget from newest backwards.  This preserves a
    # point before the late statistical window and enough consecutive recent
    # writes to integrate drift, instead of relying on a lucky final chunk.
    for name in reversed(numeric):
        selected.add(name)
        if len(selected) >= limit:
            break
    return sorted(selected, key=lambda name: (float(name), name))


def _move_rel(src_root, dst_root, relpath):
    src = os.path.join(src_root, relpath)
    dst = os.path.join(dst_root, relpath)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    os.replace(src, dst)


def _remove_path(path):
    if os.path.isdir(path) and not os.path.islink(path):
        shutil.rmtree(path)
    elif os.path.exists(path):
        os.remove(path)


def _publish_recovery(stage, case, *, keep_mesh=False, preserve_time_dirs=0):
    """Publish a verified recovery set without mixing it with stale results.

    Existing result artifacts are moved to a sibling backup first.  If any
    replacement fails they are restored, so a copy/lock error cannot leave a
    half-new case or make a prior result disappear.
    """
    new_rel = _result_relpaths(stage, keep_mesh=keep_mesh)
    old_rel = _result_relpaths(case, keep_mesh=keep_mesh)
    retained_old_times = []
    if preserve_time_dirs > 0:
        old_times = [rel for rel in old_rel if _is_float_dir(rel)]
        new_times = [rel for rel in new_rel if _is_float_dir(rel)]
        keep = set(sorted(
            set(old_times + new_times), key=lambda value: (float(value), value)
        )[-int(preserve_time_dirs):])
        retained_old_times = [rel for rel in old_times if rel in keep and rel not in new_times]
    parent = os.path.dirname(case)
    backup = tempfile.mkdtemp(prefix=".mep-cfd-previous-", dir=parent)
    published = []
    try:
        for rel in old_rel:
            _move_rel(case, backup, rel)
        for rel in new_rel:
            _move_rel(stage, case, rel)
            published.append(rel)
        # A thermal continuation starts from the latest saved state. Preserve
        # a bounded sparse history so time-window statistics can be calculated,
        # while the backup still provides an atomic rollback source.
        for rel in retained_old_times:
            source = os.path.join(backup, rel)
            target = os.path.join(case, rel)
            shutil.copytree(source, target)
            published.append(rel)
    except BaseException:
        for rel in reversed(published):
            _remove_path(os.path.join(case, rel))
        for rel in old_rel:
            saved = os.path.join(backup, rel)
            if os.path.exists(saved):
                current = os.path.join(case, rel)
                if os.path.exists(current):
                    _remove_path(current)
                _move_rel(backup, case, rel)
        shutil.rmtree(backup, ignore_errors=True)
        raise
    else:
        shutil.rmtree(backup, ignore_errors=True)


def check_openfoam():
    """Compatibility wrapper for callers that only need a Boolean."""
    return bool(diagnose_openfoam().get("ok"))


def _publish_log_stage(stage, case):
    """Atomically replace only logs present in a recovery stage."""
    published = []
    for source in glob.glob(os.path.join(stage, "log.*")):
        name = os.path.basename(source)
        temporary = os.path.join(case, "." + name + ".new." + uuid.uuid4().hex)
        shutil.copy2(source, temporary)
        os.replace(temporary, os.path.join(case, name))
        published.append(name)
    return published


def _recover_failure_logs(run_dir, case, distro=None):
    """Best-effort recovery of fresh WSL logs without touching old result times."""
    stage = tempfile.mkdtemp(prefix=".mep-cfd-failure-logs-", dir=os.path.dirname(case))
    try:
        stage_wsl = win_to_wsl(stage, distro=distro)
        recovered = _wsl(
            f"cd {run_dir} 2>/dev/null || exit 1; "
            f"for f in log.*; do [ -f \"$f\" ] && cp \"$f\" {shlex.quote(stage_wsl)}/; done",
            distro=distro,
        )
        return _publish_log_stage(stage, case) if recovered.returncode == 0 else []
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def _sha256_file(path):
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
    except OSError:
        return ""
    return digest.hexdigest()


def _case_input_sha256(case):
    """Hash the two generated inputs that define a legacy screening run."""
    digest = hashlib.sha256()
    found = False
    for relative in ("cfd_case_meta.json", "Allrun", "system/controlDict"):
        path = os.path.join(case, relative)
        try:
            with open(path, "rb") as handle:
                digest.update(relative.encode("utf-8"))
                digest.update(b"\0")
                for block in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(block)
                found = True
        except OSError:
            continue
    return digest.hexdigest() if found else ""


def _latest_solver_log(case):
    candidates = glob.glob(os.path.join(case, "log.*Foam"))
    try:
        return max(candidates, key=os.path.getmtime) if candidates else ""
    except OSError:
        return ""


def runtime_baseline(case, runner_wall_seconds):
    """Extract a serial-run performance record after verified recovery."""
    from cfd_diagnostics import parse_gnu_time_v, parse_openfoam_timing

    log_path = _latest_solver_log(case)
    try:
        with open(log_path, encoding="utf-8", errors="replace") as handle:
            solver_text = handle.read()
    except OSError:
        solver_text = ""
    try:
        with open(os.path.join(case, "log.runner"),
                  encoding="utf-8", errors="replace") as handle:
            runner_text = handle.read()
    except OSError:
        runner_text = ""
    timing = parse_openfoam_timing(solver_text)
    memory = parse_gnu_time_v(runner_text)
    complete = all(value is not None for value in (
        runner_wall_seconds,
        timing["clock_seconds"],
        memory["peak_rss_kib"],
    ))
    return {
        "status": "PASS" if complete else "PARTIAL",
        "runner_wall_seconds": round(float(runner_wall_seconds), 6)
        if runner_wall_seconds is not None else None,
        "solver_execution_seconds": timing["execution_seconds"],
        "solver_clock_seconds": timing["clock_seconds"],
        "peak_rss_kib": memory["peak_rss_kib"],
        "case_input_sha256": _case_input_sha256(case),
        "solver_log_sha256": _sha256_file(log_path),
    }


def _aggregate_runtime_baselines(rounds):
    """Sum sequential continuation rounds into one honest runner baseline."""
    rows = [dict(row) for row in (rounds or []) if isinstance(row, dict)]
    if not rows:
        return None

    def total(key):
        values = [row.get(key) for row in rows]
        if any(value is None for value in values):
            return None
        return round(sum(float(value) for value in values), 6)

    peaks = [row.get("peak_rss_kib") for row in rows if row.get("peak_rss_kib") is not None]
    final = dict(rows[-1])
    final.update({
        "status": "PASS" if all(row.get("status") == "PASS" for row in rows) else "PARTIAL",
        "runner_wall_seconds": total("runner_wall_seconds"),
        "solver_execution_seconds": total("solver_execution_seconds"),
        "solver_clock_seconds": total("solver_clock_seconds"),
        "peak_rss_kib": max(peaks) if peaks else None,
        "round_count": len(rows),
    })
    return final


def record_runtime_capability(path, baseline=None, *, mpi_smoke=None):
    """Probe the current WSL runtime and atomically publish runtime evidence."""
    import cfd_capabilities

    payload = cfd_capabilities.build_runtime_capability(
        diagnose_openfoam(), baseline=baseline, mpi_smoke=mpi_smoke
    )
    saved_path = cfd_capabilities.write_runtime_capability(path, payload)
    return {"ok": True, "path": saved_path, "manifest": payload}


def run_case(case_dir, name=None, keep_mesh=False, progress_cb=None,
             restart_from_latest=False):
    """케이스 실행: WSL 복사 → Allrun(진행 라인 스트리밍) → 결과 회수.
    progress_cb(line:str): 진행 라인 콜백(None이면 print).
    restart_from_latest=True이면 검증된 미회수 WSL time을 재사용하거나,
    Windows case의 마지막 양수 time 하나만 WSL에 보존한다.
    반환: {"ok": bool, "error": str|None, "case": 절대경로}"""
    run_started = time.monotonic()
    cb = progress_cb or (lambda s: print(s, flush=True))
    case = os.path.abspath(case_dir)
    if not os.path.isdir(case):
        return {"ok": False, "error": f"케이스 폴더 없음: {case}", "case": case}
    name = name or os.path.basename(case.rstrip("/\\"))
    capabilities = diagnose_openfoam()
    if not capabilities.get("ok"):
        detail = capabilities.get("summary") or "OpenFOAM 환경을 사용할 수 없습니다."
        fix = capabilities.get("fix") or "스튜디오의 환경 진단을 확인하세요."
        return {"ok": False, "case": case, "error": f"{detail}\n{fix}"}
    distro = capabilities.get("distro") or None
    bashrc = capabilities.get("bashrc") or OF_BASHRC
    wsl_case = win_to_wsl(case, distro=distro)
    run_dir = _remote_run_dir(case, name)
    restart_name = _latest_local_time_name(case) if restart_from_latest else None
    if restart_from_latest and restart_name is None:
        return {"ok": False, "case": case,
                "error": "재시작할 정상상태 결과 time 폴더가 없습니다."}
    restart_base = float(restart_name or 0.0)
    resume_fingerprint = (
        _restart_fingerprint(case, restart_name) if restart_from_latest else None
    )
    if restart_from_latest and resume_fingerprint is None:
        return {
            "ok": False,
            "case": case,
            "code": "WSL_RESTART_FINGERPRINT_UNAVAILABLE",
            "error": (
                "재시작 입력 지문을 만들지 못해 원격 체크포인트를 보호하기 위해 "
                "자동 재복사를 중단했습니다."
            ),
        }
    remote = (
        _remote_checkpoint_status(
            run_dir, resume_fingerprint, restart_base, distro=distro
        ) if restart_from_latest else {"status": "missing"}
    )
    if remote["status"] == "active":
        return {
            "ok": False, "case": case, "code": "WSL_REMOTE_SOLVER_ACTIVE",
            "error": "같은 WSL 작업공간에서 OpenFOAM이 아직 실행 중입니다. 기존 계산이 끝난 뒤 다시 시도하세요.",
        }
    if remote["status"] == "unavailable":
        return {
            "ok": False,
            "case": case,
            "code": "WSL_REMOTE_CHECKPOINT_PROBE_FAILED",
            "error": (
                remote.get("error")
                or "WSL 체크포인트 상태를 확인하지 못해 안전을 위해 재복사를 중단했습니다."
            ),
        }
    if remote["status"] == "conflict":
        latest = remote.get("latest_time")
        latest_note = f" ({latest:.6g}s)" if isinstance(latest, (int, float)) else ""
        return {
            "ok": False,
            "case": case,
            "code": "WSL_REMOTE_CHECKPOINT_CONFLICT",
            "error": (
                (remote.get("error") or "입력이 다른 WSL 체크포인트가 있습니다.")
                + latest_note
            ),
        }
    if remote["status"] == "recoverable":
        cb(
            f"[1/3] 미회수 WSL 체크포인트 재사용: {run_dir} "
            f"({remote['latest_time']:.6g}s)"
        )
    else:
        cb(f"[1/3] WSL 홈으로 복사: {run_dir}")
        copied = _wsl(
            _stage_case_command(wsl_case, run_dir, restart_name, resume_fingerprint),
            distro=distro,
        )
        if copied.returncode != 0:
            detail = (copied.stderr or copied.stdout or "").strip()
            return {"ok": False, "case": case,
                    "error": f"WSL 작업 폴더 복사 실패: {detail or 'unknown error'}"}

    cb("[2/3] Allrun 실행(포그라운드)...")
    # 진행에 필요한 라인만 통과(전체 solver 출력은 WSL 쪽 log.* 에 tee 됨):
    # 단계 마커(===), 모든 Time 라인(스튜디오 진행바용), 실패/메시 판정.
    run_cmd = (f"set -o pipefail; source {shlex.quote(bashrc)} 2>/dev/null || exit 20; "
               f"cd {run_dir} || exit 21; chmod +x Allrun 2>/dev/null || exit 22; "
               f"(if [ -x /usr/bin/time ]; then "
               f"LC_ALL=C /usr/bin/time -v -o log.runner ./Allrun; "
               f"else ./Allrun; fi) 2>&1 | "
               f"awk '/^Time = /{{print; next}} /^===/{{print; next}} "
               f"/FATAL|FAILED|Mesh OK|\\*\\*\\*|SIMPLE solution converged/{{print; next}}'")
    proc = subprocess.Popen(_wsl_args(run_cmd, distro),
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="replace")
    for line in proc.stdout:
        cb(line.rstrip("\n"))
    proc.wait()
    if proc.stdout:
        proc.stdout.close()
    run_rc = proc.returncode
    time_listing = _wsl(
        f"cd {run_dir} 2>/dev/null || exit 1; "
        "for d in [0-9]*; do [ -d \"$d\" ] && printf '%s\\n' \"$d\"; done",
        distro=distro,
    )
    fresh_times = []
    for value in (time_listing.stdout or "").splitlines():
        try:
            numeric = float(value.strip())
        except ValueError:
            continue
        if numeric > restart_base:
            fresh_times.append(numeric)
    has_fresh_time = bool(fresh_times)

    if run_rc != 0:
        failure_logs = _recover_failure_logs(run_dir, case, distro=distro)
        recovered_note = (" 회수 로그: " + ", ".join(failure_logs)) if failure_logs else ""
        return {"ok": False,
                "error": f"OpenFOAM 실행 실패(종료코드 {run_rc}).{recovered_note}",
                "case": case}
    if not has_fresh_time:
        recovered_logs = _recover_failure_logs(run_dir, case, distro=distro)
        recovered_note = (" 회수 로그: " + ", ".join(recovered_logs)) if recovered_logs else ""
        return {"ok": False,
                "error": "OpenFOAM이 정상 종료를 보고했지만 새 결과 time 폴더가 없습니다 — "
                         "이전 결과를 성공으로 간주하지 않습니다." + recovered_note,
                "case": case}

    cb(f"[3/3] 결과 회수 -> {case}")
    # 로그·postProcessing + time 스냅샷 최대 7개. 실행 전체의 처음/중간 기준점과
    # 최신 연속 쓰기들을 함께 보존해 마지막 정상성 시간창을 실제 데이터로 덮는다.
    listed_times = (time_listing.stdout or "").splitlines()
    if restart_from_latest:
        listed_times = [name for name in listed_times
                        if _is_float_dir(name) and float(name) > restart_base]
    selected_times = _select_recovery_times(listed_times)
    if time_listing.returncode != 0 or not selected_times:
        return {"ok": False,
                "error": "결과 time 디렉터리 목록을 확인하지 못했습니다",
                "case": case}
    selected_time_args = " ".join(shlex.quote(name) for name in selected_times)
    recovery_token = uuid.uuid4().hex
    recovery_stage = tempfile.mkdtemp(prefix=".mep-cfd-recovery-",
                                      dir=os.path.dirname(case))
    try:
        stage_wsl = win_to_wsl(recovery_stage, distro=distro)
        stage_wsl_q = shlex.quote(stage_wsl)
        marker_local = os.path.join(recovery_stage, ".mep_cfd_last_recovery")
        marker_wsl_q = shlex.quote(stage_wsl.rstrip("/") + "/.mep_cfd_last_recovery")
        recover = (f"set -e; cd {run_dir}; "
                   f"cp log.* {stage_wsl_q}/; "
                   f"if [ -d postProcessing ]; then cp -r postProcessing {stage_wsl_q}/; fi; "
                   f"if [ -d VTK ]; then cp -r VTK {stage_wsl_q}/; fi; "
                   f"copied_time=0; "
                   f"for T in {selected_time_args}; "
                   f"do cp -r \"$T\" {stage_wsl_q}/; copied_time=1; done; "
                   f"[ \"$copied_time\" -eq 1 ]; "
                   + (f"mkdir -p {stage_wsl_q}/constant; "
                      f"cp -r constant/polyMesh {stage_wsl_q}/constant/; " if keep_mesh else "")
                   + f"printf '%s' {shlex.quote(recovery_token)} > {marker_wsl_q}; echo done")
        recovered = _wsl(recover, distro=distro)
        if recovered.returncode != 0:
            detail = (recovered.stderr or recovered.stdout or "").strip()
            return {"ok": False,
                    "error": f"OpenFOAM 결과를 Windows 폴더로 회수하지 못했습니다: {detail or 'copy failed'}",
                    "case": case}
        try:
            with open(marker_local, encoding="ascii") as f:
                marker_ok = f.read() == recovery_token
        except OSError:
            marker_ok = False
        if not marker_ok:
            return {"ok": False,
                    "error": "결과 회수 확인 표시가 없습니다 — 이전 로컬 결과를 성공으로 간주하지 않습니다",
                    "case": case}

        got_log = bool(glob.glob(os.path.join(recovery_stage, "log.*Foam")))
        got_time = any(_is_float_dir(d) for d in os.listdir(recovery_stage)
                       if os.path.isdir(os.path.join(recovery_stage, d)))
        if not got_log:
            return {"ok": False, "error": "solver 로그 미회수 — 실행 실패(위 출력 확인)", "case": case}
        if not got_time:
            return {"ok": False, "error": "결과 time 디렉토리 없음 — solver 조기 종료(로그 확인)", "case": case}
        try:
            _publish_recovery(
                recovery_stage, case, keep_mesh=keep_mesh,
                preserve_time_dirs=28 if restart_from_latest else 0,
            )
        except OSError as exc:
            return {"ok": False,
                    "error": f"검증된 결과를 케이스에 반영하지 못했습니다(기존 결과 유지): {exc}",
                    "case": case}
        opening_verification = None
        try:
            with open(os.path.join(case, "cfd_case_meta.json"), encoding="utf-8") as stream:
                case_meta = json.load(stream)
            if case_meta.get("patches"):
                import cfd_export
                opening_verification = cfd_export.verify_opening_boundary_areas(case)
        except Exception as exc:
            # The solve itself is already verified.  Do not hide a valid CFD
            # result if optional post-run opening evidence cannot be written.
            opening_verification = {"status": "NOT_AVAILABLE",
                                    "reason": type(exc).__name__}
        return {
            "ok": True,
            "error": None,
            "case": case,
            "opening_verification": opening_verification,
            "runtime_baseline": runtime_baseline(
                case, time.monotonic() - run_started
            ),
        }
    finally:
        shutil.rmtree(recovery_stage, ignore_errors=True)


def _is_float_dir(name):
    try:
        return float(name) > 0
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# 폐합 통과까지 자동 연장 실행
# ---------------------------------------------------------------------------
# 고정 endTime 으로 한 번만 돌리면 "계산은 끝났는데 물리적으로는 미완"인 결과가
# 그대로 리포트에 실린다(2026-08 사고: 1000회에서 멈춰 폐합 158%).
# 반복이 얼마나 더 필요한지는 사전에 알 수 없지만, 실행 후 잔차 감쇠율에서
# 측정할 수 있다. 그래서 "돌린다 → 재고 → 부족하면 이어서 더" 를 자동화한다.

AUTO_EXTEND_MAX_ROUNDS = 4          # 라운드 상한(무한 연장 방지)
AUTO_EXTEND_STEP_CAP = 8000         # 1회 연장 상한 iteration
AUTO_EXTEND_TOTAL_CAP = 30000       # 총 iteration 상한


def _control_dict_end_time(case):
    path = os.path.join(case, "system", "controlDict")
    with open(path, encoding="utf-8") as f:
        text = f.read()
    m = re.search(r"^\s*endTime\s+([0-9.eE+-]+)\s*;", text, re.M)
    return (float(m.group(1)) if m else None), text, path


def _set_control_dict_end_time(case, value):
    current, text, path = _control_dict_end_time(case)
    text = re.sub(r"^(\s*endTime\s+)[0-9.eE+-]+(\s*;)",
                  lambda mo: f"{mo.group(1)}{value:g}{mo.group(2)}", text, count=1, flags=re.M)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return current


def _auto_extend_convergence(parsed, energy):
    """Evaluate only the evidence needed to decide whether to stop extending.

    This is intentionally separate from result_trust.v1. The latter also
    requires final display fields (T/U) before a dashboard result is shown,
    while automatic continuation has to make a bounded solver-control decision
    from residual, continuity, and energy-closure evidence alone.
    """
    import cfd_result_gate

    parsed = parsed or {}
    energy = energy or {}
    blockers = []
    if parsed.get("crashed"):
        blockers.append("solver_crash")

    continuity = parsed.get("continuity_global") or []
    try:
        continuity_ok = bool(continuity) and abs(float(continuity[-1][1])) < 1e-3
    except (IndexError, TypeError, ValueError):
        continuity_ok = False
    if not continuity_ok:
        blockers.append("continuity")

    residual_ok = True
    for field in cfd_result_gate.LEGACY_SCREENING_RESIDUALS:
        values = [value for value in (parsed.get("residuals", {}).get(field) or [])
                  if value is not None]
        if not values or values[-1] > cfd_result_gate.RESIDUAL_LIMITS[field]:
            residual_ok = False
            break
    if not residual_ok:
        blockers.append("residuals")

    try:
        closure = float(energy.get("closure_pct"))
    except (TypeError, ValueError):
        closure = None
    if closure is None or not (
            cfd_result_gate.CLOSURE_OK[0] <= closure <= cfd_result_gate.CLOSURE_OK[1]):
        blockers.append("energy_closure")

    mass_error = energy.get("mass_err_pct")
    try:
        mass_ok = mass_error is None or abs(float(mass_error)) <= 5.0
    except (TypeError, ValueError):
        mass_ok = False
    if not mass_ok:
        blockers.append("mass_balance")

    try:
        oscillation_ok = abs(float(energy.get("closure_osc") or 0.0)) <= 10.0
    except (TypeError, ValueError):
        oscillation_ok = False
    if not oscillation_ok:
        blockers.append("energy_oscillation")

    return not blockers, blockers


def closure_status(case_dir):
    """현재 케이스의 에너지 폐합 상태와 남은 반복 추정.
    반환: {closure_pct, citable, need_iters, n_iters, stalled} — 산출 불가 항목은 None."""
    import cfd_report
    out = {"closure_pct": None, "citable": None, "need_iters": None,
           "closure_ready": None, "auto_extend_blockers": [],
           "n_iters": None, "stalled": False}
    meta_path = os.path.join(case_dir, "cfd_case_meta.json")
    if not os.path.isfile(meta_path):
        return out
    with open(meta_path, encoding="utf-8") as f:
        meta = json.load(f)
    ec = cfd_report.energy_closure(case_dir, meta)
    if ec:
        out["closure_pct"] = ec["closure_pct"]
        out["closure_osc"] = ec.get("closure_osc")
        out["mass_err_pct"] = ec.get("mass_err_pct")
    log = cfd_report.find_log(case_dir)
    if log and os.path.isfile(log):
        with open(log, encoding="utf-8", errors="replace") as f:
            parsed = cfd_report.parse_log(f.read())
        out["n_iters"] = parsed.get("n_iters")
        if ec:
            ready, blockers = _auto_extend_convergence(parsed, ec)
            out["closure_ready"] = ready
            out["auto_extend_blockers"] = blockers
            # Compatibility for existing runner consumers. The report/UI must
            # use cfd_report.result_trust(), never this runner alias.
            out["citable"] = ready
        fc = cfd_report.residual_decay_forecast(parsed)
        need = [i["iters_to_target"] for i in fc.values()
                if i.get("iters_to_target")]
        # 감쇠가 멈춘 필드가 있으면 반복만으로는 못 간다(설정 문제일 가능성)
        out["stalled"] = any(i.get("iters_to_target") is None for i in fc.values())
        out["need_iters"] = max(need) if need else 0
    return out


def run_until_closed(case_dir, name=None, keep_mesh=False, progress_cb=None,
                     max_rounds=AUTO_EXTEND_MAX_ROUNDS,
                     total_cap=AUTO_EXTEND_TOTAL_CAP):
    """에너지 폐합이 통과할 때까지 endTime 을 늘려가며 이어서 실행.

    각 라운드: 실행 → 폐합·잔차 재고 → 부족하면 필요 반복수만큼 연장 후 재시작.
    연장량은 관측된 잔차 감쇠율에서 계산한다(임의 배수가 아님).
    반환: run_case 결과 + {rounds, closure_pct, citable, iterations}
    """
    cb = progress_cb or (lambda s: print(s, flush=True))
    case = os.path.abspath(case_dir)
    result = None
    baseline_rounds = []
    for rnd in range(1, max_rounds + 1):
        result = run_case(case, name=name, keep_mesh=keep_mesh, progress_cb=cb,
                          restart_from_latest=(rnd > 1))
        if not result.get("ok"):
            result["rounds"] = rnd
            aggregate = _aggregate_runtime_baselines(baseline_rounds)
            if aggregate is not None:
                result["runtime_baseline"] = aggregate
            return result
        round_baseline = result.get("runtime_baseline")
        if isinstance(round_baseline, dict):
            baseline_rounds.append(round_baseline)
            result["runtime_baseline"] = _aggregate_runtime_baselines(baseline_rounds)
        st = closure_status(case)
        closure_ready = st.get("closure_ready", st.get("citable"))
        result.update(rounds=rnd, closure_pct=st["closure_pct"],
                      closure_ready=closure_ready, citable=closure_ready,
                      iterations=st["n_iters"])
        if closure_ready:
            cb(f"[자동연장] 라운드 {rnd}: 에너지 폐합 통과"
               f"({st['closure_pct']:.0f}%) — 종료")
            return result
        if st["closure_pct"] is None:
            cb("[자동연장] 폐합을 계산할 수 없는 케이스 — 연장하지 않고 종료")
            return result
        if st["stalled"]:
            cb("[자동연장] 잔차 감쇠가 정체 — 반복을 늘려도 수렴하지 않습니다."
               " 격자·경계조건·완화계수를 점검하세요.")
            result["stall"] = True
            return result
        cur_end, _, _ = _control_dict_end_time(case)
        if cur_end is None:
            cb("[자동연장] controlDict endTime 을 읽지 못해 연장을 중단합니다.")
            return result
        step = min(int(st["need_iters"] or 0) or 1000, AUTO_EXTEND_STEP_CAP)
        new_end = cur_end + step
        if new_end > total_cap:
            new_end = total_cap
        if new_end <= cur_end:
            cb(f"[자동연장] 총 반복 상한({total_cap:,})에 도달 — 종료."
               f" 현재 폐합 {st['closure_pct']:.0f}%")
            result["capped"] = True
            return result
        if rnd == max_rounds:
            cb(f"[자동연장] 라운드 상한 도달 — 종료. 현재 폐합 {st['closure_pct']:.0f}%")
            return result
        _set_control_dict_end_time(case, new_end)
        cb(f"[자동연장] 라운드 {rnd}: 폐합 {st['closure_pct']:.0f}% 미달,"
           f" 잔차 기준 {st['need_iters']:,}회 추가 필요 →"
           f" endTime {cur_end:g} → {new_end:g} 로 늘려 이어서 실행합니다.")
    return result


def main():
    ap = argparse.ArgumentParser(description="WSL OpenFOAM 실행 + 결과 회수")
    ap.add_argument("case", nargs="?", help="생성된 케이스 디렉토리(Windows 경로)")
    ap.add_argument("--keep-mesh", action="store_true", help="polyMesh 도 회수")
    ap.add_argument("--name", default=None, help="WSL 실행 폴더명(기본 케이스 basename)")
    ap.add_argument("--once", action="store_true",
                    help="폐합 미달이어도 연장하지 않고 1회만 실행(기본은 자동 연장)")
    ap.add_argument(
        "--record-runtime-evidence",
        metavar="PATH",
        help="현재 WSL 환경과 (실행했다면) 직렬 baseline을 runtime_capability.v1 JSON으로 저장",
    )
    args = ap.parse_args()

    if args.record_runtime_evidence and not args.case:
        recorded = record_runtime_capability(args.record_runtime_evidence)
        print(json.dumps(recorded["manifest"], ensure_ascii=False, indent=2))
        return 0
    if not args.case:
        ap.error("case 또는 --record-runtime-evidence가 필요합니다.")

    # CLI 는 Time 라인을 25개마다 하나만 출력(도배 방지) — 이전 동작 유지
    n_time = [0]

    def cli_cb(line):
        if line.startswith("Time = "):
            n_time[0] += 1
            if n_time[0] % 25 != 0:
                return
        print(line, flush=True)

    runner = run_case if args.once else run_until_closed
    r = runner(args.case, name=args.name, keep_mesh=args.keep_mesh, progress_cb=cli_cb)
    if not r["ok"]:
        print(r["error"], file=sys.stderr)
        sys.exit(2)
    if args.record_runtime_evidence:
        recorded = record_runtime_capability(
            args.record_runtime_evidence, r.get("runtime_baseline")
        )
        print(f"런타임 증거 저장: {recorded['path']}")
    if r.get("closure_pct") is not None:
        print(f"\n에너지 폐합율 {r['closure_pct']:.0f}% · 반복 {r.get('iterations')}회"
              f" · 라운드 {r.get('rounds')}"
              + ("" if r.get("citable") else "  ← 아직 인용 불가"))
    print("\n실행 완료. 리포트:")
    print(f"  python cfd_report.py \"{r['case']}\"")


if __name__ == "__main__":
    main()
