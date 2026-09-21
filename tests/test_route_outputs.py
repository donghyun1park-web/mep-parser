# -*- coding: utf-8 -*-
"""계약 v3 경로를 소비자가 **같은 경로·같은 단면**으로 쓰는가 — Blender 준비·물량·빌드 게이트.

입상관이 천장에 눕거나, 원호가 꺾은선이 되거나, 평면 v2 경로의 출력이 조용히 바뀌면
모델은 열리지만 틀린다. 좌표·길이·게이트 판정으로 잰다.
"""
import copy
import json
import math

import pytest

import boq_export
import geom_contract as GC
import verify

pytest.importorskip("shapely")
import blender_builder  # noqa: E402
import blender_verify  # noqa: E402


def _data(**elements):
    return {"units": "mm", "params": {}, "elements": copy.deepcopy(elements),
            "contract": GC.contract_block()}


RISER = {"eid": "p:riser", "points": [[0, 0], [1000, 0]], "elevation": 100.0, "diameter": 20.0,
         "path3d": {"segments": [{"type": "line", "start": [0, 0, 0], "end": [1000, 0, 0]},
                                 {"type": "line", "start": [1000, 0, 0], "end": [1000, 0, 1000]}]}}
ROUND = {"eid": "d:round", "section_shape": "round", "diameter": 125.0, "elevation": 2500.0,
         "points": [[0, 0], [500, 500]],
         "path3d": {"segments": [{"type": "arc", "start": [0, 0, 0], "end": [500, 500, 0],
                                  "center": [0, 500, 0], "normal": [0, 0, 1]}]}}
SLOPED = {"eid": "d:sloped", "width_mm": 204.0, "height_mm": 60.0, "elevation": 2400.0,
          "points": [[0, 0], [2000, 0], [3000, 1000]],
          "path3d": {"segments": [{"type": "line", "start": [0, 0, 0], "end": [2000, 0, 0]},
                                  {"type": "line", "start": [2000, 0, 0], "end": [3000, 1000, 300]}]}}
FLAT = {"eid": "d:flat", "width_mm": 110.0, "height_mm": 54.0, "elevation": 2600.0,
        "points": [[0, 3000], [2000, 3000]]}


def test_blender_payload_follows_the_route_and_section():
    payload = blender_builder.prepare_payload(_data(pipe=[RISER], duct=[ROUND, SLOPED, FLAT]))
    blender_verify.validate_payload(payload)
    objs = {o["eid"]: o for o in payload["objects"]}
    ox, oy = payload["origin_mm"][:2]

    riser = objs["p:riser"]
    assert riser["kind"] == "mesh" and riser["geometry_method"] == "route_polygon_tube_3d"
    zs = [v[2] * 1000 + payload["origin_mm"][2] for v in riser["vertices_m"]]
    assert min(zs) == pytest.approx(90.0) and max(zs) == pytest.approx(1100.0)   # 입상관이 눕지 않는다
    # 저장본과 대조하는 높이 범위 = 관의 실제 꼭짓점(끝 단면은 1100 에서 수평이다 — 봉투 1110 이 아니다)
    assert riser["z_bounds_mm"] == pytest.approx([min(zs), max(zs)])

    rnd = objs["d:round"]
    assert rnd["kind"] == "mesh" and len(rnd["vertices_m"]) % GC.ROUND_SIDES == 0   # FreeCAD 와 같은 정다각형 관
    rings = GC.rect_parts(GC.route_points("duct", ROUND), 125.0, 125.0, 0.0, GC.ROUND_SIDES)
    centers = [[sum(c) / len(ring) for c in zip(*ring)] for part in rings for ring in part]
    assert all(abs(math.hypot(c[0], c[1] - 500) - 500) < 1e-6 for c in centers)  # 링 중심이 원호 위에 남는다

    sloped = objs["d:sloped"]
    assert sloped["kind"] == "mesh" and sloped["geometry_method"] == "route_mitre_sweep_3d"
    rings = GC.rect_rings(GC.route_points("duct", SLOPED), 204.0, 60.0)
    got = [[v[0] * 1000 + ox, v[1] * 1000 + oy, v[2] * 1000] for v in sloped["vertices_m"]]
    want = [p for ring in rings for p in ring]
    assert len(got) == len(want) and all(math.dist(p, q) < 1e-6 for p, q in zip(got, want))  # 공통 링 그대로
    assert objs["d:flat"]["geometry_method"] == "continuous_plan_mitre_sweep"   # v2 경로는 종전 그대로


def test_planar_line_route_with_path3d_builds_exactly_like_v2():
    v3 = dict(FLAT, eid="d:v3", path3d={"segments": GC.path3d_segments(FLAT)})
    payload = blender_builder.prepare_payload(_data(duct=[FLAT, v3]))
    a, b = [o for o in payload["objects"] if o["eid"] in ("d:flat", "d:v3")]
    assert a["vertices_m"] == b["vertices_m"] and a["faces"] == b["faces"]


def test_rect_duct_width_is_horizontal_in_every_direction():
    """폭은 수평이다 — 방향에 따라 폭·높이가 뒤바뀌어도 부피는 같아 어떤 검사에도 안 걸린다
    (FreeCAD 종전 스윕이 실제로 그랬다: 400×100 덕트가 x 방향이면 높이 방향 크기 400)."""
    for end in ([2000, 0, 0], [0, 2000, 0], [-1500, 900, 0]):
        rec = {"eid": "d:dir", "width_mm": 400.0, "height_mm": 100.0, "elevation": 1000.0,
               "points": [[0, 0], end[:2]],
               "path3d": {"segments": [{"type": "line", "start": [0, 0, 0], "end": end},
                                       {"type": "line", "start": end, "end": [end[0], end[1], 10]}]}}
        mesh = blender_builder.prepare_payload(_data(duct=[rec]))["objects"][0]
        zs = [v[2] * 1000 for v in mesh["vertices_m"][:4]]                       # 시작 단면
        assert max(zs) - min(zs) == pytest.approx(100.0, abs=1e-6)


