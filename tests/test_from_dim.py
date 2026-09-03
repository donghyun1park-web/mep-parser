# -*- coding: utf-8 -*-
"""`opts: from=dim` — DIMENSION 을 부재 축선으로 쓰는 레이어의 동작.

실무 구조도면은 부재를 '치수선으로 긋고 텍스트를 부재명으로 덮어쓰는' 관행이
있다. 그런데 같은 레이어에 그 치수를 그리기 위한 부속(보조선 LINE, 화살표
INSERT)도 함께 있다 — 실측 도면에서 부재 225개 옆에 부속 694개.
이걸 안 걸러내면 길이 500mm 짜리 화살촉이 보로 세워진다.
"""
import contextlib
import io
import os
import sys
import tempfile

import ezdxf

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dxf_parser as dp

RULES = [("^BEAM$", "beam", {"_opts": {"from": "dim", "member_re": "^R[AS]"}})]


def _make_dxf():
    """부재 2개 = DIMENSION 2 + 보조선 LINE 2 + 화살표 INSERT 2, 전부 같은 레이어."""
    doc = ezdxf.new(setup=True)
    doc.header["$INSUNITS"] = 4          # mm — 구조도면 관례(=6 이면 파서가 x1000 한다)
    msp = doc.modelspace()
    blk = doc.blocks.new(name="ARROW")
    blk.add_lwpolyline([(0, 0), (100, 50), (100, -50)], close=True)

    for i, (name, a, b) in enumerate((("RAG1", (0, 0), (6000, 0)),
                                      ("RSB2", (0, 3000), (4000, 3000)))):
        d = msp.add_linear_dim(base=(0, a[1] + 500), p1=a, p2=b,
                               text=name, dxfattribs={"layer": "BEAM"})
        d.render()
        # 치수 장식: 보조선 + 화살표 (실도면에서 부재당 3~4개씩 붙어 있다)
        msp.add_line(a, (a[0], a[1] + 1000), dxfattribs={"layer": "BEAM"})
        msp.add_blockref("ARROW", a, dxfattribs={"layer": "BEAM"})
        msp.add_blockref("ARROW", b, dxfattribs={"layer": "BEAM"})

    # 진짜 치수선(측정값 표시) — 부재가 아니다
    msp.add_linear_dim(base=(0, -1500), p1=(0, -1000), p2=(6000, -1000),
                       dxfattribs={"layer": "BEAM"}).render()
    p = os.path.join(tempfile.gettempdir(), "_from_dim_test.dxf")
    doc.saveas(p)
    return p


def _parse(rules=None):
    with contextlib.redirect_stdout(io.StringIO()):     # 파서 진행 로그는 테스트 출력에서 뺀다
        return dp.parse(_make_dxf(), rules or RULES, block_rules=[])


# ── 부재 추출 ──────────────────────────────────────────────────────────────
def test_dimension_becomes_a_member_with_its_name():
    d = _parse()
    beams = d["elements"]["beam"]
    assert sorted(b["member_name"] for b in beams) == ["RAG1", "RSB2"], beams
    assert all(b["source"] == "dimension" for b in beams)


def test_member_endpoints_come_from_the_measured_points():
    """defpoint(10)는 치수선이 그려질 위치일 뿐이다. 측정 구간은 defpoint2→defpoint3."""
    d = _parse()
    rag = [b for b in d["elements"]["beam"] if b["member_name"] == "RAG1"][0]
    (x0, y0), (x1, y1) = rag["points"]
    assert abs(x1 - x0) == 6000 and y0 == y1 == 0, rag["points"]


def test_real_dimension_lines_are_skipped_not_turned_into_members():
    d = _parse()
    assert sum(d.get("dimension_skipped", {}).values()) == 1


# ── ★ 치수 장식 배제 ───────────────────────────────────────────────────────
def test_non_dimension_entities_on_a_from_dim_layer_are_not_members():
    """from 은 '형상 출처'다. dim 이면 그 레이어 형상은 DIMENSION 에서만 온다.

    이걸 안 걸러내던 시절: 실측 도면에서 보 레코드 915개 중 690개가
    보조선·화살촉이었다. 레코드일 땐 잡음이지만 build_beams 가 생기면
    모델 안에 실제 쓰레기 솔리드가 된다."""
    d = _parse()
    beams = d["elements"]["beam"]
    assert len(beams) == 2, [b.get("member_name") for b in beams]
    assert all(b.get("member_name") for b in beams)


def test_skipped_decoration_is_counted_not_silently_dropped():
    d = _parse()
    dec = d.get("dimension_decoration_skipped") or {}
    # 보조선 2 + 화살표 INSERT 4 = 6
    assert dec.get("BEAM") == 6, dec


def test_decoration_does_not_become_unhandled_warnings():
    """부재당 3~4개씩 경고를 내면 진짜 경고가 묻힌다."""
    d = _parse()
    assert not [w for w in d["warnings"] if "unhandled" in w], d["warnings"]


def test_member_re_narrows_what_counts_as_a_member():
    """일반 가드로는 부재명(RAB1D)과 상세도 기호(Lt, ta)를 구분할 수 없다."""
    d = _parse([("^BEAM$", "beam", {"_opts": {"from": "dim", "member_re": "^RAG"}})])
    assert [b["member_name"] for b in d["elements"]["beam"]] == ["RAG1"]


def test_without_from_dim_the_layer_behaves_as_before():
    """옵트인이다 — 다른 도면의 진짜 치수선이 부재로 둔갑하지 않는다."""
    d = _parse([("^BEAM$", "beam", {})])
    assert not any(b.get("member_name") for b in d["elements"].get("beam", []))
    assert "dimension_decoration_skipped" not in d
