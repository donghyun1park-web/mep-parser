# -*- coding: utf-8 -*-
"""부재일람표(schedule_table) 테스트.

고정하려는 실패들:
  · 부재명→단면 매핑을 사람이 읽어 스크립트에 하드코딩하던 것(층마다 반복, 검증 없음).
  · 한 레이어의 표 여러 개를 단일 표로 병합해 헤더 행이 데이터가 되는 것.
  · 'H 800x300'(춤×폭) 과 '350x1100'(폭×춤) 을 같은 규칙으로 읽어 납작한 보를 만드는 것.
  · 일람표에 없는 부재를 조용한 기본값으로 때우는 것.
"""
import os
import sys

import ezdxf

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import schedule_table as ST


# ── 합성 도면 만들기 ───────────────────────────────────────────────────────
def make_msp(rows, layer="SCH", height=600.0, x0=0.0, y0=0.0, col_pitch=5000.0,
             row_pitch=1200.0):
    """rows = [[셀, 셀, ...], ...] → 중앙정렬 TEXT 격자. None 셀은 비운다.

    실제 도면과 같이 halign/valign 을 설정하고 align_point 를 앵커로 쓴다.
    insert 는 일부러 문자열 길이에 따라 좌우로 흔들어 둔다 — insert 기준으로
    열을 묶으면 깨지고 align_point 기준이면 안 깨지는 것을 검증하기 위함이다.
    """
    doc = ezdxf.new()
    msp = doc.modelspace()
    for ri, row in enumerate(rows):
        y = y0 - ri * row_pitch
        for ci, cell in enumerate(row):
            if cell is None:
                continue
            txt, h = (cell if isinstance(cell, tuple) else (cell, height))
            cx = x0 + ci * col_pitch
            t = msp.add_text(txt, dxfattribs={"layer": layer, "height": h})
            t.dxf.halign, t.dxf.valign = 1, 2          # CENTER / MIDDLE
            t.dxf.align_point = (cx, y)
            t.dxf.insert = (cx - len(txt) * h * 0.3, y)   # 일부러 흔든 값
    return msp


# ── 단면 표기 파싱 ─────────────────────────────────────────────────────────
def test_h_prefix_is_depth_first():
    """'H 800x300x14/26' = 춤 800 × 폭 300. 뒤집으면 납작한 보가 된다."""
    s = ST.parse_section("H 800x300x14/26")
    assert (s["h"], s["b"]) == (800.0, 300.0), s
    assert s["notation"] == "DxB" and s["shape"] == "H"
    assert (s["tw"], s["tf"]) == (14.0, 26.0)


def test_bare_numeric_is_width_first():
    """'350x1100x8x8' = 폭 350 × 춤 1100 (조립보 표기)."""
    s = ST.parse_section("350x1100x8x8")
    assert (s["b"], s["h"]) == (350.0, 1100.0), s
    assert s["notation"] == "BxD" and s["shape"] is None


def test_two_notations_disagree_on_the_same_numbers():
    """같은 숫자쌍이라도 프리픽스 유무로 의미가 뒤집힌다 — 이게 D3 의 핵심 함정."""
    a = ST.parse_section("H 350x1100x8/8")
    b = ST.parse_section("350x1100x8x8")
    assert (a["h"], a["b"]) == (350.0, 1100.0)
    assert (b["h"], b["b"]) == (1100.0, 350.0)


def test_builtup_and_decimals():
    assert ST.parse_section("BH 900x350x16x30")["h"] == 900.0
    s = ST.parse_section("H 200x100x5.5/8")
    assert (s["h"], s["b"], s["tw"]) == (200.0, 100.0, 5.5)


def test_three_number_plate_girder():
    s = ST.parse_section("320x800x8")
    assert (s["b"], s["h"], s["tw"]) == (320.0, 800.0, 8.0)
    assert "tf" not in s


def test_composite_src_section():
    """'1200x1200 / BH 500x500x15x25' — 바깥 RC 가 실제 부재 크기, 철골은 심."""
    s = ST.parse_section("1200x1200 / BH 500x500x15x25")
    assert (s["b"], s["h"]) == (1200.0, 1200.0)
    assert s["core"]["shape"] == "BH" and s["core"]["h"] == 500.0


def test_slash_inside_thickness_is_not_a_composite():
    """'12/17' 의 슬래시는 복합단면 구분자가 아니다(공백 없는 슬래시)."""
    s = ST.parse_section("H 582x300x12/17")
    assert "core" not in s and s["tf"] == 17.0


