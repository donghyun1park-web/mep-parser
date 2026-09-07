# -*- coding: utf-8 -*-
"""GUI 레이어맵 편집기의 CSV 왕복.

종전 구현은 6컬럼 파일을 **5컬럼으로 읽고 5컬럼으로 썼다.** 편집기에서 '저장' 을
누르면 `opts` 전체(`pair_max`·`pair_min`·`from=dim`·`member_re`·`schedule=`·
`material=`)와 주석이 통째로 사라졌고, 사용자가 알 방법이 없었다.

파서가 넣은 `row.get(None) → LayerMapError` 가드는 이걸 못 잡는다 — 쓰기가
자기 일관적이라 5컬럼 파일로 조용히 로드되기 때문이다. 그래서 여기서 잡는다.
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

SAMPLE = """pattern,category,width,height,thickness,opts
# 선매칭 우선 — 좁은 패턴을 위에 둘 것
배수판_벽체|배수판,ignore,,,,
# 보: 축선은 DIMENSION, 단면은 일람표에서
^(AU|STEEL)_(BEAM|GIRDER)$,beam,,,,pair_max=1800;from=dim;schedule=BEAM_SCHEDULE
WALL|벽|CON,wall,,2800,,material=콘크리트
"""


def _mod():
    """mep_gui 는 tkinter 를 임포트한다 — 헤드리스에서도 되지만 창은 안 띄운다."""
    import importlib
    return importlib.import_module("mep_gui")


def _tmp(text):
    p = os.path.join(tempfile.mkdtemp(prefix="lmrt_"), "layer_map.csv")
    with open(p, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    return p


def test_read_write_preserves_opts_and_comments():
    """★ 열었다 저장하면 **한 글자도 달라지면 안 된다.**"""
    G = _mod()
    p = _tmp(SAMPLE)
    G._write_csv_rows(p, G._read_csv_rows(p))
    with open(p, encoding="utf-8") as f:
        after = f.read()
    assert after == SAMPLE, "왕복에서 내용이 바뀌었다:\n" + after


def test_opts_survive_a_row_edit():
    """다른 행을 고쳐도 남의 opts 는 그대로다."""
    G = _mod()
    p = _tmp(SAMPLE)
    rows = G._read_csv_rows(p)
    rows[-1]["width"] = "250"
    G._write_csv_rows(p, rows)
    back = G._read_csv_rows(p)
    assert back[1]["opts"] == "pair_max=1800;from=dim;schedule=BEAM_SCHEDULE", back[1]
    assert back[-1]["opts"] == "material=콘크리트" and back[-1]["width"] == "250", back[-1]


def test_saved_file_still_loads_in_the_parser():
    """저장한 파일이 파서에서 그대로 로드되고 opts 가 살아 있는지."""
    import dxf_parser as dp
    G = _mod()
    p = _tmp(SAMPLE)
    G._write_csv_rows(p, G._read_csv_rows(p))
    rules = dp.load_layer_map(p)
    beam = [r for r in rules if r[1] == "beam"][0]
    assert beam[2]["_opts"]["schedule"] == "BEAM_SCHEDULE", beam[2]
    wall = [r for r in rules if r[1] == "wall"][0]
    assert wall[2]["material"] == "콘크리트", wall[2]


def test_editor_categories_cover_the_parser():
    """편집기에 없는 카테고리는 저장 시 표현되지 않는다 — 목록이 어긋나면 안 된다."""
    import dxf_parser as dp
    G = _mod()
    missing = set(dp.VALID_CATEGORIES) - set(G.CATEGORIES)
    assert not missing, f"편집기 CATEGORIES 에 빠진 것: {sorted(missing)}"
