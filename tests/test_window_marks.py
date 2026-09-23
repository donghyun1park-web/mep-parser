# -*- coding: utf-8 -*-
"""평면도는 창호의 **위치와 폭**만 준다(블록 이름 W-1200 · D-900). 높이·창대높이는 창호일람에서 온다.

실측(아파트 단위세대 평면도): 창·문 16개가 전부 높이 1200·창대 900 추정치였고, 문(D-900·PD-750·FSD-1100)까지
창 기본값을 받았다. 블록 이름이 부호이므로 그 부호로 일람과 조인한다 — 사용자는 양식의 빈칸(높이·창대)만 채운다.
"""
import os
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


def test_open_defaults_into_threads_height_and_fans_material_out_to_all_four_categories():
    """열 때 물은 층고·재질 기본값 → open_source_project 인자. tkinter 없이 순수 함수만 검사한다
    (이 코드베이스의 관례: `_do_parse`/다이얼로그를 직접 몰지 않는다 — GUI 이벤트루프 없이는 걸린다)."""
    import mep_gui
    options = {"use_ai": False}
    height = mep_gui._open_defaults_into({"height": 3000.0, "material": "콘크리트"}, options)
    assert height == 3000.0
    assert options["defaults"] == {"architecture": {
        "wall": {"material": "콘크리트"}, "column": {"material": "콘크리트"},
        "slab": {"material": "콘크리트"}, "beam": {"material": "콘크리트"}}}


def test_open_defaults_into_is_a_noop_on_an_empty_pending():
    import mep_gui
    options = {"use_ai": False}
    assert mep_gui._open_defaults_into({}, options) is None
    assert "defaults" not in options


def test_merge_material_default_sets_all_four_architecture_categories_and_keeps_other_keys():
    """'그 밖의 도구 → 프로젝트 기본값' 저장 버튼의 병합 로직 — 재질만 다룬다(층고는 생성 시점 전용)."""
    import mep_gui
    merged = mep_gui._merge_material_default({"mep": {"pipe": {"service": "drain"}}}, "콘크리트")
    assert merged == {
        "mep": {"pipe": {"service": "drain"}},
        "architecture": {"wall": {"material": "콘크리트"}, "column": {"material": "콘크리트"},
                          "slab": {"material": "콘크리트"}, "beam": {"material": "콘크리트"}}}


def test_merge_material_default_clears_architecture_on_an_empty_string():
    import mep_gui
    merged = mep_gui._merge_material_default({"architecture": {"wall": {"material": "PB"}}}, "")
    assert merged == {}


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
    # 세대 창호 부호는 '종류 규격'(공백)이다 — 접두를 '-' 로만 자르면 'AG 9x22' 가 문이 됐다
    assert [_kind_label_to_subtype("", m) for m in ("PW 18x22", "AG 9x22", "FSD 11x22")] == ["window", "window", "door"]


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


# ── 부호 없는 블록: 블록 종류가 폭·종류·부호를 선언한다(block_map opts mark=) ─────────────────
def _unnamed(tmp_path, block_row):
    """이름에 폭·부호가 없는 창 블록(기준점 = 창의 왼쪽 끝, 로컬 X 0~1800)이 1800 끊김에 앉고, 같은 폭의 빈 끊김이 하나 더 있다."""
    doc = ezdxf.new(units=4)
    b = doc.blocks.new("XU$0$A$C0001")
    for y in (-100, 0, 100):
        b.add_line((0, y), (1800, y))
    ms = doc.modelspace()
    for x0, x1 in [(0, 3000), (4800, 6000), (7800, 9000)]:
        for y in (0, 200):
            ms.add_line((x0, y), (x1, y), dxfattribs={"layer": "A-WALL"})
    ms.add_blockref("XU$0$A$C0001", (3000, 100), dxfattribs={"layer": "A-WIN"})
    p = tmp_path / "unnamed.dxf"
    doc.saveas(p)
    bm = tmp_path / "block_map.csv"
    bm.write_text("pattern,category,width,height,thickness,opts\n" + block_row + "\n", encoding="utf-8")
    return str(p), P.load_layer_map(str(bm))