def test_unreadable_sizes_return_none_not_a_guess():
    """추측해서 기본값을 만들지 않는다 — 조용히 틀린 치수가 가장 비싸다."""
    for bad in ("", "-", "Ø500", "TBD", None, "H"):
        assert ST.parse_section(bad) is None, bad


def test_wide_flag_marks_possible_notation_flip():
    assert ST.parse_section("1100x350x8x8")["wide"] is True
    assert ST.parse_section("350x1100x8x8")["wide"] is False


# ── 표 복원 ────────────────────────────────────────────────────────────────
HDR = ["NAME", "SIZE", "MATERIAL"]


def test_single_table_roundtrip():
    msp = make_msp([[("STEEL BEAM MEMBER LIST", 700.0)],
                    HDR,
                    ["RSB1", "H 800x300x14/26", "SM355"],
                    ["RSB2", "H 792x300x14/22", "SM355"]])
    t = ST.extract_tables(msp, "SCH")
    assert len(t) == 1 and len(t[0]["rows"]) == 2, t
    assert t[0]["kind"] == "beam"
    assert t[0]["roles"]["name"] == 0 and t[0]["roles"]["size"] == 1
    assert not t[0]["guessed_roles"], "라벨을 읽었는데 추정으로 떨어졌다"


def test_multiple_tables_keep_their_own_headers():
    """한 레이어에 표가 여러 개다. 단일 표로 병합하면 헤더가 데이터가 된다."""
    msp = make_msp([[("STEEL BEAM MEMBER LIST", 700.0)],
                    HDR,
                    ["RSB1", "H 800x300x14/26", "SM355"],
                    [("AU BEAM MEMBER LIST", 700.0)],
                    ["NAME", "TYPE", "SIZE"],
                    ["RAG11B", "C", "400x1800x25x25"]])
    ts = ST.extract_tables(msp, "SCH")
    assert len(ts) == 2, [t["title"] for t in ts]
    assert [c["label"] for c in ts[1]["columns"]] == ["NAME", "TYPE", "SIZE"]
    idx, warns = ST.build_member_index(ts)
    assert set(idx) == {"RSB1", "RAG11B"}, idx
    assert idx["RAG11B"]["section"]["h"] == 1800.0
    assert not any("NAME" in k for k in idx), "헤더 행이 부재로 들어갔다"


def test_repeat_header_without_title_starts_a_new_table():
    """제목 없이 헤더만 반복되는 연속 표도 데이터로 오염되면 안 된다."""
    msp = make_msp([HDR,
                    ["RSB1", "H 800x300x14/26", "SM355"],
                    HDR,
                    ["RSB2", "H 792x300x14/22", "SM355"]])
    ts = ST.extract_tables(msp, "SCH")
    assert sum(len(t["rows"]) for t in ts) == 2
    idx, _ = ST.build_member_index(ts)
    assert set(idx) == {"RSB1", "RSB2"}, idx


def test_columns_track_anchor_not_text_length():
    """중앙정렬 텍스트는 길이에 따라 insert 가 흔들린다. 앵커를 써야 열이 안 섞인다."""
    msp = make_msp([HDR,
                    ["A1", "1200x1200 / BH 500x500x15x25", "SM355"],
                    ["A2", "H 200x100x5.5/8", "SS275"]])
    idx, _ = ST.build_member_index(ST.extract_tables(msp, "SCH"))
    assert idx["A1"]["material"] == "SM355", idx["A1"]
    assert idx["A1"]["section"]["b"] == 1200.0
    assert idx["A2"]["material"] == "SS275"


def test_empty_layer_yields_no_tables():
    assert ST.extract_tables(make_msp([HDR], layer="OTHER"), "SCH") == []


# ── 인덱스 / 충돌 ──────────────────────────────────────────────────────────
def test_same_name_same_size_across_tables_is_not_a_conflict():
    """SB0 처럼 층마다 같은 값으로 반복 등장하는 것은 정상이다."""
    msp = make_msp([[("LIST A", 700.0)], HDR, ["SB0", "H 200x100x5.5/8", "SS275"],
                    [("LIST B", 700.0)], HDR, ["SB0", "H 200x100x5.5/8", "SS275"]])
    idx, warns = ST.build_member_index(ST.extract_tables(msp, "SCH"))
    assert len(idx) == 1 and not warns, warns


