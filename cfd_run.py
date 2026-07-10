"""
cfd_run.py — 생성된 케이스를 WSL OpenFOAM 으로 실행하고 결과를 회수

Windows에서 한 명령으로: WSL 홈(~/cfd_runs/<name>)에 복사(/mnt/c 9p 느림 회피) →
OpenFOAM 환경 source 후 Allrun(포그라운드) → 로그 스트리밍 → 로그·postProcessing·마지막
time 디렉토리만 Windows 케이스로 회수(GB 방지).

사용:
  python cfd_run.py case_pilot
  python cfd_run.py case_pilot --keep-mesh   # polyMesh 도 회수(디버깅)

전제: WSL2 + Ubuntu + OpenFOAM(apt openfoam, /usr/share/openfoam/etc/bashrc). 없으면 안내.
"""
import argparse
import os
import subprocess
import sys

OF_BASHRC = "/usr/share/openfoam/etc/bashrc"


def _wsl(cmd, capture=False, stream=False):
    """WSL bash -lc 로 명령 실행."""
    full = ["wsl", "-e", "bash", "-c", cmd]
    if stream:
        return subprocess.run(full)
    r = subprocess.run(full, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return r


def win_to_wsl(path):
    """Windows 경로 → WSL 경로(wslpath). 실패 시 /mnt 규칙 폴백."""
    p = os.path.abspath(path)
    r = _wsl(f"wslpath -u '{p}'")
    if r.returncode == 0 and r.stdout.strip():
        return r.stdout.strip()
    drive = p[0].lower()
    return "/mnt/" + drive + p[2:].replace("\\", "/")


def main():
    ap = argparse.ArgumentParser(description="WSL OpenFOAM 실행 + 결과 회수")
    ap.add_argument("case", help="생성된 케이스 디렉토리(Windows 경로)")
    ap.add_argument("--keep-mesh", action="store_true", help="polyMesh 도 회수")
    ap.add_argument("--name", default=None, help="WSL 실행 폴더명(기본 케이스 basename)")
    args = ap.parse_args()

    case = os.path.abspath(args.case)
    if not os.path.isdir(case):
        print(f"케이스 폴더 없음: {case}", file=sys.stderr)
        sys.exit(1)
    name = args.name or os.path.basename(case.rstrip("/\\"))
    wsl_case = win_to_wsl(case)

    # OpenFOAM 존재 확인
    chk = _wsl(f"test -f {OF_BASHRC} && echo ok")
    if "ok" not in (chk.stdout or ""):
        print(f"WSL 에 OpenFOAM 환경({OF_BASHRC})이 없습니다.\n"
              "  설치: sudo apt-get install openfoam  (또는 dl.openfoam.com 레포)", file=sys.stderr)
        sys.exit(2)

    run_dir = f"~/cfd_runs/{name}"
    print(f"[1/3] WSL 홈으로 복사: {run_dir}")
    _wsl(f"mkdir -p ~/cfd_runs && rm -rf {run_dir} && cp -r '{wsl_case}' {run_dir}")

    print(f"[2/3] Allrun 실행(포그라운드)...")
    # OpenFOAM env source 후 Allrun. solver 전체출력을 log.* 로 tee 하고, 진행은
    # 단계 메시지 + 25스텝마다 Time 라인만 얇게 스트리밍(400 iter 도배 방지).
    run_cmd = (f"source {OF_BASHRC} 2>/dev/null; cd {run_dir} && "
               f"chmod +x Allrun 2>/dev/null; ./Allrun 2>&1 | "
               f"awk '/^Time =/{{n++; if(n%25==0) print; next}} {{print}}'")
    _wsl(run_cmd, stream=True)

    print(f"[3/3] 결과 회수 -> {case}")
    # 로그·postProcessing·마지막 time 디렉토리만
    recover = (f"cd {run_dir} && "
               f"cp log.* '{wsl_case}/' 2>/dev/null; "
               f"[ -d postProcessing ] && cp -r postProcessing '{wsl_case}/' 2>/dev/null; "
               f"LAST=$(ls -d [0-9]* 2>/dev/null | sort -n | tail -1); "
               f"[ -n \"$LAST\" ] && [ \"$LAST\" != \"0\" ] && cp -r \"$LAST\" '{wsl_case}/' 2>/dev/null; "
               + (f"cp -r constant/polyMesh '{wsl_case}/constant/' 2>/dev/null; " if args.keep_mesh else "")
               + "echo done")
    _wsl(recover)

    # 요약
    logp = os.path.join(case, "log.buoyantBoussinesqSimpleFoam")
    if not os.path.exists(logp):
        import glob
        cands = glob.glob(os.path.join(case, "log.*Foam"))
        logp = cands[0] if cands else None
    print("\n실행 완료. 리포트:")
    print(f"  python cfd_report.py \"{case}\"")


if __name__ == "__main__":
    main()