def _parse_blocks(plan, blocks, **kw):
    return P.parse(plan, P.load_layer_map(str(ROOT / "layer_map.csv")), blocks, **kw)


DECLARED = r"\$A\$C0001$,opening,1800,,,subtype=window;mark=PW 18x22"


def test_a_declared_block_width_recentres_an_unnamed_window_block(tmp_path):
    """★ 폭을 block_map 이 선언하면 중심 보정이 안 돌았다 — 이름의 폭만 넘겼다. 창이 삽입점(창 끝)에 반 폭만큼
    옆 벽을 걸치고 앉아 창대 벽·인방이 반쪽만 섰고, 나머지 창 자리는 바닥부터 천장까지 뚫렸다
    (실측: 종합평면도 기준층 블록 창 67개 중 반쯤 걸친 39곳)."""
    g = _parse_blocks(*_unnamed(tmp_path, DECLARED))
    (o,) = g["elements"]["opening"]
    assert o["center"] == [3900.0, 100.0] and o["anchor"] == [3000.0, 100.0]
    assert o["width"] == 1800.0 and "width" not in (o.get("dims_assumed") or [])
    assert (o["mark"], o["subtype"]) == ("PW 18x22", "window")
    below = [w for w in g["elements"]["wall"] if w.get("infill_of") == o["eid"] and w["infill_part"] == "below"]
    assert [sorted(p[0] for p in w["centerline"]) for w in below] == [[3000.0, 4800.0]]


def test_the_900_fallback_width_of_an_unnamed_block_is_reported_assumed(tmp_path):
    """폭을 아무도 말하지 않은 블록의 900 은 가정이다 — 반지름에 실려 '잰 값' 으로 읽히던 것을 드러낸다."""
    g = _parse_blocks(*_unnamed(tmp_path, r"\$A\$C0001$,opening,,,,subtype=window"))
    (o,) = g["elements"]["opening"]
    assert (o["width"], o["width_source"]) == (900.0, "default") and "width" in o["dims_assumed"]
    assert g["openings_dims_assumed"]["width"] == 1


def test_declared_mark_joins_an_unnamed_block_to_its_schedule_row(tmp_path):
    sched = [{"mark": "PW 18x22", "subtype": "window", "width": 1800.0, "height": 2200.0, "sill": 0.0, "count": 1}]
    g = _parse_blocks(*_unnamed(tmp_path, DECLARED), ext_schedule=sched)
    (o,) = g["elements"]["opening"]
    assert (o["height"], o["sill"], o["dims_source"]) == (2200.0, 0.0, "schedule") and "dims_assumed" not in o
    assert g["window_marks"] == [{"mark": "PW 18x22", "subtype": "window", "width": 1800.0,
                                  "height": None, "sill": None, "count": 1}]


def test_rows_already_placed_by_mark_do_not_open_wall_gaps(tmp_path):
    """평면이 부호로 자리를 말한 행은 끊김 매칭에 안 쓴다 — 같은 폭의 빈 끊김(통로)에 그 창이 한 번 더 서고 창대 벽이
    통로를 막았다. 평면에 없는 부호의 행은 종전대로 끊김과 맞춘다."""
    sched = [{"mark": "PW 18x22", "subtype": "window", "width": 1800.0, "height": 2200.0, "sill": 0.0, "count": 1}]
    g = _parse_blocks(*_unnamed(tmp_path, DECLARED), ext_schedule=sched)
    assert [o.get("source") for o in g["elements"]["opening"]] == [None]
    sched.append({"mark": "AW-1", "subtype": "window", "width": 1800.0, "height": 1500.0, "sill": 900.0, "count": 1})
    g = _parse_blocks(*_unnamed(tmp_path, DECLARED), ext_schedule=sched)
    assert sorted((o["mark"], o.get("source")) for o in g["elements"]["opening"]) == [
        ("AW-1", "wall_gap_match"), ("PW 18x22", None)]


