# -*- coding: utf-8 -*-
"""geom_contract z 기준면 규약 테스트.

규약이 4곳에 흩어져 있다가 preview 가 슬래브를 '하단' 으로 해석해
보/슬래브가 한 두께 떠 보인 사고가 있었다. 규약을 여기서 고정한다.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import geom_contract as GC

P = {"wall": {"width": 200.0, "height": 2800.0},
     "column": {"height": 3000.0}, "slab": {"thickness": 200.0}}


def test_bottom_datum_categories():
    for cat in ("wall", "column", "zone", "opening"):
        assert GC.datum_of(cat) == "bottom", cat
        z0, z1 = GC.z_range(cat, {"z_base": 1000.0, "overrides": {"height": 500.0}}, P)
        assert (z0, z1) == (1000.0, 1500.0), f"{cat}: {z0},{z1}"


def test_slab_and_beam_are_top_datum():
    """슬래브/보는 z_base 가 '윗면'. 이걸 하단으로 착각한 게 D6 사고."""
    assert GC.datum_of("slab") == "top" and GC.datum_of("beam") == "top"
    assert GC.z_range("slab", {"z_base": 6050.0, "overrides": {"thickness": 200.0}}, P) \
        == (5850.0, 6050.0)
    assert GC.z_range("beam", {"z_base": 6050.0, "overrides": {"thickness": 1200.0}}, P) \
        == (4850.0, 6050.0)


def test_beam_top_meets_slab_top():
    """실무 규약: 콘크리트 보 상단 = 슬래브 상단, 춤은 슬래브 두께를 포함한다."""
    slab = GC.z_range("slab", {"z_base": 6050.0, "overrides": {"thickness": 200.0}}, P)
    beam = GC.z_range("beam", {"z_base": 6050.0, "overrides": {"thickness": 1200.0}}, P)
    assert slab[1] == beam[1], "보 상단과 슬래브 상단이 어긋난다"
    assert beam[0] < slab[0], "보가 슬래브 아래로 돌출되지 않는다"


def test_mep_axis_datum():
    """배관/덕트의 elevation 은 중심축이다(하단도 상단도 아님)."""
    assert GC.z_range("pipe", {"elevation": 2600.0, "diameter": 100.0}, P) == (2550.0, 2650.0)
    assert GC.z_range("duct", {"elevation": 2800.0, "height_mm": 300.0}, P) == (2650.0, 2950.0)
    assert GC.z_range("tray", {"elevation": 3000.0, "height_mm": 100.0}, P) == (2950.0, 3050.0)


def test_equipment_is_bottom():
    assert GC.z_range("equipment", {"elevation": 0.0}, P) == (0.0, 1000.0)


def test_mep_uses_elevation_not_z_base():
    """MEP 는 키가 다르다. 개명하면 cfd_export/boq_export 가 깨진다."""
    assert GC.base_z("pipe", {"elevation": 2600.0, "z_base": 999.0}) == 2600.0
    assert GC.base_z("wall", {"z_base": 100.0, "elevation": 999.0}) == 100.0


def test_dimension_precedence():
    """overrides > 레코드 최상위 > params > 기본값"""
    assert GC.z_range("slab", {"z_base": 1000.0, "thickness": 300.0,
                               "overrides": {"thickness": 500.0}}, P) == (500.0, 1000.0)
    assert GC.z_range("slab", {"z_base": 1000.0, "thickness": 300.0}, P) == (700.0, 1000.0)
    assert GC.z_range("slab", {"z_base": 1000.0}, P) == (800.0, 1000.0)


def test_unknown_category_raises():
    try:
        GC.z_range("bogus", {"z_base": 0}, P)
    except GC.ContractError:
        return
    raise AssertionError("알 수 없는 카테고리가 통과했다")


# ── 감김 정규화 ────────────────────────────────────────────────────────────
def test_ccw_normalizes_winding():
    """Arch.makeStructure 는 면 법선(=감김)으로 압출 방향이 결정된다.
    CW 폴리곤 352개가 한 두께씩 밀렸던 사고의 방어선."""
    sq = [(0, 0), (10, 0), (10, 10), (0, 10)]          # CCW
    assert GC.signed_area(sq) > 0
    assert GC.ccw(sq) == sq, "이미 CCW인데 뒤집었다"
    assert GC.ccw(list(reversed(sq))) == sq, "CW→CCW 반전 실패"
    assert GC.signed_area(GC.ccw(list(reversed(sq)))) > 0


def test_ccw_handles_degenerate():
    assert GC.ccw([]) == []
    assert GC.ccw([(0, 0), (1, 1)]) == [(0, 0), (1, 1)]


# ── 보 footprint (축선 + 폭 → 평면 사각형) ─────────────────────────────────
def test_beam_footprint_is_centred_on_the_axis():
    fp = GC.beam_footprint([0, 0], [1000, 0], 400)
    xs = sorted({round(p[0]) for p in fp})
    ys = sorted({round(p[1]) for p in fp})
    assert xs == [0, 1000] and ys == [-200, 200], fp
    assert GC.signed_area(fp) > 0, "footprint 가 CW — 압출이 -Z 로 간다"


def test_beam_footprint_follows_direction():
    """대각선 축선도 축에 수직으로 폭이 잡혀야 한다."""
    fp = GC.beam_footprint([0, 0], [0, 1000], 400)
    xs = sorted({round(p[0]) for p in fp})
    ys = sorted({round(p[1]) for p in fp})
    assert xs == [-200, 200] and ys == [0, 1000], fp


def test_beam_footprint_rejects_degenerate():
    assert GC.beam_footprint([0, 0], [0, 0], 400) is None
    assert GC.beam_footprint([0, 0], [1000, 0], 0) is None


def test_beam_rings_from_centreline_uses_schedule_width():
    """일람표가 준 overrides.width 가 footprint 폭이 된다."""
    rec = {"points": [[0, 0], [1000, 0]], "overrides": {"width": 400.0, "thickness": 1800.0}}
    rings = GC.beam_rings(rec, P)
    assert len(rings) == 1
    assert sorted({round(p[1]) for p in rings[0]}) == [-200, 200]


def test_beam_rings_one_per_segment():
    rec = {"points": [[0, 0], [1000, 0], [1000, 1000]], "overrides": {"width": 300.0}}
    assert len(GC.beam_rings(rec, P)) == 2


def test_beam_rings_closed_outline_passes_through():
    """외곽선으로 온 보는 footprint 가 이미 있다 — 축선 규칙을 적용하면 안 된다."""
    pts = [[0, 0], [1000, 0], [1000, 400], [0, 400]]
    assert GC.beam_rings({"closed": True, "points": pts}, P) == [pts]


def test_beam_solid_spans_downward_from_z_base():
    """보 상단 = z_base, 아래로 춤. footprint 와 z 규약이 함께 맞아야 솔리드가 맞다."""
    rec = {"z_base": 6050.0, "points": [[0, 0], [1000, 0]],
           "overrides": {"width": 400.0, "thickness": 1800.0}}
    z0, z1 = GC.z_range("beam", rec, P)
    assert (z0, z1) == (4250.0, 6050.0)
    ring = GC.beam_rings(rec, P)[0]
    assert sorted({round(p[1]) for p in ring}) == [-200, 200]


# ── 계약 블록 ──────────────────────────────────────────────────────────────
def test_contract_block_roundtrip():
    d = {"contract": GC.contract_block(), "elements": {}}
    assert GC.check_contract(d) == []


def test_contract_detects_version_drift():
    d = {"contract": {"version": 999, "z_datum": {}}, "elements": {}}
    assert GC.check_contract(d), "버전 불일치를 못 잡았다"


def test_contract_detects_datum_drift():
    c = GC.contract_block()
    c["z_datum"]["slab"] = "bottom"          # 과거 해석으로 되돌린 파일
    assert GC.check_contract({"contract": c, "elements": {}})


def test_contract_detects_unknown_bucket():
    d = {"contract": GC.contract_block(), "elements": {"bogus": [{}]}}
    assert GC.check_contract(d)


# ── JS 주입 ────────────────────────────────────────────────────────────────
def test_js_constants_carry_the_same_datums():
    """preview 는 import 가 안 되므로 주입된 상수가 파이썬과 같아야 한다."""
    js = GC.js_constants()
    for cat, datum in GC.Z_DATUM.items():
        assert f'"{cat}": "{datum}"' in js, f"{cat} 규약이 JS 에 안 실렸다"
    assert "gcZRange" in js and "gcDim" in js


def test_js_carries_the_beam_footprint_rule_too():
    """preview 가 보를 그리려면 축선→footprint 규칙도 같아야 한다.
    파이썬에만 두면 preview 가 자기 식을 다시 쓰게 되고, 그게 D6 이었다."""
    js = GC.js_constants()
    assert "gcBeamRings" in js and "gcCcw" in js
