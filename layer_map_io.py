"""layer_map.csv 읽기·쓰기 — GUI(tkinter)·서버(project_server)·MCP 가 공유한다.

종전에는 이 세 함수가 `mep_gui.py` 안에서만 살아, 브라우저(`project_server`)와 MCP 가
같은 CSV 왕복을 다시 구현하거나 tkinter 를 끌고 오지 않으면 CSV 를 못 고쳤다.
tkinter 의존이 없으므로 어디서든 import 할 수 있다.
"""
import csv
import os
import re


def insert_layer_rule_first(csv_path, row):
    """layer_map 헤더 바로 아래에 규칙 한 줄을 넣는다. 규칙은 **선매칭 우선**이라 끝에 붙이면
    'COL|기둥' 같은 넓은 규칙에 가려져 아무 일도 안 난다 — 파서 경고가 'COL 행 위에' 라고 하는 이유."""
    with open(csv_path, encoding="utf-8") as f:
        lines = f.read().splitlines()
    for i, line in enumerate(lines):
        if line.strip().lower().startswith("pattern,"):
            lines.insert(i + 1, row)
            break
    else:
        raise ValueError(f"layer_map 헤더(pattern,…)가 없습니다: {csv_path}")
    with open(csv_path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")


CSV_FIELDS = ["pattern", "category", "width", "height", "thickness", "opts"]


def _read_csv_rows(csv_path):
    """layer_map.csv → [{...CSV_FIELDS, "_before": [원문 주석줄]}]

    ★ `opts` 와 주석을 **반드시 왕복**시킨다. 종전 구현은 5컬럼만 읽고 5컬럼만 써서,
    편집기에서 '저장' 을 누르면 `pair_max`·`pair_min`·`from=dim`·`member_re`·
    `schedule=`·`material=` 과 주석이 통째로 사라졌다 — 사용자가 알 방법이 없었다.
    (파서가 이번에 넣은 `row.get(None) → LayerMapError` 가드도 이건 못 잡는다.
     쓰기가 자기 일관적이라 5컬럼 파일로 조용히 로드되기 때문이다.)

    주석은 **바로 뒤 규칙에 붙여** 두었다가 저장 시 그 앞에 다시 쓴다 — 주석은
    보통 다음 규칙을 설명하므로 그게 의미를 지킨다.
    """
    rows, pending, seen_header = [], [], False
    if not os.path.exists(csv_path):
        return rows
    with open(csv_path, encoding="utf-8") as f:
        for ln in f.read().splitlines():
            t = ln.strip()
            if not t or t.startswith("#"):
                pending.append(ln)
                continue
            vals = next(csv.reader([ln]))
            if not seen_header:
                seen_header = True          # 헤더 줄
                continue
            r = {k: (vals[i].strip() if i < len(vals) else "")
                 for i, k in enumerate(CSV_FIELDS)}
            r["_before"], pending = pending, []
            rows.append(r)
    if pending and rows:                    # 파일 끝 주석
        rows[-1]["_after"] = pending
    return rows


def _write_csv_rows(csv_path, rows):
    """rows → layer_map.csv. `opts`·주석을 읽은 그대로 되돌려 놓는다."""
    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(CSV_FIELDS)
        for r in rows:
            for c in r.get("_before") or []:
                print(c, file=f)
            w.writerow([r.get(k, "") for k in CSV_FIELDS])
            for c in r.get("_after") or []:
                print(c, file=f)


def find_matching_row(rows, layer):
    """그 레이어를 **실제로 분류하는** 첫 행 — `dxf_parser.classify` 와 같은 판정(선매칭 우선,
    대소문자 무시). 저장된 패턴 문자열이 `layer_rule_pattern` 의 표준형과 다를 수 있어(사람이
    손으로 쓴 정규식 등) 문자열로 비교하지 않는다."""
    for row in rows:
        try:
            if re.search(row["pattern"], layer, re.IGNORECASE):
                return row
        except re.error:
            continue
    return None


def set_opts(csv_path, row_layer, opts):
    """레이어 행의 `opts` 를 병합해 갱신한다(멱등 — 같은 키를 다시 적용해도 행이 안 늘어난다).
    그 레이어를 분류하는 행이 없으면 실패한다(값을 어디 넣을지 모른다). `opts` 는
    `키=값;키=값` 문자열로 합친다 — dxf_parser.OPT_SPEC 형식과 같다."""
    rows = _read_csv_rows(csv_path)
    row = find_matching_row(rows, row_layer)
    if row is None:
        raise ValueError(f"'{row_layer}' 를 분류하는 규칙 행이 없습니다")
    existing = dict(kv.split("=", 1) for kv in row.get("opts", "").split(";") if "=" in kv)
    existing.update({k: str(v) for k, v in opts.items()})
    row["opts"] = ";".join(f"{k}={v}" for k, v in existing.items())
    _write_csv_rows(csv_path, rows)


def set_width(csv_path, row_layer, width):
    """레이어 행의 `width` 열을 바꾼다(멱등) — 없으면 실패(값을 어디 넣을지 모른다)."""
    rows = _read_csv_rows(csv_path)
    row = find_matching_row(rows, row_layer)
    if row is None:
        raise ValueError(f"'{row_layer}' 를 분류하는 규칙 행이 없습니다")
    row["width"] = f"{width:g}"
    _write_csv_rows(csv_path, rows)


def insert_row(csv_path, pattern, category, height=None, width=None):
    """`pattern` 행이 이미 있으면 손대지 않는다(멱등) — 없으면 헤더 아래 새로 만든다."""
    rows = _read_csv_rows(csv_path)
    if any(r["pattern"] == pattern for r in rows):
        return
    row = ",".join([pattern, category, f"{width:g}" if width is not None else "",
                     f"{height:g}" if height is not None else "", "", ""])
    insert_layer_rule_first(csv_path, row)