def test_a_mark_in_a_layer_rule_is_refused(tmp_path):
    """부호는 부재 하나의 이름이다 — 레이어 규칙에 적으면 그 레이어의 선·블록 전부에 같은 부호가 찍힌다."""
    import pytest
    plan, blocks = _unnamed(tmp_path, DECLARED)
    lm = tmp_path / "lm.csv"
    lm.write_text("pattern,category,width,height,thickness,opts\nA-WIN$,opening,,,,mark=PW 18x22\n", encoding="utf-8")
    with pytest.raises(P.LayerMapError, match="block_map"):
        P.parse(plan, P.load_layer_map(str(lm)), blocks)


def test_hardware_circles_in_a_door_block_are_not_the_opening(tmp_path):
    """★ 블록 안의 원을 개구부로 돌려주던 갈래가 문 손잡이(r≈8)까지 개구부로 삼았고, 그 원은 조각으로 버려져 문이 통째로
    사라졌다(실측: 종합평면도 기준층 세대 현관문 6개 전부). 개구부일 수 있는 크기의 원만 원 그대로 쓴다 — 슬리브는 남는다."""
    doc = ezdxf.new(units=4)
    d = doc.blocks.new("XU$0$D_FSD1100")
    d.add_line((0, 0), (1100, 0))
    d.add_line((0, 0), (0, -1100))
    d.add_circle((950, -60), 8)
    s = doc.blocks.new("SLEEVE")
    s.add_circle((0, 0), 75)
    ms = doc.modelspace()
    for x0, x1 in [(0, 3000), (4100, 9000)]:
        for y in (0, 200):
            ms.add_line((x0, y), (x1, y), dxfattribs={"layer": "A-WALL"})
    ms.add_blockref("XU$0$D_FSD1100", (3000, 100), dxfattribs={"layer": "A-DOOR"})
    ms.add_blockref("SLEEVE", (6000, 100), dxfattribs={"layer": "A-DOOR"})
    p = tmp_path / "door.dxf"
    doc.saveas(p)
    bm = tmp_path / "bm.csv"
    bm.write_text("pattern,category,width,height,thickness,opts\n"
                  r"D_FSD1100$,opening,1100,,,subtype=door;mark=FSD 11x22" "\nSLEEVE,opening,,,,\n", encoding="utf-8")
    g = _parse_blocks(str(p), P.load_layer_map(str(bm)))
    ops = sorted(g["elements"]["opening"], key=lambda o: o["center"][0])
    assert [(o.get("mark"), o["center"], o["width"]) for o in ops] == [("FSD 11x22", [3550.0, 100.0], 1100.0), (None, [6000.0, 100.0], 150.0)]


def test_a_door_block_holding_several_leaf_positions_is_centred_along_its_threshold(tmp_path):
    """★ 동적 문 블록은 문짝을 여러 위치(30°·90°·180°)로 한 블록에 담는다 — 가시성 상태를 가를 정보가 DXF 에 없어 전체 범위가
    폭과 안 맞았고(700 문의 X 범위 1295), 폭과 맞는 문짝 쪽 축(벽에 수직)으로 240mm 옮겨져 벽을 떠났다(실측 동적 문 15개 전부).
    범위가 안 맞는 축은 폭 길이의 직선(문턱)의 가운데를 쓴다."""
    doc = ezdxf.new(units=4)
    d = doc.blocks.new("XU$0$DYNDOOR")
    d.add_line((0, 0), (700, 0))                                     # 문턱
    d.add_lwpolyline([(0, 0), (50, 0), (50, 150), (0, 150)], close=True)
    d.add_lwpolyline([(650, 0), (700, 0), (700, 150), (650, 150)], close=True)
    d.add_line((35, 0), (35, -630))                                  # 90° 문짝
    d.add_line((35, -30), (-595, -30))                               # 180° 문짝
    ms = doc.modelspace()
    for x0, x1 in [(0, 3000), (3700, 9000)]:
        for y in (0, 200):
            ms.add_line((x0, y), (x1, y), dxfattribs={"layer": "A-WALL"})
    ms.add_blockref("XU$0$DYNDOOR", (3000, 100), dxfattribs={"layer": "A-DOOR"})
    p = tmp_path / "dyn.dxf"
    doc.saveas(p)
    bm = tmp_path / "bm.csv"
    bm.write_text("pattern,category,width,height,thickness,opts\nDYNDOOR$,opening,700,,,subtype=door\n", encoding="utf-8")
    (o,) = _parse_blocks(str(p), P.load_layer_map(str(bm)))["elements"]["opening"]
    assert o["center"] == [3350.0, 100.0]


