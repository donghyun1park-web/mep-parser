# -*- coding: utf-8 -*-
"""layer_map_io — 브라우저·MCP 가 같이 쓰는 CSV 왕복. 레이어를 실제로 분류하는 행을
정규식으로 찾는다(문자열 비교가 아니라 `dxf_parser.classify` 와 같은 판정)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import layer_map_io as L


def _csv(tmp_path, text):
    p = tmp_path / "layer_map.csv"
    p.write_text(text, encoding="utf-8")
    return str(p)


def test_set_opts_merges_into_the_row_that_actually_classifies_the_layer(tmp_path):
    # A-CON 은 표준형이 아닌 손으로 쓴 정규식이다 — 문자열 비교였으면 못 찾았을 것
    p = _csv(tmp_path, "pattern,category,width,height,thickness,opts\n^(A-CON|A-CONC)$,wall,200,2800,,\n")
    L.set_opts(p, "A-CON", {"pair_min": 67})
    rows = L._read_csv_rows(p)
    assert rows[0]["opts"] == "pair_min=67"
    L.set_opts(p, "A-CON", {"pair_min": 67})            # 두 번 적용해도 행 하나·값 하나
    assert L._read_csv_rows(p) == rows


def test_set_opts_preserves_existing_keys_and_comments(tmp_path):
    p = _csv(tmp_path, "pattern,category,width,height,thickness,opts\n# 벽 규칙\n^A-CON$,wall,200,2800,,pair_max=1800\n")
    L.set_opts(p, "A-CON", {"pair_min": 67})
    rows = L._read_csv_rows(p)
    assert set(rows[0]["opts"].split(";")) == {"pair_max=1800", "pair_min=67"}
    assert rows[0]["_before"] == ["# 벽 규칙"]


def test_set_opts_fails_when_no_row_classifies_the_layer(tmp_path):
    p = _csv(tmp_path, "pattern,category,width,height,thickness,opts\n^A-CON$,wall,200,2800,,\n")
    try:
        L.set_opts(p, "S-COL", {"pair_min": 67})
        assert False, "must raise"
    except ValueError as exc:
        assert "S-COL" in str(exc)


def test_set_width_updates_the_matching_row(tmp_path):
    p = _csv(tmp_path, "pattern,category,width,height,thickness,opts\n^A-CON$,wall,200,2800,,\n")
    L.set_width(p, "A-CON", 450.0)
    assert L._read_csv_rows(p)[0]["width"] == "450"


def test_insert_row_is_idempotent_and_lands_above_existing_rules(tmp_path):
    p = _csv(tmp_path, "pattern,category,width,height,thickness,opts\n^S-COL$,column,,,,\n")
    L.insert_row(p, r"^S\-COL$", "wall", height=2800)
    rows = L._read_csv_rows(p)
    assert [r["pattern"] for r in rows] == [r"^S\-COL$", "^S-COL$"]   # 새 규칙이 먼저(선매칭 우선)
    assert rows[0]["category"] == "wall" and rows[0]["height"] == "2800"
    L.insert_row(p, r"^S\-COL$", "wall", height=2800)                 # 같은 제안 두 번 → 행 안 늘어남
    assert len(L._read_csv_rows(p)) == 2


def demo():
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        test_set_opts_merges_into_the_row_that_actually_classifies_the_layer(tmp)
        test_set_opts_preserves_existing_keys_and_comments(tmp)
        test_set_opts_fails_when_no_row_classifies_the_layer(tmp)
        test_set_width_updates_the_matching_row(tmp)
        test_insert_row_is_idempotent_and_lands_above_existing_rules(tmp)
    print("ok")


if __name__ == "__main__":
    demo()
