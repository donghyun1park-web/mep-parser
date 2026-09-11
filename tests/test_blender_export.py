"""Blender preparation and export guards. These tests never launch native apps."""
import copy
import hashlib
import json
import math
import struct
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import blender_builder as BB
import blender_runner as BR
import blender_verify as BV


def source():
    return {"units": "mm", "params": {}, "elements": {
        "pipe": [{"eid": "pipe:generic", "points": [[12000, -7000], [15000, -7000], [15000, -5000]],
                  "diameter": 19.0, "elevation": 91.5,
                  "source_refs": [{"handle": "ABC"}], "source_length_mm": 5000}],
        "duct": [{"eid": "duct:generic", "points": [[12500, -4000], [14000, -4000], [14000, -3000]],
                  "width_mm": 237, "height_mm": 83, "elevation": 2518.5}],
    }}


def test_pipe_source_path_and_contract_z_preserved():
    data = source()
    payload = BB.prepare_payload(data)
    pipe = next(o for o in payload["objects"] if o["eid"] == "pipe:generic")
    ox, oy, oz = payload["origin_mm"]
    assert pipe["kind"] == "curve" and pipe["radius_m"] == 0.0095
    assert pipe["z_bounds_mm"] == [82.0, 101.0]
    assert [[p[0] * 1000 + ox, p[1] * 1000 + oy] for p in pipe["points_m"]] == data["elements"]["pipe"][0]["points"]
    assert pipe["source_refs"] == [{"handle": "ABC"}]
    assert data == source(), "Export preparation must not mutate the source contract"


def test_canonical_source_context_and_material_override_survive_payload():
    data = source()
    rec = data['elements']['pipe'][0]
    rec.update(layer='H-FLR-P',source_layers=['H-FLR-P','H-FLR-ARC'],region_id='apartment_variant',material='PB')
    rec['overrides'] = {'material':'PB selected product'}
    obj = BB.prepare_payload(data)['objects'][0]
    assert obj['layer'] == 'H-FLR-P'
    assert obj['source_layers'] == ['H-FLR-P','H-FLR-ARC']
    assert obj['region_id'] == 'apartment_variant'
    assert obj['material'] == 'PB selected product'


def test_architecture_not_invented_from_coil_bounds():
    payload = BB.prepare_payload(source())
    assert not any(o["category"] in ("wall", "slab", "floor_layer") for o in payload["objects"])
    assert any(d["code"] == "floor_footprint_not_available" for d in payload["diagnostics"])


def test_rectangular_footprint_with_hole_stays_closed_and_preserves_volume():
    data = {"units": "mm", "elements": {"duct": [{"eid": "outline", "closed": True,
        "geometry_mode": "footprint", "points": [[0, 0], [500, 0], [1000, 0], [1000, 800], [0, 800]],
        "holes": [[[200, 200], [200, 400], [400, 400], [400, 200]]],
        "width_mm": 200, "height_mm": 80, "elevation": 2200}]}}
    obj = BB.prepare_payload(data)["objects"][0]
    BV.validate_mesh_payload(obj)
    assert obj["expected_volume_m3"] == pytest.approx((1000 * 800 - 200 * 200) * 80 / 1e9)
    bad = copy.deepcopy(obj)
    bad["faces"].pop()
    with pytest.raises(ValueError, match="closed"):
        BV.validate_mesh_payload(bad)


def test_closed_duct_centerline_sweeps_ring_instead_of_filling_interior():
    data = {'units':'mm', 'elements':{'duct':[{'eid':'closed:centerline','closed':True,
        'points':[[0,0],[1000,0],[1000,1000],[0,1000]],'width_mm':100,'height_mm':60,'elevation':2500}]}}
    obj = BB.prepare_payload(data)['objects'][0]
    assert obj['expected_volume_m3'] == pytest.approx((1100**2-900**2)*60/1e9)
    assert obj['geometry_method'] == 'continuous_plan_mitre_sweep'
    BV.validate_mesh_payload(obj)


