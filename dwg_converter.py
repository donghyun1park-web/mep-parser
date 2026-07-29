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
INSTALL_GUIDE = (
    "DWG 변환에는 무료 'ODA File Converter' 가 필요합니다.\n"
    "  1) https://www.opendesign.com/guestfiles/oda_file_converter 에서 다운로드\n"
    "  2) 기본 경로로 설치 (C:\\Program Files\\ODA\\...)\n"
    "  3) 다시 시도 — 자동 탐지됩니다.\n"
    "(대안: CAD 프로그램에서 '다른 이름으로 저장 → DXF' 후 DXF 를 선택)")


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

    exe = find_oda()
    if not exe:
        raise RuntimeError(INSTALL_GUIDE)

    if out_dir is None:
        out_dir = src_dir if os.access(src_dir, os.W_OK) else \
            os.path.join(os.environ.get("TEMP", "."), "mep_dwg")
    os.makedirs(out_dir, exist_ok=True)

    log(f"[DWG] 변환 중: {name} → DXF ({OUT_VERSION}, ODA)")
    try:
        r = subprocess.run(
            [exe, src_dir, out_dir, OUT_VERSION, "DXF", "0", "0", name],
            capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"DWG 변환 시간초과({timeout}s): {name}")

    out_dxf = os.path.join(out_dir, stem + ".dxf")
    if not os.path.exists(out_dxf):
        # ODA 는 실패 시 <이름>.dxf.err 를 남기고 returncode 0 인 경우가 있음
        err_file = out_dxf + ".err"
        detail = ""
        if os.path.exists(err_file):
            try:
                with open(err_file, encoding="utf-8", errors="replace") as f:
                    detail = "\n" + f.read()[:500]
            except Exception:
                pass
        raise RuntimeError(
            f"DWG 변환 실패: {name} (exit {r.returncode}){detail}\n"
            f"STDERR: {(r.stderr or '')[:300]}")
    log(f"[DWG] 변환 완료 → {out_dxf}")
    return out_dxf


def main():
    import argparse
    ap = argparse.ArgumentParser(description="DWG → DXF 변환 (ODA 래퍼)")
    ap.add_argument("dwg", help=".dwg 경로 (.dxf 는 그대로 통과)")
    ap.add_argument("out_dir", nargs="?", default=None, help="출력 폴더(기본 DWG 옆)")
    args = ap.parse_args()
    try:
        out = ensure_dxf(args.dwg, out_dir=args.out_dir)
        print(f"[OK] {out}")
    except RuntimeError as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