def test_short_sharp_bend_still_builds_a_closed_mesh():
    """폭 200mm 덕트가 188mm 구간에서 118° 씩 꺾여도(실측 사고 형상) 닫힌 메시 · 부피 일치."""
    t1 = math.radians(118)
    p2 = [1000 + 188 * math.cos(t1), 188 * math.sin(t1), 0]
    p3 = [p2[0] + 1000 * math.cos(2 * t1), p2[1] + 1000 * math.sin(2 * t1), 10]
    rec = {"eid": "d:sharp", "width_mm": 200.0, "height_mm": 100.0, "elevation": 2400.0,
           "points": [[0, 0], [1000, 0], p2[:2], p3[:2]],
           "path3d": {"segments": GC.polyline_segments([[0, 0, 0], [1000, 0, 0], p2, p3])}}
    payload = blender_builder.prepare_payload(_data(duct=[rec]))
    blender_verify.validate_payload(payload)
    assert payload["objects"][0]["geometry_method"] == "route_mitre_sweep_3d"


def test_boq_counts_vertical_runs_and_round_ducts():
    header, rows, total = boq_export.aggregate(_data(pipe=[RISER], duct=[ROUND, FLAT]))["MEP"]
    by = {(r[0], r[1]): r for r in rows}
    assert by[("배관", "20")][3] == pytest.approx(2.0)                           # 수평 1m + 입상 1m
    assert by[("덕트", "Ø125")][3] == pytest.approx(math.pi / 2 * 500 / 1000, abs=1e-3)
    assert by[("덕트", "110x54")][3] == pytest.approx(2.0)


def test_the_support_sheet_says_it_is_a_lower_bound_and_names_the_clause():
    """시공기준 표(construction_rules.py)의 지지 개수는 하한 추정 — 근거 열에 규칙 id·조항을 싣는다."""
    supply = {"eid": "p:sup", "points": [[0, 0], [10000, 0]], "elevation": 2600.0, "diameter": 20.0,
              "nominal_size": "DN80", "material": "강관"}
    headers, rows, tot = boq_export.aggregate(_data(pipe=[supply]))["MEP 지지·청소구"]
    assert headers[-1] == "근거"
    (row,) = rows
    assert row[3] == "DN80" and "pipe-support-spacing-horizontal" in row[-1] and "3.4" in row[-1]
    assert tot[6] == row[6]                                   # 합계행이 개수(하한) 열을 더한다


def test_the_insulation_sheet_reads_the_thickness_mep_profile_already_looked_up():
    """rec['insulation_mm'] 는 파싱(mep_profile._assign) 시점에 한 번만 계산된 값 — BOQ 는 다시 계산하지 않는다."""
    hot = {"eid": "p:hot", "points": [[0, 0], [10000, 0]], "elevation": 2600.0, "diameter": 60.5,
           "service": "domestic_hot", "insulation_mm": 35.0, "insulation_table": "hot_90"}
    headers, rows, tot = boq_export.aggregate(_data(pipe=[hot]))["MEP 보온"]
    assert headers == ["구분", "용도", "규격", "두께(mm)", "길이(m)", "근거"]
    (row,) = rows
    assert row[:4] == ["배관", "domestic_hot", "Ø60.5", 35.0] and row[4] == pytest.approx(10.0)
    assert "insulation-thickness-hot-water" in row[-1] and "최소값" in row[-1]
    assert tot[4] == pytest.approx(row[4])
    assert "MEP 보온" not in boq_export.aggregate(_data(duct=[FLAT]))                 # 선언 없으면 섹션 자체가 없다


def test_the_support_and_insulation_sheets_flag_project_default_material_and_grade():
    """근거 열에 '프로젝트 기본값' 을 붙인다 — 검토자가 선언과 기본값을 구분할 수 있게."""
    supply = {"eid": "p:sup", "points": [[0, 0], [10000, 0]], "elevation": 2600.0, "diameter": 20.0,
              "nominal_size": "DN80", "material": "강관", "declaration_basis": {"material": "project_default"}}
    _, (row,), _ = boq_export.aggregate(_data(pipe=[supply]))["MEP 지지·청소구"]
    assert "재질=프로젝트 기본값" in row[-1]
    hot = {"eid": "p:hot", "points": [[0, 0], [10000, 0]], "elevation": 2600.0, "diameter": 60.5,
           "service": "domestic_hot", "insulation_mm": 35.0, "insulation_table": "hot_90",
           "declaration_basis": {"insulation": "project_default"}}
    _, (irow,), _ = boq_export.aggregate(_data(pipe=[hot]))["MEP 보온"]
    assert "등급=프로젝트 기본값" in irow[-1]


def test_broken_path3d_is_stopped_by_the_build_gate():
    broken = dict(SLOPED, eid="d:broken", path3d={"segments": [
        {"type": "line", "start": [0, 0, 0], "end": [1000, 0, 0]},
        {"type": "line", "start": [1500, 0, 0], "end": [2000, 0, 0]}]})
    rep = verify.verify_geometry(_data(duct=[broken]))
    v010 = [f for f in rep.findings if f.id == "V010"]
    assert v010 and v010[0].severity == "error", rep.text()
    assert "path3d" in json.dumps(v010[0].payload, ensure_ascii=False, default=str)