def test_translated_rotated_source_keeps_length_and_section():
    data = source()
    theta = math.radians(23)
    transformed = copy.deepcopy(data)
    for recs in transformed["elements"].values():
        for rec in recs:
            rec["points"] = [[x * math.cos(theta) - y * math.sin(theta) + 9e6,
                              x * math.sin(theta) + y * math.cos(theta) - 8e6] for x, y in rec["points"]]
    first, second = BB.prepare_payload(data), BB.prepare_payload(transformed)
    a, b = first["objects"][0], second["objects"][0]
    length = lambda obj: sum(math.dist(p, q) for p, q in zip(obj["points_m"], obj["points_m"][1:]))
    assert length(a) == pytest.approx(length(b), abs=1e-9)
    assert a["radius_m"] == b["radius_m"]


def test_floor_layers_require_explicit_source_slab_and_thicknesses():
    data = source()
    data["elements"]["slab"] = [{"eid": "slab:source", "closed": True,
        "points": [[11000, -8000], [16000, -8000], [16000, -2000], [11000, -2000]],
        "z_base": 250, "thickness": 210}]
    data["mep_profile"] = {"floor_layers": [{"name": "resilient", "thickness_mm": 23},
        {"name": "foam", "thickness_mm": 44}, {"name": "screed", "thickness_mm": 38}]}
    payload = BB.prepare_payload(data)
    layers = [o for o in payload["objects"] if o["category"] == "floor_layer"]
    assert [o["z_bounds_mm"] for o in layers] == [[250, 273], [273, 317], [317, 355]]
    assert layers[-1]["covered_only"] is True
    assert all(o["source_eid"] == "slab:source" for o in layers)


@pytest.mark.parametrize("change", ["nan", "duplicate_eid", "unknown_units", "zero_diameter", "missing_eid"])
def test_payload_fails_closed_for_invalid_source(change):
    data = source()
    if change == "nan": data["elements"]["pipe"][0]["points"][0][0] = float("nan")
    if change == "duplicate_eid": data["elements"]["pipe"].append(copy.deepcopy(data["elements"]["pipe"][0]))
    if change == "unknown_units": data["units"] = "unitless"
    if change == "zero_diameter": data["elements"]["pipe"][0]["diameter"] = 0
    if change == "missing_eid": data["elements"]["pipe"][0].pop("eid")
    with pytest.raises(ValueError): BB.prepare_payload(data)


def test_forged_success_receipt_does_not_pass(tmp_path):
    fake = {"status": "verified", "run_id": "r", "input_sha256": "a" * 64,
            "artifacts": {"blend": {"path": str(tmp_path / "missing.blend"), "sha256": "a" * 64}},
            "native_reopened": True}
    with pytest.raises(ValueError): BR.validate_receipt(fake, "r", "a" * 64, tmp_path)


def test_unavailable_blender_returns_blocked_receipt_without_running(tmp_path):
    geometry = tmp_path / "geometry.json"
    geometry.write_text(json.dumps(source()), encoding="utf-8")
    result = BR.build_blender(geometry, tmp_path / "out", blender_path=tmp_path / "not-installed.exe")
    assert result["status"] == "blocked"
    assert result["errors"] and result["run_id"] and result["input_sha256"]
    assert result["artifacts"] == {}


def test_input_snapshot_is_exact_hash_bound_bytes(tmp_path):
    raw = json.dumps(source(), ensure_ascii=False, indent=1).encode('utf-8')
    geometry = tmp_path/'geometry.json'; geometry.write_bytes(raw)
    result = BR.build_blender(geometry, tmp_path/'out', blender_path=tmp_path/'missing.exe')
    assert Path(result['input_snapshot']).read_bytes() == raw
    assert result['input_sha256'] == hashlib.sha256(raw).hexdigest()


def test_source_review_gate_stops_before_native_or_payload(tmp_path):
    data = source()
    data['edit_conflicts'] = [{'eid': 'pipe:generic', 'reason': 'ambiguous edit target'}]
    geometry = tmp_path/'geometry.json'; geometry.write_text(json.dumps(data), encoding='utf-8')
    result = BR.build_blender(geometry, tmp_path/'out', blender_path=tmp_path/'missing.exe')
    assert result['status'] == 'failed'
    assert result['source_verification']['status'] == 'failed'
    assert any(f['id'] == 'V009' for f in result['source_verification']['findings'])
    assert not result.get('runtime') and result['artifacts'] == {}


