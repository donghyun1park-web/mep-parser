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

전제: WSL2 + Ubuntu + OpenFOAM(apt openfoam, /usr/share/openfoam/etc/bashrc). 없으면 안내.
"""
import argparse
import os
import subprocess
import sys

OF_BASHRC = "/usr/share/openfoam/etc/bashrc"


def _wsl(cmd):
    """WSL bash -c 로 명령 실행(캡처)."""
    full = ["wsl", "-e", "bash", "-c", cmd]
    return subprocess.run(full, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def win_to_wsl(path):
    """Windows 경로 → WSL 경로(wslpath). 실패 시 /mnt 규칙 폴백."""
    p = os.path.abspath(path)
    r = _wsl(f"wslpath -u '{p}'")
    if r.returncode == 0 and r.stdout.strip():
        return r.stdout.strip()
    drive = p[0].lower()
    return "/mnt/" + drive + p[2:].replace("\\", "/")


def check_openfoam():
    """WSL + OpenFOAM 환경 존재 여부 (wsl 자체가 없어도 False)."""
    try:
        chk = _wsl(f"test -f {OF_BASHRC} && echo ok")
        return "ok" in (chk.stdout or "")
    except (OSError, FileNotFoundError):
        return False


def run_case(case_dir, name=None, keep_mesh=False, progress_cb=None):
    """케이스 실행: WSL 복사 → Allrun(진행 라인 스트리밍) → 결과 회수.
    progress_cb(line:str): 진행 라인 콜백(None이면 print).
    반환: {"ok": bool, "error": str|None, "case": 절대경로}"""
    cb = progress_cb or (lambda s: print(s, flush=True))
    case = os.path.abspath(case_dir)
    if not os.path.isdir(case):
        return {"ok": False, "error": f"케이스 폴더 없음: {case}", "case": case}
    name = name or os.path.basename(case.rstrip("/\\"))
    if not check_openfoam():
        return {"ok": False, "case": case, "error":
                f"WSL 에 OpenFOAM 환경({OF_BASHRC})이 없습니다.\n"
                "  설치: sudo apt-get install openfoam  (또는 dl.openfoam.com 레포)"}
    wsl_case = win_to_wsl(case)
    run_dir = f"~/cfd_runs/{name}"

    cb(f"[1/3] WSL 홈으로 복사: {run_dir}")
    _wsl(f"mkdir -p ~/cfd_runs && rm -rf {run_dir} && cp -r '{wsl_case}' {run_dir}")

    cb("[2/3] Allrun 실행(포그라운드)...")
    # 진행에 필요한 라인만 통과(전체 solver 출력은 WSL 쪽 log.* 에 tee 됨):
    # 단계 마커(===), 모든 Time 라인(스튜디오 진행바용), 실패/메시 판정.
    run_cmd = (f"source {OF_BASHRC} 2>/dev/null; cd {run_dir} && "
               f"chmod +x Allrun 2>/dev/null; ./Allrun 2>&1 | "
               f"awk '/^Time = /{{print; next}} /^===/{{print; next}} "
               f"/FATAL|FAILED|Mesh OK|\\*\\*\\*|SIMPLE solution converged/{{print; next}}'")
    proc = subprocess.Popen(["wsl", "-e", "bash", "-c", run_cmd],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="replace")
    for line in proc.stdout:
        cb(line.rstrip("\n"))
    proc.wait()

    cb(f"[3/3] 결과 회수 -> {case}")
    # 로그·postProcessing·마지막 time 디렉토리만
    recover = (f"cd {run_dir} && "
               f"cp log.* '{wsl_case}/' 2>/dev/null; "
               f"[ -d postProcessing ] && cp -r postProcessing '{wsl_case}/' 2>/dev/null; "
               f"LAST=$(ls -d [0-9]* 2>/dev/null | sort -n | tail -1); "
               f"[ -n \"$LAST\" ] && [ \"$LAST\" != \"0\" ] && cp -r \"$LAST\" '{wsl_case}/' 2>/dev/null; "
               + (f"cp -r constant/polyMesh '{wsl_case}/constant/' 2>/dev/null; " if keep_mesh else "")
               + "echo done")
    _wsl(recover)

    # 성공 판정: solver 로그가 회수됐고 0이 아닌 time 디렉토리가 존재
    import glob
    got_log = bool(glob.glob(os.path.join(case, "log.*Foam")))
    got_time = any(_is_float_dir(d)
                   for d in os.listdir(case) if os.path.isdir(os.path.join(case, d)))
    if not got_log:
        return {"ok": False, "error": "solver 로그 미회수 — 실행 실패(위 출력 확인)", "case": case}
    if not got_time:
        return {"ok": False, "error": "결과 time 디렉토리 없음 — solver 조기 종료(로그 확인)", "case": case}
    return {"ok": True, "error": None, "case": case}


def _is_float_dir(name):
    try:
        return float(name) > 0
    except ValueError:
        return False


def main():
    ap = argparse.ArgumentParser(description="WSL OpenFOAM 실행 + 결과 회수")
    ap.add_argument("case", help="생성된 케이스 디렉토리(Windows 경로)")
    ap.add_argument("--keep-mesh", action="store_true", help="polyMesh 도 회수")
    ap.add_argument("--name", default=None, help="WSL 실행 폴더명(기본 케이스 basename)")
    args = ap.parse_args()

    # CLI 는 Time 라인을 25개마다 하나만 출력(도배 방지) — 이전 동작 유지
    n_time = [0]

    def cli_cb(line):
        if line.startswith("Time = "):
            n_time[0] += 1
            if n_time[0] % 25 != 0:
                return
        print(line, flush=True)

    r = run_case(args.case, name=args.name, keep_mesh=args.keep_mesh, progress_cb=cli_cb)
    if not r["ok"]:
        print(r["error"], file=sys.stderr)
        sys.exit(2)
    print("\n실행 완료. 리포트:")
    print(f"  python cfd_report.py \"{r['case']}\"")


if __name__ == "__main__":
    main()
