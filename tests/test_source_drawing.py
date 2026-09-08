import os
import sys

import ezdxf

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import source_drawing as SD
import dxf_parser as DP
from element_id import element_eid


def _write(path, units=4):
    doc = ezdxf.new(units=units)
    return doc, doc.modelspace()


def test_reads_actual_source_geometry_in_mm_instead_of_model_centerline(tmp_path):
    path = tmp_path / "plan.dxf"
    doc, msp = _write(path, units=6)
    msp.add_line((1, 2), (3, 2), dxfattribs={"layer": "WALL-FACE"})
    doc.saveas(path)
    geometry = {"elements": {"wall": [{"centerline": [[100, 200], [200, 200]]}]}}

    out = SD.build_source_drawing([{"id": "main", "path": str(path)}], geometry)

    assert out["units"] == "mm" and out["status"] == "available"
    floor = out["floors"][0]
    assert floor["name"] == "plan.dxf" and floor["bbox"] == [1000.0, 2000.0, 3000.0, 2000.0]
    assert floor["primitives"][0]["points"] == [[1000.0, 2000.0], [3000.0, 2000.0]]


def test_same_drawing_uses_resolved_stack_offsets_without_cross_floor_mixing(tmp_path):
    path = tmp_path / "same.dxf"
    doc, msp = _write(path)
    msp.add_circle((10, 20), 5)
    doc.saveas(path)
    sources = [{"id": "L1", "path": str(path), "z": 0, "offset": [999, 999]},
               {"id": "L2", "path": str(path), "z": 4000, "offset": [999, 999]}]
    geometry = {"stack": {"levels": [
        {"id": "L1", "z": 0, "offset": [0, 0]},
        {"id": "L2", "z": 4200, "offset": [100, 300]},
    ]}}

    out = SD.build_source_drawing(sources, geometry)

    a, b = out["floors"]
    assert (a["z"], a["offset"], a["primitives"][0]["center"]) == (0.0, [0.0, 0.0], [10.0, 20.0])
    assert (b["z"], b["offset"], b["primitives"][0]["center"]) == (4200.0, [100.0, 300.0], [110.0, 320.0])
    assert a["primitives"] is not b["primitives"]


def test_missing_and_unsupported_sources_are_explicit(tmp_path):
    path = tmp_path / "mostly-text.dxf"
    doc, msp = _write(path)
    msp.add_text("note")
    doc.saveas(path)

    out = SD.build_source_drawing([
        {"id": "missing", "path": str(tmp_path / "secret" / "absent.dxf")},
        {"id": "text", "path": str(path)},
    ], {})

    assert out["status"] == "partial"
    assert out["floors"][0]["status"] == "unavailable"
    assert out["floors"][0]["name"] == "absent.dxf"
    assert out["floors"][1]["status"] == "partial"
    assert out["floors"][1]["omitted"] == {"TEXT": 1}


def test_bulge_arc_spline_and_nested_insert_are_sampled_as_source_curves(tmp_path):
    path = tmp_path / "curves.dxf"
    doc, msp = _write(path)
    inner = doc.blocks.new("INNER")
    inner.add_line((0, 0), (10, 0), dxfattribs={"layer": "INNER-LAYER"})
    outer = doc.blocks.new("OUTER")
    outer.add_blockref("INNER", (5, 7))
    msp.add_blockref("OUTER", (100, 200))
    msp.add_lwpolyline([(0, 0, 1), (10, 0, 0)], format="xyb")
    msp.add_arc((20, 20), 10, 0, 90)
    msp.add_spline(fit_points=[(30, 0), (35, 10), (40, 0)])
    doc.saveas(path)

    floor = SD.build_source_drawing([{"id": "main", "path": str(path)}], {})["floors"][0]

    polylines = [p for p in floor["primitives"] if p["kind"] == "polyline"]
    assert any(len(p["points"]) > 2 for p in polylines)  # curves were flattened, not control polygons
    assert any(p["points"] == [[105.0, 207.0], [115.0, 207.0]] for p in polylines)
    assert not floor["omitted"]


def test_output_is_bounded_and_reports_truncation(tmp_path, monkeypatch):
    path = tmp_path / "large.dxf"
    doc, msp = _write(path)
    for x in range(5):
        msp.add_line((x, 0), (x, 1))
    doc.saveas(path)
    monkeypatch.setattr(SD, "MAX_PRIMITIVES", 2)

    floor = SD.build_source_drawing([{"id": "main", "path": str(path)}], {})["floors"][0]

    assert len(floor["primitives"]) == 2 and floor["omitted"]["TRUNCATED"] == 1
    assert floor["status"] == "partial" and any("limit" in w.lower() for w in floor["warnings"])
    assert any("uncounted" in w for w in floor["warnings"])