def test_source_review_warnings_are_retained_in_receipt(tmp_path):
    data = source(); data['elements']['pipe'][0]['needs_review'] = True
    geometry = tmp_path/'geometry.json'; geometry.write_text(json.dumps(data), encoding='utf-8')
    result = BR.build_blender(geometry, tmp_path/'out', blender_path=tmp_path/'missing.exe')
    assert result['status'] == 'blocked'
    assert any(d['code'] == 'V012' for d in result['diagnostics'])


def test_mesh_nan_and_wrong_volume_rejected():
    obj = next(o for o in BB.prepare_payload(source())["objects"] if o["kind"] == "mesh")
    wrong = copy.deepcopy(obj); wrong["vertices_m"][0][0] = float("nan")
    with pytest.raises(ValueError, match="finite"): BV.validate_mesh_payload(wrong)
    wrong = copy.deepcopy(obj); wrong["expected_volume_m3"] *= 10
    with pytest.raises(ValueError, match="volume"): BV.validate_mesh_payload(wrong)


def test_missing_dimensions_are_diagnosed_as_contract_defaults():
    data = source(); data["elements"]["pipe"][0].pop("diameter")
    result = BB.prepare_payload(data)
    assert any(d["code"] == "dimension_default_used" and d["eid"] == "pipe:generic" for d in result["diagnostics"])


def test_different_layouts_cannot_mix_in_one_export():
    data = source()
    data['elements']['pipe'][0]['layout_id'] = 'apartment'
    data['elements']['duct'][0]['layout_id'] = 'detached_alternative'
    with pytest.raises(ValueError, match='layout'):
        BB.prepare_payload(data)


def glb(path, extra_scene=False, wrong_height=False, bad_index=False):
    vertices = [(0., 0., 0.), (1., 0., 0.), (0., 1., 0.)]
    binary = b"".join(struct.pack("<fff", *p) for p in vertices)
    doc = {"asset": {"version": "2.0"}, "scene": 0,
        "scenes": [{"nodes": [0]}], "nodes": [{"mesh": 0, "extras": {"eid": "test"}}],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0}}]}],
        "buffers": [{"byteLength": len(binary)}], "bufferViews": [{"buffer": 0, "byteOffset": 0, "byteLength": len(binary)}],
        "accessors": [{"bufferView": 0, "componentType": 5126, "count": 3, "type": "VEC3"}]}
    if extra_scene: doc["scenes"].append({"nodes": [0]})
    if wrong_height: doc["nodes"][0]["translation"] = [0, 100, 0]
    if bad_index:
        doc['meshes'][0]['primitives'][0]['indices'] = 1
        doc['bufferViews'].append({'buffer': 0, 'byteOffset': len(binary), 'byteLength': 12})
        doc['accessors'].append({'bufferView': 1, 'componentType': 5125, 'count': 3, 'type': 'SCALAR'})
        binary += struct.pack('<III', 0, 1, 999)
        doc['buffers'][0]['byteLength'] = len(binary)
    text = json.dumps(doc).encode(); text += b" " * (-len(text) % 4)
    payload = struct.pack("<II", len(text), 0x4E4F534A) + text + struct.pack("<II", len(binary), 0x004E4942) + binary
    path.write_bytes(struct.pack("<4sII", b"glTF", 2, 12 + len(payload)) + payload)


def test_glb_actual_active_scene_and_transformed_geometry_guard(tmp_path):
    path = tmp_path / "test.glb"
    expected = {"test": {"bounds_m": [[0, 0, 0], [1, 0, 1]], "source_refs": []}}
    glb(path)
    assert BV.verify_glb(path, expected)["status"] == "verified"
    glb(path, extra_scene=True)
    with pytest.raises(ValueError, match="scene"): BV.verify_glb(path, expected)
    glb(path, wrong_height=True)
    with pytest.raises(ValueError, match="bounds"): BV.verify_glb(path, expected)
    glb(path, bad_index=True)
    with pytest.raises(ValueError, match='index'): BV.verify_glb(path, expected)
