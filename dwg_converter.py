"""
dwg_converter.py  —  DWG → DXF 자동 변환 (ODA File Converter 래퍼)

DWG 는 비공개 포맷이라 직접 파싱하지 않는다. 무료 ODA File Converter 를
subprocess 로 호출해 DXF 로 변환한 뒤 기존 결정론 파이프라인(ezdxf)에 태운다.

ODA File Converter (무료):
    https://www.opendesign.com/guestfiles/oda_file_converter
설치만 하면 자동 탐지된다. CLI 규약:
    ODAFileConverter <in_dir> <out_dir> <version> <type> <recurse> <audit> [filter]

사용:
    from dwg_converter import ensure_dxf
    dxf_path = ensure_dxf("plan.dwg")     # .dxf 는 그대로 통과, .dwg 는 변환
    python dwg_converter.py plan.dwg      # CLI 단독 변환
"""
import glob
import os
import shutil
import subprocess
import sys

OUT_VERSION = "ACAD2018"   # ezdxf 가 안정적으로 읽는 최신 규격
LIBREDWG_URL = "https://github.com/LibreDWG/libredwg/releases"
INSTALL_GUIDE = (
    "DWG 변환기가 없습니다. 둘 중 하나를 설치하세요.\n"
    "\n"
    "  [권장] GNU LibreDWG — 설치 프로그램 없음, 회원가입 없음 (약 11.5MB)\n"
    f"     1) {LIBREDWG_URL} 에서 libredwg-*-win64.zip 다운로드\n"
    "     2) %LOCALAPPDATA%\\libredwg 에 압축 해제 (dwg2dxf.exe 가 나오면 됨)\n"
    "     3) 다시 시도 — 자동 탐지됩니다. DWG R10~R2018 지원.\n"
    "\n"
    "  [대안] ODA File Converter — 설치 필요, 회원가입 필요\n"
    "     https://www.opendesign.com/guestfiles/oda_file_converter\n"
    "\n"
    "(설치 없이 진행하려면: CAD 에서 '다른 이름으로 저장 → DXF' 후 DXF 를 선택)")


def find_libredwg():
    """LibreDWG dwg2dxf.exe 자동 탐지. 없으면 None.
    ODA 와 달리 설치 프로그램이 없어 압축 해제만으로 동작 → 우선 백엔드."""
    w = shutil.which("dwg2dxf") or shutil.which("dwg2dxf.exe")
    if w:
        return w
    local = os.environ.get("LOCALAPPDATA", "")
    pats = []
    if local:
        pats += [os.path.join(local, "libredwg", "dwg2dxf.exe"),
                 os.path.join(local, "libredwg", "*", "dwg2dxf.exe")]
    pats += [r"C:\Program Files\libredwg*\dwg2dxf.exe",
             r"C:\Program Files\LibreDWG*\bin\dwg2dxf.exe",
             os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "vendor", "libredwg", "dwg2dxf.exe")]
    for pat in pats:
        hits = sorted(glob.glob(pat), reverse=True)  # 최신 버전 우선
        if hits:
            return hits[0]
    return None


def find_oda():
    """ODAFileConverter.exe 자동 탐지. 없으면 None."""
    w = shutil.which("ODAFileConverter") or shutil.which("ODAFileConverter.exe")
    if w:
        return w
    pats = [r"C:\Program Files\ODA\*\ODAFileConverter.exe",
            r"C:\Program Files (x86)\ODA\*\ODAFileConverter.exe"]
    for pat in pats:
        hits = sorted(glob.glob(pat), reverse=True)  # 최신 버전 우선
        if hits:
            return hits[0]
    return None


def find_converter():
    """(종류, 경로) — LibreDWG 우선, ODA 폴백. 둘 다 없으면 (None, None)."""
    p = find_libredwg()
    if p:
        return "libredwg", p
    p = find_oda()
    if p:
        return "oda", p
    return None, None


def _convert_libredwg(exe, src, out_dxf, timeout, log):
    """dwg2dxf -y -o <out> <src>. 실패 시 RuntimeError."""
    log(f"[DWG] LibreDWG 변환: {os.path.basename(src)}")
    try:
        r = subprocess.run([exe, "-y", "-o", out_dxf, src],
                           capture_output=True, text=True, errors="replace",
                           timeout=timeout)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"DWG 변환 시간초과({timeout}s): {os.path.basename(src)}")
    if not os.path.exists(out_dxf):
        # exit 5 = 지원 범위를 넘는 최신 DWG (R2018 초과)
        hint = ("\n  → DWG 버전이 너무 최신입니다(R2018 이하로 다시 저장하거나 ODA 사용)"
                if r.returncode == 5 else "")
        raise RuntimeError(
            f"LibreDWG 변환 실패 (exit {r.returncode}){hint}\n"
            f"  {(r.stderr or r.stdout or '')[:300]}")
    return out_dxf


