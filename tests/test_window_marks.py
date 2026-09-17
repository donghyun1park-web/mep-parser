# -*- coding: utf-8 -*-
"""평면도는 창호의 **위치와 폭**만 준다(블록 이름 W-1200 · D-900). 높이·창대높이는 창호일람에서 온다.

실측(아파트 단위세대 평면도): 창·문 16개가 전부 높이 1200·창대 900 추정치였고, 문(D-900·PD-750·FSD-1100)까지
창 기본값을 받았다. 블록 이름이 부호이므로 그 부호로 일람과 조인한다 — 사용자는 양식의 빈칸(높이·창대)만 채운다.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ezdxf
import dxf_parser as P
from schedule_io import schedule_with_marks

ROOT = Path(__file__).resolve().parents[1]


def _plan(tmp_path):
    doc = ezdxf.new(units=4)
    for name in ("XREF_U$0$W-1200", "D-900"):
        doc.blocks.new(name).add_line((-100, 0), (100, 0))     # 열린 선뿐 → 이름 폭의 마커로 폴백
    ms = doc.modelspace()
    ms.add_line((0, 0), (8000, 0), dxfattribs={"layer": "A-WALL"})
    ms.add_line((0, 200), (8000, 200), dxfattribs={"layer": "A-WALL"})
    for name, x in (("XREF_U$0$W-1200", 2000), ("XREF_U$0$W-1200", 4000), ("D-900", 6000)):
        ms.add_blockref(name, (x, 100), dxfattribs={"layer": "A-DOOR"})
    p = tmp_path / "plan.dxf"
    doc.saveas(p)
    return str(p)


def _parse(path, **kw):
    return P.parse(path, P.load_layer_map(str(ROOT / "layer_map.csv")),
                   P.load_layer_map(str(ROOT / "block_map.csv")), **kw)


def test_block_openings_carry_their_mark_and_doors_do_not_get_window_defaults(tmp_path):
    g = _parse(_plan(tmp_path))
    ops = {(o["mark"], round(o["center"][0])): o for o in g["elements"]["opening"]}
    assert set(ops) == {("W-1200", 2000), ("W-1200", 4000), ("D-900", 6000)}
    w, d = ops[("W-1200", 2000)], ops[("D-900", 6000)]
    assert (w["subtype"], w["width"], w["height"], w["sill"]) == ("window", 1200.0, 1200.0, 900.0)
    assert (d["subtype"], d["width"], d["height"], d["sill"]) == ("door", 900.0, 2100.0, 0.0)
    assert "height" in w["dims_assumed"] and "sill" in d["dims_assumed"]
    assert g["window_marks"] == [
        {"mark": "W-1200", "subtype": "window", "width": 1200.0, "height": None, "sill": None, "count": 2},
        {"mark": "D-900", "subtype": "door", "width": 900.0, "height": None, "sill": None, "count": 1}]


def test_schedule_rows_fill_height_and_sill_by_mark_and_the_rest_stay_assumed(tmp_path):
    sched = [{"mark": "w-1200", "subtype": "window", "width": 1200.0, "height": 2100.0, "sill": 300.0, "count": 2}]
    g = _parse(_plan(tmp_path), ext_schedule=sched)
    for o in g["elements"]["opening"]:
        if o["mark"] == "W-1200":
            assert (o["height"], o["sill"], o.get("dims_source")) == (2100.0, 300.0, "schedule")
            assert "dims_assumed" not in o
        else:
            assert o["dims_assumed"] == ["height", "sill"]
    assert g["openings_dims_assumed"] == {"height": 1, "sill": 1}
    # 양식은 일람 행 + 일람에 없는 부호(빈칸)만 — 사용자가 채울 줄이 D-900 한 줄로 드러난다
    rows = schedule_with_marks(g["window_schedule"], g["window_marks"])
    assert [(r["mark"], r["height"]) for r in rows] == [("w-1200", 2100.0), ("D-900", None)]


def test_layer_rule_inserted_first_wins_over_the_broad_column_rule(tmp_path):
    import mep_gui
    csv = tmp_path / "layer_map.csv"
    csv.write_text("# comment\npattern,category,width,height,thickness,opts\nCOL|기둥,column,400,3000,,\n", encoding="utf-8")
    mep_gui.insert_layer_rule_first(str(csv), r"(^|\$)A\-COL$,wall,,2800,")
    rules = P.load_layer_map(str(csv))
    assert P.classify("XREF_U$0$A-COL", rules)[0] == "wall"
    assert P.classify("S-COL", rules)[0] == "column"
    assert csv.read_text(encoding="utf-8").splitlines()[1:3] == [
        "pattern,category,width,height,thickness,opts", r"(^|\$)A\-COL$,wall,,2800,"]


def test_a_gap_that_already_holds_a_block_opening_is_not_opened_twice(tmp_path):
    """블록 삽입점이 창의 끝에 있어도(중심에서 1800mm) 같은 끊김에 두 번째 창을 만들지 않는다."""
    doc = ezdxf.new(units=4)
    doc.blocks.new("W-3600").add_line((0, 0), (3600, 0))
    ms = doc.modelspace()
    for y in (0, 200):   # 벽 두 장, 사이 3600 끊김
        ms.add_line((0, y), (2000, y), dxfattribs={"layer": "A-WALL"})
        ms.add_line((5600, y), (9000, y), dxfattribs={"layer": "A-WALL"})
    ms.add_blockref("W-3600", (2000, 100), dxfattribs={"layer": "A-DOOR"})   # 삽입점 = 끊김의 왼쪽 끝
    p = tmp_path / "gap.dxf"
    doc.saveas(p)
    sched = [{"mark": "W-3600", "subtype": "window", "width": 3600.0, "height": 2200.0, "sill": 200.0, "count": 1}]
    g = _parse(str(p), ext_schedule=sched)
    ops = g["elements"]["opening"]
    assert [(o.get("mark"), o.get("source")) for o in ops] == [("W-3600", None)], ops


def _fake_app(data, csv_path):
    import types
    calls = []
    app = types.SimpleNamespace(
        data=data, v_map=types.SimpleNamespace(get=lambda: csv_path, set=lambda p: calls.append(("v_map", p))),
        _log=lambda m: calls.append(("log", m)), _do_parse=lambda: calls.append(("parse",)) or True,
        _do_schedule_export=lambda: calls.append(("export",)), _do_schedule_pick=lambda: "sched.xlsx",
        _ask_window_dims=lambda marks, n: calls.append(("ask", len(marks), n)) or "load")
    return app, calls


def test_the_gui_asks_about_a_wall_like_column_layer_once_and_reparses_on_yes(tmp_path, monkeypatch):
    """외벽이 기둥 레이어에 있으면 오답이 첫 화면이 되기 전에 묻는다. 예 → layer_map 한 줄 + 재파싱. 같은 질문은 한 번만."""
    import mep_gui
    csv = tmp_path / "layer_map.csv"
    csv.write_text("pattern,category,width,height,thickness,opts\nCOL|기둥,column,400,3000,,\n", encoding="utf-8")
    row = r"(^|\$)A\-COL$,wall,,2800,"
    data = {"column_layers_like_wall": [{"layer": "X$0$A-COL", "lines": 78, "paired": 49, "spacing_mm": 200,
                                         "suggested_row": row}]}
    app, calls = _fake_app(data, str(csv))
    monkeypatch.setattr(mep_gui.messagebox, "askyesno", lambda *a, **k: True)
    assert mep_gui.App._prompt_after_parse(app, "plan.dxf") is True
    assert ("parse",) in calls and row in csv.read_text(encoding="utf-8").splitlines()[1]
    calls.clear()
    assert mep_gui.App._prompt_after_parse(app, "plan.dxf") is False and calls == []


def test_the_gui_asks_for_window_dims_when_they_are_assumed_and_reparses_after_a_schedule_is_picked(tmp_path, monkeypatch):
    import mep_gui
    data = {"openings_dims_assumed": {"height": 16, "sill": 16},
            "window_marks": [{"mark": "W-1200", "subtype": "window", "width": 1200.0, "count": 2}]}
    app, calls = _fake_app(data, str(tmp_path / "layer_map.csv"))
    assert mep_gui.App._prompt_after_parse(app, "plan.dxf") is True
    assert ("ask", 1, 16) in calls and ("parse",) in calls
    calls.clear()
    assert mep_gui.App._prompt_after_parse(app, "plan.dxf") is False and calls == []   # 한 세션에 한 번


def test_door_window_prefixes_are_doors_like_every_other_mark_table():
    """ADW·DW(문+창)는 문이다 — WINDOW_MARK_TYPES·schedule_io 와 같은 판정. 창으로 보면 1200 높이·창대 900 이 된다."""
    assert P._mark_from_block_name("XREF$0$ADW-2950") == ("ADW-2950", "door")
    assert P._mark_from_block_name("DW-1500") == ("DW-1500", "door")
    assert P._mark_from_block_name("AG-1100") == ("AG-1100", "window")
    from schedule_io import _kind_label_to_subtype
    assert [_kind_label_to_subtype("", m) for m in ("W-1200", "AG-1100", "FSD-1100", "ADW-2950", "AW", "AD")] == ["window", "window", "door", "door", "window", "door"]


def test_blank_schedule_cells_do_not_masquerade_as_user_values(tmp_path):
    """양식의 빈 종류·창대 칸은 기본값이 아니라 빈칸이다: 블록이 아는 종류는 남고, 창대는 추정치로 **보고된다**. 높이 0 은 값이 아니다."""
    import openpyxl
    from schedule_io import load_schedule_xlsx
    wb = openpyxl.Workbook(); ws = wb.active
    ws.append(["부호", "종류", "폭(mm)", "높이(mm)", "창대높이(mm)", "수량"])
    ws.append(["W-1200", None, 1200, 1500, None, 2])
    ws.append(["D-900", None, 900, 0, None, 1])
    x = tmp_path / "s.xlsx"; wb.save(x)
    rows = load_schedule_xlsx(str(x))
    assert [(r["mark"], r["subtype"], r["sill"]) for r in rows] == [("W-1200", "window", None), ("D-900", "door", None)]
    g = _parse(_plan(tmp_path), ext_schedule=rows)
    for o in g["elements"]["opening"]:
        if o["mark"] == "W-1200":
            assert (o["subtype"], o["height"], o["sill"], o["dims_assumed"]) == ("window", 1500.0, 900.0, ["sill"])
        else:
            assert (o["subtype"], o["height"], o.get("dims_source")) == ("door", 2100.0, None)   # 높이 0 행은 무시
    assert g["openings_dims_assumed"] == {"height": 1, "sill": 3}


def test_cancelling_the_schedule_file_dialog_keeps_the_question_for_next_time(tmp_path):
    import mep_gui
    data = {"openings_dims_assumed": {"height": 2, "sill": 2},
            "window_marks": [{"mark": "W-1200", "subtype": "window", "width": 1200.0, "count": 2}]}
    app, calls = _fake_app(data, str(tmp_path / "layer_map.csv"))
    app._do_schedule_pick = lambda: ""            # 취소
    assert mep_gui.App._prompt_after_parse(app, "plan.dxf") is False
    assert ("parse",) not in calls
    calls.clear()
    assert mep_gui.App._prompt_after_parse(app, "plan.dxf") is False and ("ask", 1, 2) in calls   # 다시 묻는다


def test_openings_made_from_the_schedule_are_not_listed_as_plan_marks(tmp_path):
    """벽 끊김 폭 매칭으로 생긴 개구부는 일람이 만든 것이다 — 부호 목록(양식)에 다시 올리면 수량이 두 배가 된다."""
    doc = ezdxf.new(units=4)
    ms = doc.modelspace()
    for y in (0, 200):
        ms.add_line((0, y), (3000, y), dxfattribs={"layer": "A-WALL"})
        ms.add_line((3900, y), (8000, y), dxfattribs={"layer": "A-WALL"})     # 900 끊김, 블록 없음
    p = tmp_path / "gap_only.dxf"
    doc.saveas(p)
    g = _parse(str(p), ext_schedule=[{"mark": "D-900", "subtype": "door", "width": 900.0, "height": 2100.0, "sill": 0.0, "count": 1}])
    assert [o.get("source") for o in g["elements"]["opening"]] == ["wall_gap_match"]
    assert g["window_marks"] == []