def test_a_wrapper_block_is_centred_from_the_window_block_inside_it():
    """창 블록을 감싼 블록(안에 INSERT 하나 + 폭 문자뿐)은 바로 아래 도형이 없어 중심 보정이 안 돌았다(실측 6개). 중첩 블록의 선으로
    중심을 정한다 — 개구부 레코드에는 넣지 않는다."""
    import tempfile
    doc = ezdxf.new(units=4)
    inner = doc.blocks.new("INNER_WIN")
    for y in (-100, 0, 100):
        inner.add_line((0, y), (900, y))
    wrap = doc.blocks.new("XU$0$WRAP")
    wrap.add_blockref("INNER_WIN", (0, 0))
    wrap.add_text("W:900", dxfattribs={"layer": "A-Defpoint"})
    ms = doc.modelspace()
    for x0, x1 in [(0, 3000), (3900, 9000)]:
        for y in (0, 200):
            ms.add_line((x0, y), (x1, y), dxfattribs={"layer": "A-WALL"})
    ms.add_blockref("XU$0$WRAP", (3000, 100), dxfattribs={"layer": "A-WIN"})
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "wrap.dxf"
        doc.saveas(p)
        bm = Path(d) / "bm.csv"
        bm.write_text("pattern,category,width,height,thickness,opts\nWRAP$,opening,900,,,subtype=window\n", encoding="utf-8")
        (o,) = _parse_blocks(str(p), P.load_layer_map(str(bm)))["elements"]["opening"]
    assert o["center"] == [3450.0, 100.0] and o["kind"] == "circle"


def test_a_window_symbol_drawn_from_one_wall_face_is_centred_in_the_wall_too():
    """★ 창 기호는 벽 두께를 가로질러 그려지고 기준점은 한쪽 면이다 — 폭 방향만 옮기면 벽 중심에서 떨어져 벽을 못 찾았고 창 자리가
    바닥부터 천장까지 뚫렸다(실측 부엌창 8개, 215~360mm). 깊이가 폭보다 충분히 작은 기호만 깊이 가운데로 — 문짝이 선 문은 그대로."""
    import tempfile
    doc = ezdxf.new(units=4)
    w = doc.blocks.new("XU$0$FACEWIN")
    for y in (0, 250, 500):                                # 기준점 = 창의 한 끝 · 한쪽 면, 깊이 500
        w.add_line((0, y), (1200, y))
    ms = doc.modelspace()
    for x0, x1 in [(0, 3000), (4200, 9000)]:
        for y in (-150, 150):                              # 300 벽, 중심 y=0
            ms.add_line((x0, y), (x1, y), dxfattribs={"layer": "A-WALL"})
    ms.add_blockref("XU$0$FACEWIN", (3000, -250), dxfattribs={"layer": "A-WIN"})
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "face.dxf"
        doc.saveas(p)
        bm = Path(d) / "bm.csv"
        bm.write_text("pattern,category,width,height,thickness,opts\nFACEWIN$,opening,1200,,,subtype=window\n", encoding="utf-8")
        g = _parse_blocks(str(p), P.load_layer_map(str(bm)))
    (o,) = g["elements"]["opening"]
    assert o["center"] == [3600.0, 0.0] and o["no_host_reason"] == "wall_open_at_this_span"
    assert {w["infill_part"] for w in g["elements"]["wall"] if w.get("infill_of") == o["eid"]} == {"below", "above"}