def _convert_oda(exe, src_dir, name, out_dir, out_dxf, timeout, log):
    """ODAFileConverter <in_dir> <out_dir> <ver> <type> <recurse> <audit> [filter]."""
    log(f"[DWG] ODA 변환: {name} → DXF ({OUT_VERSION})")
    try:
        r = subprocess.run(
            [exe, src_dir, out_dir, OUT_VERSION, "DXF", "0", "0", name],
            capture_output=True, text=True, errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"DWG 변환 시간초과({timeout}s): {name}")
    if not os.path.exists(out_dxf):
        # ODA 는 실패 시 <이름>.dxf.err 를 남기고 returncode 0 인 경우가 있음
        detail = ""
        err_file = out_dxf + ".err"
        if os.path.exists(err_file):
            try:
                with open(err_file, encoding="utf-8", errors="replace") as f:
                    detail = "\n" + f.read()[:500]
            except Exception:
                pass
        raise RuntimeError(
            f"ODA 변환 실패: {name} (exit {r.returncode}){detail}\n"
            f"STDERR: {(r.stderr or '')[:300]}")
    return out_dxf


def ensure_dxf(path, out_dir=None, timeout=300, log=print):
    """.dxf 는 그대로 반환. .dwg 는 ODA 로 변환해 생성된 .dxf 경로 반환.

    - 변환 결과는 기본적으로 DWG 옆에 <이름>.dxf 로 저장(재사용 캐시:
      기존 .dxf 가 .dwg 보다 새로우면 재변환 생략 — 결정론).
    - DWG 폴더에 쓰기 불가하면 %TEMP%/mep_dwg 폴백.
    - ODA 미설치 시 RuntimeError(INSTALL_GUIDE).
    """
    ext = os.path.splitext(path)[1].lower()
    if ext == ".dxf":
        return path
    if ext != ".dwg":
        raise RuntimeError(f"지원하지 않는 확장자: {ext} (.dxf/.dwg 만 지원)")
    if not os.path.exists(path):
        raise RuntimeError(f"파일 없음: {path}")

    src_dir = os.path.dirname(os.path.abspath(path))
    name = os.path.basename(path)
    stem = os.path.splitext(name)[0]

    # 캐시: DWG 옆 최신 DXF 재사용
    cached = os.path.join(src_dir, stem + ".dxf")
    if os.path.exists(cached) and os.path.getmtime(cached) >= os.path.getmtime(path):
        log(f"[DWG] 기존 변환본 재사용: {cached}")
        return cached

    kind, exe = find_converter()
    if not exe:
        raise RuntimeError(INSTALL_GUIDE)

    if out_dir is None:
        out_dir = src_dir if os.access(src_dir, os.W_OK) else \
            os.path.join(os.environ.get("TEMP", "."), "mep_dwg")
    os.makedirs(out_dir, exist_ok=True)
    out_dxf = os.path.join(out_dir, stem + ".dxf")

    if kind == "libredwg":
        _convert_libredwg(exe, os.path.abspath(path), out_dxf, timeout, log)
    else:
        _convert_oda(exe, src_dir, name, out_dir, out_dxf, timeout, log)
    log(f"[DWG] 변환 완료 → {out_dxf}")
    return out_dxf


def _utf8_console():
    """cp949 콘솔에서 한글·기호 출력이 깨지거나 UnicodeEncodeError 로 죽는 것 방지."""
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass


def main():
    import argparse
    _utf8_console()
    ap = argparse.ArgumentParser(description="DWG → DXF 변환 (LibreDWG / ODA 래퍼)")
    ap.add_argument("dwg", nargs="?", default=None, help=".dwg 경로 (.dxf 는 그대로 통과)")
    ap.add_argument("out_dir", nargs="?", default=None, help="출력 폴더(기본 DWG 옆)")
    ap.add_argument("--which", action="store_true",
                    help="탐지된 변환기 출력 후 종료")
    args = ap.parse_args()
    if args.which:
        kind, exe = find_converter()
        print(f"{kind or '없음'}: {exe or INSTALL_GUIDE}")
        return
    if not args.dwg:
        ap.error("DWG 경로를 지정하세요 (또는 --which)")
    try:
        out = ensure_dxf(args.dwg, out_dir=args.out_dir)
        print(f"[OK] {out}")
    except RuntimeError as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
