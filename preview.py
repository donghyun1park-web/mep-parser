"""
preview.py — FreeCAD 없는 즉석 3D 미리보기 + 클릭 수정 루프

목표(프로그램 지향점 직결): "쉽고 빠르게 DXF → 3D".
  - geometry.json(또는 DXF) → 자립 preview.html 생성 → 브라우저에서 즉시 3D.
  - FreeCAD 불필요. Vite로 동봉한 Three.js로 벽/기둥/슬래브/개구부/MEP 렌더.
  - 검출 결과를 카테고리·신뢰도 색으로 오버레이(single_offset=경고색) → '눈으로' 검증.
  - 요소 클릭 → 카테고리 재분류 / 치수 override / 삭제 → edits.json 다운로드.
    edits.json 은 EID 기반(element_id.apply_edits) → 재파싱 후에도 수정 보존(라운드트립).

사용:
  python preview.py geometry.json                       # 파싱된 json 미리보기
  python preview.py plan.dxf -m layer_map.csv -b block_map.csv   # DXF 즉시 미리보기
  python preview.py geometry.json -o preview.html --no-open

수정 반영(라운드트립):
  python dxf_parser.py plan.dxf -m layer_map.csv -o geometry.json --edits edits.json
"""
import argparse
import json
import os
import sys
import webbrowser
from pathlib import Path

import geom_contract as _GC   # z 기준면 규약의 단일 출처


def _asset_dir():
    base = getattr(sys, "_MEIPASS", None) or os.path.dirname(os.path.abspath(__file__))
    return Path(base) / "frontend" / "built"


def _asset(name):
    try:
        return (Path(_asset_dir()) / name).read_text(encoding="utf-8")
    except OSError as exc:
        raise RuntimeError("미리보기 빌드 파일이 없습니다. frontend에서 npm ci && npm run build 를 실행하세요: " + name) from exc


def contract_script():
    # The Python geometry contract remains the only source of dimension rules.
    return _GC.js_constants() + "\nglobalThis.MepContract = {gcDim,gcZRange,gcWidthOf,gcCcw,gcBeamRings};"


if sys.stdout is not None and getattr(sys.stdout, "encoding", None) \
        and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


def _collect_xy(elements):
    """모든 요소 좌표 → bbox 중심 산출(카메라 프레이밍·재중심용)."""
    xs, ys = [], []
    for recs in elements.values():
        for r in recs:
            for key in ("centerline", "points"):
                for p in r.get(key, []) or []:
                    if isinstance(p, (list, tuple)) and len(p) >= 2:
                        xs.append(float(p[0])); ys.append(float(p[1]))
            c = r.get("center")
            if isinstance(c, (list, tuple)) and len(c) >= 2:
                xs.append(float(c[0])); ys.append(float(c[1]))
    if not xs:
        return [0.0, 0.0], [0.0, 0.0, 0.0, 0.0]
    bbox = [min(xs), min(ys), max(xs), max(ys)]
    center = [(bbox[0] + bbox[2]) / 2.0, (bbox[1] + bbox[3]) / 2.0]
    return center, bbox


def build_html(data):
    """geometry.json dict → 자립 HTML 문자열."""
    elements = data.get("elements", {})
    params = data.get("params", {})
    center, bbox = _collect_xy(elements)
    payload = {
        "elements": elements,
        "params": params,
        "floors": data.get("floors", []),
        "center": center,
        "bbox": bbox,
        "source": data.get("source", ""),
        "wall_pairing": data.get("wall_pairing", {}),
        "window_schedule": data.get("window_schedule", []),
        # ★ '왜 이렇게 나왔는지' 를 함께 싣는다. 종전엔 형상만 보냈고, 사용자가
        #   고칠 대상을 보고 있는 유일한 화면에 판단 근거가 하나도 없었다.
        "warnings": data.get("warnings", []),
        "thin_pairs": data.get("thin_pairs", {}),
        "width_conflicts": data.get("width_conflicts", []),
        "qa": data.get("qa", {}),
        "edits_report": data.get("edits_report", {}),
        "project_runtime": data.get("project_runtime"),
        "project_edits": data.get("project_edits", {}),
        "source_drawing": data.get("source_drawing", {"status":"unavailable","floors":[],"warnings":["원본 DXF가 연결되지 않았습니다."]}),
    }
    data_json = json.dumps(payload, ensure_ascii=False)
    # JS 안전: </script> 분리
    data_json = data_json.replace("</", "<\\/")
    # Runtime/customer data never enters Vite's reusable production bundle.
    import re
    def script_safe(text):
        # Do not rewrite arbitrary </ tokens: a minified comparison followed by
        # a regexp (x</.../) is valid JavaScript and would be corrupted.
        return re.sub(r'</script', r'<\\/script', text, flags=re.IGNORECASE)
    replacements = {
        "/*__DATA__*/": data_json,
        "/*__STYLE__*/": _asset("preview.css").replace("</", "<\\/"),
        "/*__CONTRACT__*/": script_safe(contract_script()),
        "/*__APP__*/": script_safe(_asset("preview.js")),
    }
    # One pass prevents marker strings inside customer data from being substituted.
    html = re.sub(r"/\*__(?:DATA|STYLE|CONTRACT|APP)__\*/",
                  lambda m: replacements[m.group()], _asset("shell.html"))
    return html


def load_data(path, layer_map=None, block_map=None):
    """입력이 .dxf 면 파싱, .json 이면 그대로 로드."""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".dxf":
        import dxf_parser as P
        rules = P.load_layer_map(layer_map) if layer_map else P.DEFAULT_LAYER_RULES
        brules = P.load_layer_map(block_map) if block_map else P.DEFAULT_BLOCK_RULES
        data = P.parse(path, rules, brules)
        from source_drawing import build_source_drawing
        data['source_drawing'] = build_source_drawing([{'id': 'main', 'path': path}], data)
        return data
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    source = Path(data.get('source', ''))
    if source.suffix.lower() == '.dxf':
        if not source.is_absolute():
            source = Path(path).resolve().parent / source
        from source_drawing import build_source_drawing
        data['source_drawing'] = build_source_drawing([{'id': 'main', 'path': str(source)}], data)
    return data


def main():
    ap = argparse.ArgumentParser(description="FreeCAD 없는 즉석 3D 미리보기")
    ap.add_argument("input", help="geometry.json 또는 .dxf")
    ap.add_argument("-m", "--map", default=None, help="layer_map.csv (DXF 입력 시)")
    ap.add_argument("-b", "--blockmap", default=None, help="block_map.csv (DXF 입력 시)")
    ap.add_argument("-o", "--out", default=None, help="출력 HTML 경로(기본 <입력>_preview.html)")
    ap.add_argument("--no-open", action="store_true", help="브라우저 자동 열기 비활성")
    args = ap.parse_args()

    data = load_data(args.input, args.map, args.blockmap)
    html = build_html(data)
    out = args.out or (os.path.splitext(args.input)[0] + "_preview.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)

    el = data.get("elements", {})
    counts = {k: len(v) for k, v in el.items() if v}
    print(f"미리보기 생성 -> {out}")
    print("  요소:", ", ".join(f"{k}={v}" for k, v in counts.items()) or "(없음)")
    print("  브라우저에서 마우스: 좌드래그=회전, 휠=줌, 우드래그=이동. 요소 클릭=수정.")
    if not args.no_open:
        webbrowser.open("file://" + os.path.abspath(out))



if __name__ == "__main__":
    main()