def test_same_name_different_size_is_reported_not_overwritten():
    msp = make_msp([[("LIST A", 700.0)], HDR, ["SB0", "H 200x100x5.5/8", "SS275"],
                    [("LIST B", 700.0)], HDR, ["SB0", "H 300x150x6.5/9", "SS275"]])
    idx, warns = ST.build_member_index(ST.extract_tables(msp, "SCH"))
    assert warns and "SB0" in warns[0], warns
    assert idx["SB0"]["section"]["h"] == 200.0, "뒤엣것이 조용히 덮어썼다"
    assert idx["SB0"].get("conflict") is True


# ── 레코드 조인 ────────────────────────────────────────────────────────────
def _index(rows):
    return ST.build_member_index(ST.extract_tables(make_msp([HDR] + rows), "SCH"))[0]


def test_join_sets_width_and_depth():
    idx = _index([["RAG11B", "400x1800x25x25", "SM355"]])
    els = {"beam": [{"member_name": "RAG11B", "kind": "polyline",
                     "points": [[0, 0], [1000, 0]]}]}
    st = ST.join_members(els, idx)
    r = els["beam"][0]
    assert st["matched"] == 1 and st["unmatched"] == 0
    assert r["overrides"] == {"width": 400.0, "thickness": 1800.0}, r["overrides"]
    assert r["section"]["name"] == "RAG11B" and r["schedule_match"] == "ok"


def test_schedule_beats_layer_default():
    """레이어 치수는 '이 레이어 전부' 용 폴백, 일람표는 부재별 실제값이다."""
    idx = _index([["RAG1", "350x1100x8x8", "SM355"]])
    els = {"beam": [{"member_name": "RAG1",
                     "overrides": {"width": 300.0, "thickness": 600.0}}]}
    ST.join_members(els, idx)
    assert els["beam"][0]["overrides"] == {"width": 350.0, "thickness": 1100.0}


def test_unmatched_member_is_flagged_never_defaulted():
    idx = _index([["RAG1", "350x1100x8x8", "SM355"]])
    els = {"beam": [{"member_name": "RAG9"}]}
    st = ST.join_members(els, idx)
    r = els["beam"][0]
    assert st["unmatched"] == 1 and st["names_unmatched"] == {"RAG9": 1}
    assert r["needs_review"] is True and r["schedule_match"] == "unmatched"
    assert "overrides" not in r, "치수를 모르는데 기본값을 넣었다"


def test_unparsable_size_is_flagged_too():
    idx = _index([["RAG1", "TBD", "SM355"]])
    els = {"beam": [{"member_name": "RAG1"}]}
    st = ST.join_members(els, idx)
    assert st["unparsed"] == 1 and els["beam"][0]["needs_review"] is True
    assert els["beam"][0]["schedule_match"] == "size_unparsed"


def test_records_without_member_name_are_untouched():
    """일람표 조인은 이름 있는 레코드만 건드린다(같은 레이어의 외곽선 등은 그대로)."""
    idx = _index([["RAG1", "350x1100x8x8", "SM355"]])
    els = {"beam": [{"kind": "polyline", "points": [[0, 0], [1, 1]]}]}
    st = ST.join_members(els, idx)
    assert st == {"matched": 0, "unmatched": 0, "unparsed": 0,
                  "names_unmatched": {}, "warnings": []}
    assert els["beam"][0] == {"kind": "polyline", "points": [[0, 0], [1, 1]]}


def test_name_matching_ignores_spacing_and_case():
    idx = _index([["RAG 1A", "350x1200x8x8", "SM355"]])
    els = {"beam": [{"member_name": "rag1a"}]}
    assert ST.join_members(els, idx)["matched"] == 1


def test_wide_section_on_a_beam_warns():
    idx = _index([["RAG1", "1100x350x8x8", "SM355"]])
    els = {"beam": [{"member_name": "RAG1"}]}
    st = ST.join_members(els, idx)
    assert st["warnings"] and "폭>춤" in st["warnings"][0], st["warnings"]


def test_wide_section_on_a_column_does_not_warn():
    """기둥은 폭≥춤이 정상이다. 경고를 남발하면 아무도 안 읽는다."""
    msp = make_msp([[("SRC COLUMN MEMBER LIST", 700.0)], ["NAME", "SIZE"],
                    ["SRC1", "1400x1200 / BH 700x700x25x40"]])
    idx, _ = ST.build_member_index(ST.extract_tables(msp, "SCH"))
    els = {"column": [{"member_name": "SRC1"}]}
    st = ST.join_members(els, idx)
    assert st["matched"] == 1 and not st["warnings"], st["warnings"]
