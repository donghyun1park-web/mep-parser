# -*- coding: utf-8 -*-
"""BOQ 벽 두께는 `geom_contract.width_of` 와 같은 순서를 따른다(overrides > 신뢰되는 실측 > params).

종전에는 `width_detected` 를 overrides 보다 먼저 읽고 `thin_pair` 실측도 그대로 믿어, 빌더는 200mm
로 세우는 벽이 물량표에서는 0/50mm 로 잡혔다(golden 664벽 도면 실측: thin_pair 7개 → T0×4·T50×3).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import boq_export as BQ
import geom_contract as GC


def _wall(**kw):
    base = {"eid": "w:1", "kind": "polyline", "closed": False,
            "centerline": [[0, 0], [3000, 0]], "points": [[0, 0], [3000, 0]]}
    base.update(kw)
    return base


def test_wall_thickness_follows_the_contract():
    """thin_pair 실측(50mm)은 물량표에서도 불신 — params 기본값 200 으로 세워진다."""
    g = {"params": {"wall": {"width": 200.0, "height": 2800.0}},
         "elements": {"wall": [_wall(width_detected=50.0, review_reason="thin_pair")]}}
    rows = {r[0]: r for r in BQ.aggregate(g)["벽"][1]}
    assert "T200" in rows and "T50" not in rows and "T0" not in rows


def test_override_beats_detected_width():
    g = {"params": {"wall": {"width": 200.0, "height": 2800.0}},
         "elements": {"wall": [_wall(width_detected=250.0, overrides={"width": 300.0})]}}
    rows = {r[0]: r for r in BQ.aggregate(g)["벽"][1]}
    assert "T300" in rows and "T250" not in rows


def test_boq_agrees_with_builder_width():
    """샘플 도면의 모든 벽에서, BOQ 가 세운 T-키는 GC.width_of 가 계산한 값과 같다."""
    import dxf_parser as P
    ROOT = Path(__file__).resolve().parents[1]
    g = P.parse(str(ROOT / "sample_walls.dxf"), P.load_layer_map(str(ROOT / "layer_map.csv")))
    rows = {r[0] for r in BQ.aggregate(g)["벽"][1]}
    expected = {"폐합(솔리드)" if (w.get("closed") and len(w.get("points", [])) >= 3)
                else f"T{BQ._r10(GC.width_of(w, g['params'], 'wall'))}"
                for w in g["elements"]["wall"]}
    assert rows == expected


def test_state_carries_boq_and_an_edit_updates_it_on_save():
    """Phase 4 — `ProjectSession._parse` 가 매번 `boq_export.aggregate()` 를 얹는다(`data['boq']`).
    저장 한 번으로 요약이 바뀐다 — 재파싱을 따로 부를 필요가 없다."""
    import json
    import tempfile
    import ezdxf
    from project_store import ProjectStore
    from project_server import ProjectSession
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        doc = ezdxf.new(); doc.header["$INSUNITS"] = 4
        ms = doc.modelspace()
        ms.add_line((0, 0), (8000, 0), dxfattribs={"layer": "A-WALL"})
        ms.add_line((0, 200), (8000, 200), dxfattribs={"layer": "A-WALL"})
        dxf = tmp / "plan.dxf"
        doc.saveas(dxf)
        layer_map = tmp / "layer_map.csv"
        layer_map.write_text("pattern,category,width,height,thickness,opts\n^A-WALL$,wall,,2800,,\n", encoding="utf-8")
        store = ProjectStore(tmp / "proj.mep").create([{"id": "main", "path": str(dxf), "layer_map": str(layer_map)}])
        sess = ProjectSession(store)
        state = sess.state()
        boq = state["geometry"]["boq"]
        assert json.dumps(boq, ensure_ascii=False)                      # 실패 시 project_server 는 {'error': ...} 만 낸다
        assert {r[0] for r in boq["벽"][1]} == {"T200"}
        wall_eid = state["geometry"]["elements"]["wall"][0]["eid"]
        saved = sess.edit(wall_eid, {"overrides": {"width": 300}}, state["revision"], state["project_id"])
        assert {r[0] for r in saved["geometry"]["boq"]["벽"][1]} == {"T300"}


def test_excel_says_what_it_does_not_count():
    """★ 물량표는 **남이** 읽는다 — 벽 면적이 개구부를 뺀 값이라고 넘겨짚으면 물량이 틀린다.
    화면 패널의 같은 문장은 `review_logic.js` 의 `BOQ_SCOPE_NOTE` 가 잠근다(node 테스트)."""
    import tempfile
    try:
        import openpyxl
    except ImportError:
        import unittest
        raise unittest.SkipTest("openpyxl 없음 — 선택 의존성")
    g = {"params": {"wall": {"width": 200.0, "height": 2800.0}},
         "elements": {"wall": [_wall()]}}
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "boq.xlsx"
        BQ.export_boq_xlsx(g, str(path), title="검사")
        meta = openpyxl.load_workbook(path).active.cell(row=2, column=1).value
    for phrase in ("개구부 미차감", "접합부 중복", "하한", "검토용"):
        assert phrase in meta, (phrase, meta)


def demo():
    test_wall_thickness_follows_the_contract()
    test_override_beats_detected_width()
    test_boq_agrees_with_builder_width()
    test_state_carries_boq_and_an_edit_updates_it_on_save()
    print("ok")


if __name__ == "__main__":
    demo()