def test_unsafe_curve_approximations_are_explicitly_omitted(tmp_path):
    path = tmp_path / "ocs.dxf"
    doc, msp = _write(path)
    poly = msp.add_polyline2d([(0, 0), (10, 0)])
    poly.vertices[0].dxf.bulge = 1
    msp.add_circle((20, 20), 10, dxfattribs={"extrusion": (0, 1, 1)})
    msp.add_circle((20, 20), 10, dxfattribs={"extrusion": (0, 0, -1)})
    doc.saveas(path)
    floor = SD.build_source_drawing([{"path": str(path)}], {})["floors"][0]
    assert floor["omitted"] == {"CIRCLE": 1, "POLYLINE": 1}
    assert floor["primitives"][0]["center"] == [-20.0, 20.0]
    assert floor["status"] == "partial"


def test_signature_preserved_before_display_rounding():
    doc = ezdxf.new()
    line = doc.modelspace().add_line((0.5001, 0), (10, 0))
    expected = DP.entity_to_record(line, 1)["_sigs"][0]
    assert SD._record(line, 1)["signature"] == expected


def test_spline_sampling_is_bounded_without_false_signature():
    import pytest
    from types import SimpleNamespace
    class Curve:
        closed = False
        dxf = SimpleNamespace(layer="0")
        def dxftype(self): return "SPLINE"
        def flattening(self, *args, **kwargs):
            for x in range(100):
                if x > 3: raise AssertionError("unbounded sampling")
                yield SimpleNamespace(x=x, y=0)
    with pytest.raises(SD._BudgetExceeded):
        SD._record(Curve(), 1, 3)


def test_bad_entity_and_bad_floor_do_not_discard_other_source(tmp_path, monkeypatch):
    path = tmp_path / "valid.dxf"
    doc, msp = _write(path)
    msp.add_line((0, 0), (1, 0))
    msp.add_line((1, 0), (2, 0))
    doc.saveas(path)
    original = SD.entity_to_record
    def fail_one(entity, scale):
        if entity.dxf.start.x == 0: raise ValueError("bad entity")
        return original(entity, scale)
    monkeypatch.setattr(SD, "entity_to_record", fail_one)
    result = SD.build_source_drawing([
        {"id": "bad", "path": str(path), "offset": [float("nan"), 0]},
        {"id": "good", "path": str(path)},
    ], {})
    assert result["floors"][0]["status"] == "unavailable"
    good = result["floors"][1]
    assert len(good["primitives"]) == 1 and good["omitted"] == {"LINE_ERROR": 1}


def test_minsert_expansion_is_lazy():
    class Insert:
        mcount = 100
        def dxftype(self): return "INSERT"
        def multi_insert(self):
            yield self
            raise AssertionError("eager expansion")
        def virtual_entities(self, **kwargs): yield "first"
    # A simple entity stub avoids using string omission markers.
    from types import SimpleNamespace
    leaf = SimpleNamespace(dxftype=lambda: "LINE")
    Insert.virtual_entities = lambda self, **kwargs: iter([leaf])
    assert next(SD._entities(Insert())) is leaf


def test_insert_skips_and_empty_blocks_are_disclosed():
    from collections import Counter
    from types import SimpleNamespace
    class Insert:
        mcount = 1
        def dxftype(self): return "INSERT"
        def virtual_entities(self, skipped_entity_callback):
            skipped_entity_callback(SimpleNamespace(dxftype=lambda: "HATCH"), "unsupported")
            return iter([])
    omitted = Counter()
    assert list(SD._entities(Insert(), omitted=omitted)) == []
    assert omitted == {"HATCH_TRANSFORM": 1, "INSERT_EMPTY": 1}


def test_cap_stops_conversion_and_reports_uncounted_remainder(tmp_path, monkeypatch):
    path = tmp_path / "cap.dxf"
    doc, msp = _write(path)
    for x in range(10): msp.add_line((x, 0), (x, 1))
    doc.saveas(path)
    monkeypatch.setattr(SD, "MAX_PRIMITIVES", 1)
    original = SD.entity_to_record
    calls = []
    def counted(entity, scale):
        calls.append(entity)
        return original(entity, scale)
    monkeypatch.setattr(SD, "entity_to_record", counted)
    floor = SD.build_source_drawing([{"path": str(path)}], {})["floors"][0]
    assert len(calls) == 1 and floor["omitted"] == {"TRUNCATED": 1}


def test_nonfinite_entity_is_rejected_and_spline_has_no_signature():
    import pytest
    doc = ezdxf.new()
    line = doc.modelspace().add_line((float("nan"), 0), (1, 0))
    with pytest.raises((ValueError, OverflowError)):
        SD._record(line, 1)
    spline = doc.modelspace().add_spline(fit_points=[(0, 0), (5, 10), (10, 0)])
    assert "signature" not in SD._record(spline, 1)


def test_parser_exposes_eid_source_signatures_without_changing_eid(tmp_path):
    path = tmp_path / "wall.dxf"
    doc, msp = _write(path)
    msp.add_line((0, 0), (1000, 0), dxfattribs={"layer": "WALL"})
    doc.saveas(path)

    result = DP.parse(str(path), [(r"^WALL$", "wall", {})], block_rules=[])
    wall = result["elements"]["wall"][0]

    assert wall["source_signatures"]
    # Walls can add an internal span signature; the legacy raw-only EID proves
    # the exported source list is the same input that was already used.
    assert wall["eid_v1"] == element_eid("w", wall["source_signatures"])
