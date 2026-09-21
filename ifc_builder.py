"""
ifc_builder.py  —  geometry.json → IFC (FreeCAD 불필요)

**납품 IFC 를 쓰는 곳이다**(2026-09-21 결정, `docs/decisions/ifc-builder.md`).
순수 Python(IfcOpenShell + shapely + numpy)으로 geometry.json 을 IFC4 로 직접 쓴다.

★ 형상은 이 파일이 **계산하지 않는다** — 전부 `geom_contract` 가 준다(z 기준면·감김·보
  footprint·관 마이터 링·이음 몸체). 여기는 그 좌표를 IFC 스키마에 옮겨 쓰는 라이터다.
  같은 이유로 곡면을 만들지 않는다: 원형 관도 정다각형 링(`GC.ROUND_SIDES`)이다
  (곡면을 IFC 로 직렬화하면 재검사에서 비다양체가 된다 — `docs/decisions/mep-geometry.md`).

검증은 빌더 바깥이다: `verify_geometry`(빌드 전) → 임시 파일 → `verify_build`
(V101~V107, `artifact_validation.inspect_ifc` 가 IFC 를 **다시 열어** EID·층·형상·부피·
Pset 을 대조) → 통과한 것만 제자리로 옮긴다.

사용:
    python ifc_builder.py geometry.json out.ifc
    python ifc_builder.py geometry.json out.ifc --storey B3 --z 0

설계:
- 벽: 2점 구간마다 IfcWall(배치행렬 + 블록) · 닫힌 벽은 폴리곤 압출.
- 기둥/슬래브/보/zone: 폴리곤 압출(IfcColumn/IfcSlab/IfcBeam/IfcSpace).
- 배관·덕트·트레이·이음: `GC.rect_parts`+`rect_sweep_mesh` 메시 → IfcPipeSegment 등.
- 개구부: IfcOpeningElement + `IfcRelVoidsElement` **선언** — 절삭은 뷰어·검증기가 한다
  (실측: `create_shape` 가 벽 부피에서 교차분을 정확히 뺀다). 문짝·창틀은 얇은 판.
- 단위: geometry.json 은 mm, IFC 는 m → /1000.
"""
import argparse
import json
import math
import os
import sys
import copy
import tempfile
import geom_contract as GC
import artifact_validation as AV
import construction_rules as CR

from geom_contract import poly_area as _poly_area

try:
    import numpy as np
    import ifcopenshell
    import ifcopenshell.api.project
    import ifcopenshell.api.root
    import ifcopenshell.api.unit
    import ifcopenshell.api.context
    import ifcopenshell.api.geometry
    import ifcopenshell.api.spatial
    import ifcopenshell.api.aggregate
    import ifcopenshell.api.feature      # 개구부 선언(IfcRelVoidsElement)
    import ifcopenshell.api.material
    import ifcopenshell.api.type
    import ifcopenshell.api.pset
    import ifcopenshell.util.shape_builder
    import ifcopenshell.util.element
except ImportError as e:
    print(f"[ERROR] 의존성 필요: pip install ifcopenshell numpy  ({e})", file=sys.stderr)
    sys.exit(1)

# 파일 길이단위를 METRE 로 명시(_setup) → add_wall_representation 과
# edit_object_placement 의 단위 규약이 둘 다 metre 로 일치. geometry.json 은 mm 이므로
# 모든 값을 /MM(1000) 해서 metre 로 변환해 전달.
MM = 1000.0

_ELEV_CATS = ("pipe", "duct", "tray", "equipment")
_MEP_IFC_CLASS = {"pipe": "IfcPipeSegment", "duct": "IfcDuctSegment",
                  "tray": "IfcCableCarrierSegment", "equipment": "IfcDistributionElement"}
_FITTING_IFC_CLASS = {"pipe": "IfcPipeFitting", "duct": "IfcDuctFitting",
                      "tray": "IfcCableCarrierFitting"}


def _floor_index(cat, rec, floors):
    """이 레코드가 들어갈 층 인덱스(없으면 None).

    ★ `artifact_validation.inspect_ifc` 의 층 판정과 **같은 식**이어야 한다 — 다르면 빌더가
      넣은 층과 재검사가 기대하는 층이 갈라져 형상이 맞아도 V107 로 막힌다. 고치려면 두 곳을
      같이 고친다(`inspect_ifc` 의 '층' 블록)."""
    z = GC.floor_z(cat, rec)
    if rec.get("level"):
        matches = [i for i, f in enumerate(floors) if GC.floor_has_level(f, rec["level"])]
        return matches[0] if len(matches) == 1 else None
    if cat in _ELEV_CATS:
        # 배관 elevation(2600)이 층 z 와 같을 리 없다 — 그 높이를 품는 층(가장 큰 z ≤ elev)에 넣는다.
        below = [i for i, f in enumerate(floors) if float(f.get("z", 0)) <= z + 100]
        return (max(below, key=lambda i: float(floors[i].get("z", 0))) if below
                else min(range(len(floors)), key=lambda i: abs(float(floors[i].get("z", 0)) - z)))
    matches = [i for i, f in enumerate(floors) if abs(float(f.get("z", 0)) - z) < 100]
    return matches[0] if len(matches) == 1 else None


def _pset_values(rec, prov):
    """Pset_MEPParser — `AV.qa_values` 의 10키(재검사가 대조하는 계약) + 뷰어용 14키.

    `freecad_builder.set_ifc_props` 와 같은 표다. 빈 값은 넣지 않는다(뷰어에서 빈 칸이 늘면
    NeedsReview 필터가 묻힌다). 여기 없는 키를 재검사가 요구하지는 않지만, 두 빌더의 IFC 를
    나란히 놓고 비교할 수 있어야 한다."""
    sec = rec.get("section") or {}
    ov = rec.get("overrides") or {}
    values = AV.qa_values(rec, prov)
    extra = {
        "System": ov.get("system", rec.get("system")),
        "Region": rec.get("region_id"),
        "Circuit": rec.get("circuit_id"),
        "NominalSize": rec.get("nominal_size"),
        "SourceRefs": json.dumps(rec.get("source_refs") or [], ensure_ascii=False) if rec.get("source_refs") else None,
        "SourceLength": rec.get("source_length_mm"),
        "Joints": json.dumps(rec["joints"], ensure_ascii=False) if rec.get("joints") else None,
        "FittingKind": rec.get("fitting_kind"),
        "Assumptions": json.dumps(rec.get("assumptions"), ensure_ascii=False) if rec.get("assumptions") else None,
        "DeclarationBasis": json.dumps(rec.get("declaration_basis"), ensure_ascii=False) if rec.get("declaration_basis") else None,
        "MemberName": sec.get("name") or rec.get("member_name"),
        "Section": sec.get("size"),
        "Pairing": rec.get("pairing"),
        "WidthDetected": rec.get("width_detected"),
    }
    for key, value in extra.items():
        if value not in (None, ""):
            values[key] = float(value) if key in ("SourceLength", "WidthDetected") else str(value)
    return values


def _prism_polygon(coords_mm):
    from shapely.geometry import Polygon
    poly = Polygon([(float(p[0]), float(p[1])) for p in coords_mm])
    return poly if poly.is_valid else poly.buffer(0)


def _void_volume(host_poly, z0, z1, cutters):
    """벽 프리즘에서 커터 프리즘들이 실제로 빼는 부피(mm³).

    둘 다 **수직 프리즘**이라 z 구간을 잘라 각 구간의 평면 합집합 넓이를 재면 정확하다 —
    커터가 겹쳐도(같은 벽에 개구부 둘) 합집합이라 두 번 빼지 않는다. 3D 불리언이 필요 없다."""
    from shapely.ops import unary_union
    if not cutters:
        return 0.0
    zs = sorted({z0, z1} | {z for _p, a, b in cutters for z in (a, b) if z0 < z < z1})
    total = 0.0
    for lo, hi in zip(zs, zs[1:]):
        mid = (lo + hi) / 2.0
        active = [p for p, a, b in cutters if a <= mid <= b]
        if active:
            total += unary_union(active).intersection(host_poly).area * (hi - lo)
    return total


def _opening_axes(op):
    """(벽 방향, 법선) 단위벡터 — `freecad_builder._opening_axes` 와 같은 규약."""
    hd = op.get("host_dir")
    if hd and (abs(hd[0]) + abs(hd[1])) > 1e-6:
        ln = math.hypot(hd[0], hd[1])
        return (hd[0] / ln, hd[1] / ln), (-hd[1] / ln, hd[0] / ln)
    return (1.0, 0.0), (0.0, 1.0)


def _opening_box(op, params):
    """개구부 스펙 → (커터 평면 폴리곤, z0, z1, 문짝 평면 폴리곤|None, subtype).

    좌표식은 `freecad_builder._opening_solids` 그대로다(두 빌더의 개구부가 같은 자리에 나야 한다).
    커터는 벽 두께보다 margin 만큼 두껍다 — 관통을 보장하고, 실제로 빠지는 양은 교차분뿐이다."""
    d = params.get("wall", {})
    wall_h = float(d.get("height", 2800.0))
    margin = 100.0
    cx, cy = [float(v) for v in (op.get("center") or [0, 0])[:2]]
    subtype = op.get("subtype")
    edited = op.get("overrides") or {}

    def dim(key, default):
        # ★ 수동 편집(`overrides`)이 먼저 — `GC._dim` 과 같은 순서. 종전에는 최상위 값만 읽어
        #   미리보기에서 고친 창 높이·창대가 납품 IFC 에서는 원래 값으로 뚫렸다(검사도 통과했다).
        for source in (edited, op):
            if source.get(key) is not None:
                return float(source[key])
        return default

    width = dim("width", 0.0) or float(op.get("radius", 450.0)) * 2
    depth = float(op.get("host_width") or d.get("width", 200.0)) + margin
    z_base = float(op.get("z_base", 0.0))
    if subtype == "window":
        sill, oh = dim("sill", 900.0) + z_base, dim("height", 1200.0)
    elif subtype == "door":
        sill, oh = z_base, dim("height", 2100.0)
    else:
        sill, oh = z_base - margin, wall_h + margin * 2
    (ux, uy), (nx, ny) = _opening_axes(op)

    def box(half_depth):
        hw = width / 2.0
        return [(cx - ux * hw - nx * half_depth, cy - uy * hw - ny * half_depth),
                (cx + ux * hw - nx * half_depth, cy + uy * hw - ny * half_depth),
                (cx + ux * hw + nx * half_depth, cy + uy * hw + ny * half_depth),
                (cx - ux * hw + nx * half_depth, cy - uy * hw + ny * half_depth)]
    leaf = box(20.0) if subtype in ("door", "window") else None   # 판 두께 40mm
    return box(depth / 2.0), sill, sill + oh, leaf, subtype


def _placement_matrix(p1, p2, z=0.0):
    """벽을 p1 에 놓고 centerline 방향으로 회전하는 4x4 행렬(m 단위)."""
    dx, dy = p2[0] - p1[0], p2[1] - p1[1]
    L = math.hypot(dx, dy)
    ux, uy = (dx / L, dy / L) if L else (1.0, 0.0)
    M = np.eye(4)
    M[0, 0], M[1, 0] = ux, uy           # X축 = 벽 길이방향
    M[0, 1], M[1, 1] = -uy, ux          # Y축 = 두께 법선
    M[0, 3] = p1[0] / MM
    M[1, 3] = p1[1] / MM
    M[2, 3] = z / MM
    return M


def _setup_project(model):
    """프로젝트 골격(단위/컨텍스트/Site/Building)만 생성 — 층은 _add_storey 로."""
    proj = ifcopenshell.api.root.create_entity(model, ifc_class="IfcProject", name="MEP")
    # 길이단위 = METRE 명시 (prefix 없음). 기본값은 MILLIMETRE 라 단위규약 불일치 발생.
    lu = ifcopenshell.api.unit.add_si_unit(model, unit_type="LENGTHUNIT")
    ifcopenshell.api.unit.assign_unit(model, units=[lu])
    ctx = ifcopenshell.api.context.add_context(model, context_type="Model")
    body = ifcopenshell.api.context.add_context(
        model, context_type="Model", context_identifier="Body",
        target_view="MODEL_VIEW", parent=ctx)
    site = ifcopenshell.api.root.create_entity(model, ifc_class="IfcSite", name="Site")
    bld = ifcopenshell.api.root.create_entity(model, ifc_class="IfcBuilding", name="Building")
    ifcopenshell.api.aggregate.assign_object(model, relating_object=proj, products=[site])
    ifcopenshell.api.aggregate.assign_object(model, relating_object=site, products=[bld])
    return body, bld


# ── [표준 접합] 벽 타입 / 접합 ────────────────────────────────────────
# IFC 는 재료 레이어셋(두께+우선순위)을 가진 벽끼리 IfcRelConnectsPathElements 로
# 연결하면, regenerate_wall_representation 이 코너의 마이터·버트·노치를 자동 생성한다.
# 우리가 직접 계산하던 코너 처리를 표준에 넘기고, Revit/ArchiCAD 가 읽는 "연결된 벽"
# 의미론까지 함께 얻는다. 전제: 재료 레이어셋 + Plan/Axis/GRAPH_VIEW 컨텍스트.
JOIN_TOL_MM = 60.0   # 끝점 이 거리 이내면 접합(dxf_parser.CORNER_SNAP_TOL_MM=50 + 여유)


def _axis_context(model):
    """Plan/Axis/GRAPH_VIEW 컨텍스트(없으면 생성) — regenerate 가 요구."""
    for c in model.by_type("IfcGeometricRepresentationSubContext"):
        if c.ContextType == "Plan" and c.ContextIdentifier == "Axis":
            return c
    parent = None
    for c in model.by_type("IfcGeometricRepresentationContext"):
        if c.ContextType == "Plan" and not c.is_a("IfcGeometricRepresentationSubContext"):
            parent = c
            break
    if parent is None:
        parent = ifcopenshell.api.context.add_context(model, context_type="Plan")
    return ifcopenshell.api.context.add_context(
        model, context_type="Plan", context_identifier="Axis",
        target_view="GRAPH_VIEW", parent=parent)


def _declared_material(rec):
    """레코드가 **적은** 재질 이름(없으면 None). `overrides` 가 먼저 — 수동 편집이 이긴다."""
    value = (rec.get("overrides") or {}).get("material") or rec.get("material")
    return str(value) if value else None


def _material_entity(model, name, ctx):
    """이름당 `IfcMaterial` 하나(CLAUDE.md `material=`). `_wall_type` 과 `_assign_material` 이 같이 쓴다."""
    cache = ctx.setdefault("_material_entities", {})
    if name not in cache:
        cache[name] = ifcopenshell.api.material.add_material(model, name=name)
    return cache[name]


def _wall_type(model, thickness_mm, cache, material=None, ctx=None):
    """두께 × **선언된 재질**별 IfcWallType(+ IfcMaterialLayerSet). 같은 조합은 캐시 재사용.

    ★ 재질을 키에 넣는 이유: 레이어셋은 타입에 딸리고 타입은 벽 여러 장이 공유한다. 두께만으로
      캐시하면 같은 200mm 의 콘크리트 벽과 조적 벽이 한 레이어셋을 쓰게 되어 **나중 것이 앞 것의
      재질을 덮어쓴다**(형상은 멀쩡해서 검사에 안 걸린다).
    ★ 선언이 없으면 레이어에 재질을 **비워 둔다**(`IfcMaterialLayer.Material` 은 optional) —
      종전처럼 "Concrete" 를 넣으면 조적벽이 콘크리트로 납품된다."""
    key = (round(float(thickness_mm), 1), material or None)
    if key in cache:
        return cache[key]
    thickness = key[0]
    mat = _material_entity(model, material, ctx) if (material and ctx is not None) else None
    lset = ifcopenshell.api.material.add_material_set(
        model, name=f"W{thickness:g}" + (f"-{material}" if material else ""),
        set_type="IfcMaterialLayerSet")
    layer = ifcopenshell.api.material.add_layer(model, layer_set=lset, material=mat)
    ifcopenshell.api.material.edit_layer(
        model, layer=layer,
        attributes={"LayerThickness": thickness / MM, "Priority": 1})
    wt = ifcopenshell.api.root.create_entity(
        model, ifc_class="IfcWallType",
        name=f"WALL-{thickness:g}" + (f"-{material}" if material else ""))
    ifcopenshell.api.material.assign_material(model, products=[wt], material=lset)
    cache[key] = wt
    return wt


def _connect_walls(model, made, log=None):
    """끝점이 맞닿는 벽쌍을 IfcRelConnectsPathElements 로 연결한 뒤 형상 재생성.
    made: [(wall, p1_mm, p2_mm, length_mm, height_mm), ...]
    한 벽의 한쪽 끝은 접합 1개만 가질 수 있으므로(ATSTART/ATEND) 노드마다 짝지어 연결.
    반환 (연결 수, 재생성 성공 수)."""
    q = JOIN_TOL_MM
    nodes = {}   # 격자 키 → [(wall_idx, 'p1'|'p2')]
    for idx, (_w, p1, p2, _L, _h) in enumerate(made):
        for tag, p in (("p1", p1), ("p2", p2)):
            nodes.setdefault((round(p[0] / q), round(p[1] / q)), []).append((idx, tag))

    used = set()      # (wall_idx, tag) — 이미 접합에 쓰인 끝
    n_conn = 0
    for key in sorted(nodes):
        ends = [e for e in nodes[key] if e not in used]
        for a, b in zip(ends[0::2], ends[1::2]):
            if made[a[0]][0] is made[b[0]][0]:
                continue
            try:
                if ifcopenshell.api.geometry.connect_wall(
                        model, wall1=made[a[0]][0], wall2=made[b[0]][0]):
                    used.add(a); used.add(b)
                    n_conn += 1
            except Exception:
                continue

    n_regen = 0
    for wall, _p1, _p2, L, h in made:
        try:
            ifcopenshell.api.geometry.regenerate_wall_representation(
                model, wall=wall, length=L / MM, height=h / MM)
            n_regen += 1
        except Exception as e:
            if log:
                log(f"[warn] regenerate 실패: {e}")
    return n_conn, n_regen


# ── [표준 물량] buildingSMART Qto_* 기입 ──────────────────────────────
# 우리 자체 집계(boq_export)와 별개로, IFC 안에 표준 물량셋을 심어 두면 Revit·
# ArchiCAD·ifc5d 등 외부 도구가 우리 물량을 그대로 읽는다. 값은 도면에서 계산한
# 결정론 값(형상 재계산 아님) — 단위는 파일 단위계(METRE) 기준.
def _add_qto(model, product, name, props):
    """Qto_*BaseQuantities 기입. 실패해도 빌드는 계속(부가 데이터)."""
    try:
        qto = ifcopenshell.api.pset.add_qto(model, product=product, name=name)
        ifcopenshell.api.pset.edit_qto(model, qto=qto, properties=props)
        return True
    except Exception:
        return False


def _add_storey(model, bld, name, z_mm=0.0):
    sto = ifcopenshell.api.root.create_entity(
        model, ifc_class="IfcBuildingStorey", name=name)
    try:
        sto.Elevation = z_mm / MM   # Revit/ArchiCAD 층 레벨 표시용
    except Exception:
        pass
    ifcopenshell.api.aggregate.assign_object(model, relating_object=bld, products=[sto])
    return sto


def _assign_material(model, product, name, ctx):
    """레코드에 **적힌** 재질만 붙인다 — 카테고리 추정은 하지 않는다(CLAUDE.md `material=`).

    ★ 접합(connect) 모드의 벽은 이미 `IfcMaterialLayerSetUsage` 를 달고 있다 — 그 자리에 평범한
      `IfcMaterial` 을 덮어쓰면 `create_2pt_wall` 이 만든 축선 벽이 두께 근거를 잃어 Body 가
      아예 안 생기고, 빌드가 `Representation is NULL` 로 죽는다. 그쪽 재질은 `_wall_type` 이
      레이어셋에 이미 써 넣었으므로 여기서는 세기만 한다."""
    material = _material_entity(model, name, ctx)
    try:
        existing = ifcopenshell.util.element.get_material(product)
        if existing is not None and existing.is_a("IfcMaterialLayerSetUsage"):
            ctx["materials"][name] = ctx["materials"].get(name, 0) + 1
            return
        ifcopenshell.api.material.assign_material(model, products=[product], material=material)
        ctx["materials"][name] = ctx["materials"].get(name, 0) + 1
    except Exception as exc:
        print(f"[warn] 재질 '{name}' 부여 실패({product.Name}): {exc}", file=sys.stderr)


def _mesh_product(model, body, verts, faces, ifc_class, name):
    """mm 좌표 메시 → 메시 표현을 가진 IFC 객체. 곡면 없음(평면 면만)."""
    obj = ifcopenshell.api.root.create_entity(model, ifc_class=ifc_class, name=name)
    rep = ifcopenshell.api.geometry.add_mesh_representation(
        model, context=body,
        vertices=[[[p[0] / MM, p[1] / MM, p[2] / MM] for p in verts]], faces=[faces])
    ifcopenshell.api.geometry.assign_representation(model, product=obj, representation=rep)
    return obj


def _route_mesh(cat, rec, params):
    """MEP 경로 → (verts, faces, 기대부피). `freecad_builder._rect_solid` 와 같은 링이다."""
    section = GC.mep_section(cat, rec, params)
    route = GC.route_points(cat, rec)
    if section["shape"] == "round":
        d = section["diameter"]
        parts = GC.rect_parts(route, d, d, 0.0, sides=GC.ROUND_SIDES)
    else:
        parts = GC.rect_parts(route, section["width_mm"], section["height_mm"], section["roll"])
    return _merge_parts(parts)


def _footprint_product(model, body, sb, rec, params, z_offset, ifc_class, name, cat):
    """장비·외곽선 부재 — 원본 외곽을 그대로 압출한다(축선을 지어내지 않는다). 구멍은 프로파일의
    내부 곡선으로 뺀다. 반환 (객체, 부피mm³).

    ★ `cat` 을 받는다 — 종전에는 장비가 아니면 전부 "duct" 로 보아, 외곽선 트레이가 덕트 단면
      (`params['duct']`)으로 서서 선언한 트레이 높이와 다르게 나갔다."""
    pts = rec.get("points") or []
    if len(pts) < 3:
        raise ValueError("닫힌 외곽선이 아니다")
    z0, z1 = GC.z_range(cat, rec, params)
    height = z1 - z0
    if height <= 0:
        raise ValueError(f"높이가 0 이하({height})")
    outer = [(float(p[0]), float(p[1])) for p in pts]
    holes = [[(float(p[0]), float(p[1])) for p in hole] for hole in (rec.get("holes") or [])]
    obj = _extrude_polygon(model, body, sb, outer, height, z_offset + z0, ifc_class, name,
                           holes=holes)
    if obj is None:
        raise ValueError("프로파일 압출 실패")
    area = _poly_area(outer) - sum(_poly_area(h) for h in holes)
    return obj, area * height


def _merge_parts(parts):
    """링 조각들(급꺾임은 직각 토막으로 나뉘어 온다) → 한 메시. 토막을 합치지 않는다."""
    verts, faces = [], []
    for part in parts:
        v, f = GC.rect_sweep_mesh(part)
        offset = len(verts)
        verts += [list(p) for p in v]
        faces += [[i + offset for i in face] for face in f]
    return verts, faces, abs(GC._signed_volume(verts, faces)) if faces else 0.0


def _build_elements(model, body, sb, sto, data, z_offset=0.0, connect=False,
                    type_cache=None, qto=True, ctx=None, keep=None):
    """한 층의 geometry dict → IFC 요소 생성. z_offset(mm)=층 바닥 레벨.
    요소별 z = z_offset + 레코드 z_base (레코드 값은 층 '내' 오프셋)."""
    params = data.get("params", {})
    el = data.get("elements", {})
    # ★ 층별 조립에서도 목록을 **자르지 않는다** — 개구부가 `wall_indices` 로 호스트를
    #   가리키므로 번호가 밀리면 엉뚱한 벽에 구멍이 난다. 거를 조건만 받는다.
    if keep is None:
        def keep(cat, rec):
            return True

    def rows(cat):
        return [(i, r) for i, r in enumerate(el.get(cat, [])) if keep(cat, r)]
    stats = {"wall": 0, "column": 0, "slab": 0, "beam": 0, "zone": 0,
             "mep": 0, "fitting": 0, "opening": 0, "skip": 0, "qto": 0}
    made = []                       # 접합 모드에서 생성된 벽 목록
    if ctx is None:
        ctx = {"hosts": {}, "openings": [], "materials": {}, "mep_volume": {"expected_mm3": 0.0, "built_mm3": 0.0}}
    if type_cache is None:
        # 벽타입은 **모델 하나에 하나**다 — 층마다 새 캐시를 주면 같은 `WALL-200` 이 층 수만큼
        # 생겨 뷰어 타입 목록이 그만큼 반복된다(실측: 3층이면 6개).
        type_cache = ctx.setdefault("_wall_types", {})

    def skip(category, rec, reason):
        """못 만든 레코드를 **무엇이 왜** 까지 적어 센다 — V106 이 이걸로 빌드를 막으므로,
        수만 세면 사람이 어느 부재를 고쳐야 하는지 알 수 없다(`freecad_builder` 의 UNBUILT 와 같은 규약)."""
        stats["skip"] += 1
        ctx.setdefault("unbuilt", []).append(
            {"category": category, "eid": (rec or {}).get("eid"),
             "layer": (rec or {}).get("layer"), "reason": reason})

    def container(prod, rec, volume_mm3=None, spatial="container"):
        prod.Name = sto.Name + ":" + prod.Name
        if spatial == "aggregate":       # IfcSpace 는 층에 **집합**된다(재검사가 Decomposes 도 본다)
            ifcopenshell.api.aggregate.assign_object(model, relating_object=sto, products=[prod])
        else:
            ifcopenshell.api.spatial.assign_container(
                model, relating_structure=sto, products=[prod])
        pset = ifcopenshell.api.pset.add_pset(model, product=prod, name="Pset_MEPParser")
        values = _pset_values(rec, data["_build_provenance"])
        values["EID"] = model.create_entity("IfcText", values["EID"])
        values["SourceEIDs"] = model.create_entity("IfcText", values["SourceEIDs"])
        ifcopenshell.api.pset.edit_pset(model, pset=pset, properties=values)
        entry = {"name": prod.Name, "source_eids": AV.source_eids(rec),
                 "qa": AV.qa_values(rec, data["_build_provenance"])}
        if volume_mm3 is not None:
            entry["volume_mm3"] = volume_mm3
        material = _declared_material(rec)
        if material:
            _assign_material(model, prod, material, ctx)
        data.setdefault("_expected_products", []).append(entry)
        return entry

    # ── 벽 ───────────────────────────────────────────────────────────
    for i, w in rows("wall"):
        cl = w.get("centerline") or w.get("points") or []
        if len(cl) < 2:
            skip("wall", w, "centerline_under_two_points")
            continue
        width = GC.width_of(w, params)
        z0, z1 = GC.z_range("wall", w, params)
        height = z1 - z0
        zb = z_offset + z0
        # 폐합 벽(closed)은 둘레 벽 여러 장이 아니라 솔리드(기둥형)로 세운다.
        # dxf_parser 가 pairing="closed" 로 표시한 원래 의도이며, boq_export 의
        # 집계(단면적×높이)와도 일치한다. 둘레 분해 시 물량이 어긋난다(교차 대조로 발견).
        if (w.get("closed") or w.get("pairing") == "closed") and len(cl) >= 3:
            pts = [(p[0], p[1]) for p in cl]
            if len(pts) > 2 and pts[0] == pts[-1]:
                pts = pts[:-1]
            solid = _extrude_polygon(model, body, sb, pts, height, zb,
                                     "IfcWall", f"Wall_{i}")
            if solid:
                area = _poly_area(pts)
                entry = container(solid, w, volume_mm3=area * height)
                ctx["hosts"].setdefault(i, []).append(
                    {"product": solid, "entry": entry, "rec": w, "poly": _prism_polygon(pts),
                     "z0": zb, "z1": zb + height, "cutters": []})
                if qto:
                    _add_qto(model, solid, "Qto_WallBaseQuantities", {
                        "Height": height / MM,
                        "GrossFootprintArea": area / MM ** 2,
                        "GrossVolume": (area * height) / MM ** 3,
                        "NetVolume": (area * height) / MM ** 3,
                    })
                    stats["qto"] += 1
                stats["wall"] += 1
            else:
                skip("wall", w, "closed_wall_extrusion_failed")
            continue
        # 다중점 centerline → 세그먼트별 벽
        for k in range(len(cl) - 1):
            p1, p2 = cl[k], cl[k + 1]
            L = math.hypot(p2[0] - p1[0], p2[1] - p1[1])
            if L < 1.0:
                continue
            wall = ifcopenshell.api.root.create_entity(
                model, ifc_class="IfcWall", name=f"Wall_{i}_{k}")
            placed = False
            if connect:
                # 표준 경로: 벽타입(재료 레이어셋) + 2점 벽 → 이후 connect/regenerate
                try:
                    ifcopenshell.api.type.assign_type(
                        model, related_objects=[wall],
                        relating_type=_wall_type(model, width, type_cache,
                                                 material=_declared_material(w), ctx=ctx))
                    usage = ifcopenshell.util.element.get_material(wall)
                    if usage and usage.is_a("IfcMaterialLayerSetUsage"):
                        usage.OffsetFromReferenceLine = -width / MM / 2
                    ifcopenshell.api.geometry.create_2pt_wall(
                        model, element=wall, context=body,
                        p1=(p1[0] / MM, p1[1] / MM), p2=(p2[0] / MM, p2[1] / MM),
                        elevation=zb / MM, height=height / MM,
                        thickness=width / MM)
                    made.append((wall, p1, p2, L, height))
                    placed = True
                except Exception as e:
                    print(f"[warn] 표준 벽 생성 실패 → 기본 경로 폴백: {e}", file=sys.stderr)
            if not placed:
                # 기본(폴백) 경로: 배치행렬 + 단순 블록 표현
                ifcopenshell.api.geometry.edit_object_placement(
                    model, product=wall, matrix=_placement_matrix(p1, p2, zb))
                rep = ifcopenshell.api.geometry.add_wall_representation(
                    model, context=body, length=L / MM,
                    height=height / MM, thickness=width / MM, offset=-width / MM / 2)
                ifcopenshell.api.geometry.assign_representation(
                    model, product=wall, representation=rep)
            # 접합(connect) 모드는 코너를 IFC 표준으로 **다시 만든다** — 그때의 부피는 우리가
            # 계산한 값이 아니므로 기대 부피를 선언하지 않는다(재검사는 경계·다양체로 계속 본다).
            entry = container(wall, w, volume_mm3=None if connect else L * width * height)
            (ux, uy) = ((p2[0] - p1[0]) / L, (p2[1] - p1[1]) / L)
            hw = width / 2.0
            ctx["hosts"].setdefault(i, []).append({"product": wall, "entry": entry, "rec": w,
                "poly": _prism_polygon([(p1[0] - uy * hw, p1[1] + ux * hw), (p2[0] - uy * hw, p2[1] + ux * hw),
                                        (p2[0] + uy * hw, p2[1] - ux * hw), (p1[0] + uy * hw, p1[1] - ux * hw)]),
                "z0": zb, "z1": zb + height, "cutters": []})
            if qto:
                _add_qto(model, wall, "Qto_WallBaseQuantities", {
                    "Length": L / MM, "Width": width / MM, "Height": height / MM,
                    "GrossSideArea": (L * height) / MM ** 2,
                    "NetSideArea": (L * height) / MM ** 2,
                    "GrossFootprintArea": (L * width) / MM ** 2,
                    "GrossVolume": (L * height * width) / MM ** 3,
                    "NetVolume": (L * height * width) / MM ** 3,
                })
                stats["qto"] += 1
            stats["wall"] += 1

    # ── 기둥 ─────────────────────────────────────────────────────────
    for i, c in rows("column"):
        z0, z1 = GC.z_range("column", c, params)
        zb = z_offset + z0
        h = z1 - z0
        coords = None
        col = None
        if c.get("kind") == "circle":
            cx, cy = c.get("center", [0, 0])
            r = float(c.get("radius", 200.0))
            profile = model.create_entity("IfcCircleProfileDef", ProfileType="AREA", Radius=r / MM)
            col = ifcopenshell.api.root.create_entity(model, ifc_class="IfcColumn", name=f"Col_{i}")
            matrix = np.eye(4)
            matrix[:3, 3] = [cx / MM, cy / MM, zb / MM]
            ifcopenshell.api.geometry.edit_object_placement(model, product=col, matrix=matrix)
            rep = ifcopenshell.api.geometry.add_profile_representation(model, context=body, profile=profile, depth=h / MM)
            ifcopenshell.api.geometry.assign_representation(model, product=col, representation=rep)
        elif c.get("kind") == "polyline" and c.get("closed") and len(c.get("points", [])) >= 3:
            coords = [(p[0], p[1]) for p in c["points"]]
        if not coords and col is None:
            skip("column", c, "no_closed_outline_or_circle")
            continue
        if col is None:
            col = _extrude_polygon(model, body, sb, coords, h, zb, "IfcColumn", f"Col_{i}")
        if col:
            area = math.pi * r * r if c.get("kind") == "circle" else _poly_area(coords)
            # 원형 기둥은 IfcCircleProfileDef(곡면) — 재검사가 다시 삼각분할하면 다각형이라
            # 부피가 원보다 작다. 기대 부피를 선언하지 않는다(경계·다양체는 계속 본다).
            container(col, c, volume_mm3=None if c.get("kind") == "circle" else area * h)
            if qto:
                _add_qto(model, col, "Qto_ColumnBaseQuantities", {
                    "Length": h / MM,
                    "CrossSectionArea": area / MM ** 2,
                    "GrossVolume": (area * h) / MM ** 3,
                    "NetVolume": (area * h) / MM ** 3,
                })
                stats["qto"] += 1
            stats["column"] += 1

    # ── 슬래브 ───────────────────────────────────────────────────────
    for i, s in rows("slab"):
        if s.get("kind") != "polyline" or not s.get("closed"):
            skip("slab", s, "not_a_closed_polyline")
            continue
        pts = s.get("points", [])
        if len(pts) < 3:
            continue
        z0, z1 = GC.z_range("slab", s, params)
        zb = z_offset + z1
        thk = z1 - z0
        coords = [(p[0], p[1]) for p in pts]
        slab = _extrude_polygon(model, body, sb, coords, thk, zb - thk, "IfcSlab", f"Slab_{i}")
        if slab:
            area = _poly_area(coords)
            container(slab, s, volume_mm3=area * thk)
            if qto:
                peri = sum(math.hypot(coords[(j + 1) % len(coords)][0] - coords[j][0],
                                      coords[(j + 1) % len(coords)][1] - coords[j][1])
                           for j in range(len(coords)))
                _add_qto(model, slab, "Qto_SlabBaseQuantities", {
                    "Depth": thk / MM, "Perimeter": peri / MM,
                    "GrossArea": area / MM ** 2, "NetArea": area / MM ** 2,
                    "GrossVolume": (area * thk) / MM ** 3,
                    "NetVolume": (area * thk) / MM ** 3,
                })
                stats["qto"] += 1
            stats["slab"] += 1

    # ── 보 ───────────────────────────────────────────────────────────
    # z 규약은 slab 과 같은 'top' — z_base 가 보 **상단**이고 아래로 춤만큼 내려간다.
    # 축선→footprint 규칙은 `GC.beam_rings` 단독(미리보기·물량이 같은 식을 쓴다).
    for i, b in rows("beam"):
        z0, z1 = GC.z_range("beam", b, params)
        depth = z1 - z0
        if depth <= 0:
            skip("beam", b, "non_positive_depth")
            continue
        rings = GC.beam_rings(b, params)
        if not rings:
            skip("beam", b, "no_footprint_rings")
            continue
        name = b.get("member_name")
        for j, ring in enumerate(rings):
            coords = [(p[0], p[1]) for p in GC.ccw(ring)]
            beam = _extrude_polygon(model, body, sb, coords, depth, z_offset + z0,
                                    "IfcBeam", f"Beam_{i}_{j}" + (f"_{name}" if name else ""))
            if not beam:
                skip("beam", b, "ring_extrusion_failed")
                continue
            area = _poly_area(coords)
            container(beam, b, volume_mm3=area * depth)
            if qto:
                _add_qto(model, beam, "Qto_BeamBaseQuantities", {
                    "CrossSectionArea": area / MM ** 2,
                    "GrossVolume": (area * depth) / MM ** 3,
                    "NetVolume": (area * depth) / MM ** 3})
                stats["qto"] += 1
            stats["beam"] += 1

    # ── zone → IfcSpace ──────────────────────────────────────────────
    for i, zrec in rows("zone"):
        pts = zrec.get("points") or []
        if zrec.get("kind") != "polyline" or not zrec.get("closed") or len(pts) < 3:
            skip("zone", zrec, "not_a_closed_polyline")
            continue
        z0, z1 = GC.z_range("zone", zrec, params)
        room_h = z1 - z0 if z1 > z0 else GC.height_of(zrec, params, "wall")
        coords = [(p[0], p[1]) for p in pts]
        space = _extrude_polygon(model, body, sb, coords, room_h, z_offset + z0,
                                 "IfcSpace", f"Space_{i}")
        if not space:
            skip("zone", zrec, "extrusion_failed")
            continue
        container(space, zrec, volume_mm3=_poly_area(coords) * room_h, spatial="aggregate")
        stats["zone"] += 1

    # ── 배관·덕트·트레이·장비 ────────────────────────────────────────
    for cat in ("pipe", "duct", "tray", "equipment"):
        for i, rec in rows(cat):
            label = f"{cat.capitalize()}_{i}"
            try:
                if cat == "equipment" or rec.get("geometry_mode") == "footprint":
                    # 외곽선 부재는 `mep_volume`(퇴화 비율)에 **넣지 않는다** — 기대·실제 부피를 같은
                    # 식(면적×높이)으로 셈하므로 비율이 늘 1 이고, 섞으면 축선 부재가 종잇장이 돼도
                    # 합계 비율이 기준을 넘겨 가린다. 외곽선 부재의 부피는 제품마다 V107 이 IFC 에서 잰다.
                    obj, volume = _footprint_product(model, body, sb, rec, params,
                                                     z_offset, _MEP_IFC_CLASS[cat], label, cat)
                else:
                    verts, faces, volume = _route_mesh(cat, rec, params)
                    if not faces:
                        raise ValueError("빈 경로")
                    if z_offset:
                        verts = [[p[0], p[1], p[2] + z_offset] for p in verts]
                    obj = _mesh_product(model, body, verts, faces, _MEP_IFC_CLASS[cat], label)
                    section = GC.mep_section(cat, rec, params)
                    route = GC.route_points(cat, rec)
                    length = sum(math.dist(a, b) for a, b in zip(route, route[1:]))
                    area = (math.pi * (section["diameter"] / 2) ** 2 if section["shape"] == "round"
                            else section["width_mm"] * section["height_mm"])
                    ctx["mep_volume"]["expected_mm3"] += length * area
                    ctx["mep_volume"]["built_mm3"] += volume
            except Exception as exc:
                print(f"[warn] {label} 빌드 실패: {exc}", file=sys.stderr)
                skip(cat, rec, f"build_failed: {exc}")
                continue
            container(obj, rec, volume_mm3=volume)
            stats["mep"] += 1

    # ── 이음 몸체 — 형식·몸체는 `GC.joint_fittings` 하나(물량표가 같은 함수로 센다) ──
    for fitting in ctx.get("fittings_for_floor", {}).get(id(sto), []):
        cat, members = fitting["category"], [rec for _c, rec, _p, _a in fitting["members"]]
        try:
            verts, faces, volume = _merge_parts(fitting["parts"])
            if not faces:
                raise ValueError("빈 이음 몸체")
            if z_offset:
                verts = [[p[0], p[1], p[2] + z_offset] for p in verts]
            obj = _mesh_product(model, body, verts, faces, _FITTING_IFC_CLASS[cat],
                                f"{cat.capitalize()}Fitting_{stats['fitting']}")
        except Exception as exc:
            ctx["fittings"]["skipped"].append({"joint": fitting["joint"], "eids": fitting["eids"],
                                               "reason": "shape_failed", "detail": str(exc)})
            continue
        first = members[0]
        review = [r for r in members if r.get("needs_review")]
        rec = {"eid": fitting["eids"][0], "_artifact_source_eids": fitting["eids"],
               "needs_review": bool(review), "review_reason": (review[0].get("review_reason") if review else None),
               "level": first.get("level"), "elevation": GC.base_z(cat, first),
               "system": (first.get("overrides") or {}).get("system", first.get("system")),
               "fitting_kind": fitting["kind"], "joints": [{"id": fitting["joint"]}]}
        container(obj, rec, volume_mm3=volume)
        key = f"{cat}:{fitting['kind']}"
        ctx["fittings"]["by_kind"][key] = ctx["fittings"]["by_kind"].get(key, 0) + 1
        ctx["fittings"]["built"] += 1
        stats["fitting"] += 1

    # ── 개구부 — 절삭이 아니라 **선언**(IfcOpeningElement + IfcRelVoidsElement) ──
    #   실측: `ifcopenshell.geom.create_shape`(재검사가 쓰는 그 함수)가 벽 부피에서 교차분을
    #   정확히 뺀다. 우리는 그 교차분을 2.5D(평면 교차 × z 겹침)로 같이 계산해 영수증에 적는다.
    for oi, op in rows("opening"):
        result = {"eid": op["eid"], "requested_hosts": op.get("wall_indices", []),
                  "cut_host_eids": [], "failed_hosts": [], "cuts": [],
                  "already_void": [], "leaf_built": False}
        ctx["openings"].append(result)
        try:
            corners, oz0, oz1, leaf_pts, subtype = _opening_box(op, params)
            oz0, oz1 = oz0 + z_offset, oz1 + z_offset
            cutter_poly = _prism_polygon(corners)
        except Exception as exc:
            result["failed_hosts"].append({"error": str(exc)})
            continue
        for wi in op.get("wall_indices", []):
            hosts = ctx["hosts"].get(wi) or []
            if not hosts:
                result["failed_hosts"].append({"wall_index": wi, "error": "host not built"})
                continue
            touched = False
            for host in hosts:
                before = _void_volume(host["poly"], host["z0"], host["z1"], host["cutters"])
                after = _void_volume(host["poly"], host["z0"], host["z1"],
                                     host["cutters"] + [(cutter_poly, oz0, oz1)])
                removed = after - before
                overlap = cutter_poly.intersection(host["poly"]).area * max(
                    0.0, min(host["z1"], oz1) - max(host["z0"], oz0))
                if overlap <= 1e-6:
                    continue                      # 이 벽에는 안 닿는다 — 아래에서 사유를 적는다
                host_eids = AV.source_eids(host["rec"])
                touched = True
                if removed <= 1e-6:
                    # 먼저 뚫은 개구부가 이미 그 자리를 비웠다 — 실패가 아니다(중복 창호부).
                    result["cut_host_eids"].extend(host_eids)
                    result["already_void"].append({"host_name": host["entry"]["name"], "host_eids": host_eids})
                    continue
                if host["poly"].area * (host["z1"] - host["z0"]) - after <= 1e-6:
                    # ★ 이 개구부가 벽을 **통째로** 비운다. 선언하면 ifcopenshell 은 결과가 빈 불리언을
                    #   조용히 버려 벽이 안 뚫린 채 나가고(재검사는 "부피 0 과 다르다" 는 엉뚱한 말을 한다),
                    #   층을 쌓으면 빈 메시로 경계 계산이 죽었다. FreeCAD 경로와 같이 실패로 적고 뚫지 않는다.
                    result["failed_hosts"].append({"wall_index": wi, "host_name": host["entry"]["name"],
                                                   "error": "cut would consume the whole host"})
                    continue
                result["cut_host_eids"].extend(host_eids)
                void = _extrude_polygon(model, body, sb, corners, oz1 - oz0, oz0,
                                        "IfcOpeningElement", f"Void_{oi}_{wi}")
                if void is None:
                    result["failed_hosts"].append({"wall_index": wi, "error": "void build failed"})
                    continue
                ifcopenshell.api.feature.add_feature(model, feature=void, element=host["product"])
                host["cutters"].append((cutter_poly, oz0, oz1))
                if host["entry"].get("volume_mm3") is not None:
                    host["entry"]["volume_mm3"] -= removed
                result["cuts"].append({"host_name": host["entry"]["name"], "host_eids": host_eids,
                                       "removed_volume_mm3": removed})
                stats["opening"] += 1
            if not touched:
                result["failed_hosts"].append({"wall_index": wi, "error": "cutter did not intersect host"})
        result["cut_host_eids"] = sorted(set(result["cut_host_eids"]))
        if leaf_pts is not None:
            leaf = _extrude_polygon(model, body, sb, leaf_pts, oz1 - oz0, oz0,
                                    "IfcDoor" if subtype == "door" else "IfcWindow",
                                    f"{'Door' if subtype == 'door' else 'Window'}_{oi}")
            if leaf is not None:
                container(leaf, op, volume_mm3=_poly_area(leaf_pts) * (oz1 - oz0))
                result["leaf_built"] = True

    # ── 표준 접합: 맞닿는 벽 연결 후 코너 형상 재생성 ────────────────
    if connect and made:
        n_conn, n_regen = _connect_walls(model, made)
        stats["connected"] = n_conn
        stats["regenerated"] = n_regen
    return stats


def build(geom_path, ifc_path, storey="Level", z_base=0.0, connect=False,
          qto=True):
    """단일 층 geometry.json → IFC. (기존 API 호환)
    connect=True 면 맞닿는 벽을 IFC 표준으로 연결해 코너를 마이터 처리."""
    with open(geom_path, encoding="utf-8") as f:
        data = json.load(f)
    return _build_verified(data, ifc_path, storey, z_base, connect, qto)


def _build_verified(original, ifc_path, storey="Level", z_base=0.0, connect=False, qto=True):
    from verify import verify_geometry, verify_build
    prov = AV.provenance(original)
    prov.update({"builder_path": os.path.abspath(__file__), "builder_sha256": AV.file_hash(__file__)})
    dst = os.path.abspath(ifc_path)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    report_path = os.path.splitext(dst)[0] + ".build.json"
    # Invalidate any old receipt before preflight, unsupported-category checks,
    # dependency/geometry creation, or export can fail.
    initial = {"schema_version": 2, "provenance": prov, "status": "failed",
               "runtime_errors": ["Build has not completed; see the raised build error"],
               "artifacts": {"ifc": {"status": "failed", "path": None, "provenance": prov}}}
    with open(report_path, "w", encoding="utf-8") as stream:
        json.dump(initial, stream, ensure_ascii=False, indent=2)
    pre = verify_geometry(original)
    if pre.failed:
        raise ValueError("Geometry preflight failed: " + pre.text())
    for rec in original.get("elements", {}).get("slab", []):
        if (rec.get("overrides") or {}).get("ifc_type", "Slab") != "Slab":
            raise ValueError("Slab ifc_type override is unsupported")
    data = copy.deepcopy(original)
    AV.prepare_records(data)
    data["_build_provenance"] = prov
    data["_expected_products"] = []
    model = ifcopenshell.api.project.create_file()
    body, bld = _setup_project(model)
    if connect:
        _axis_context(model)
    sb = ifcopenshell.util.shape_builder.ShapeBuilder(model)
    floors = original.get("floors") or [{"label": storey, "z": z_base}]
    stacked = bool(original.get("floors"))
    # 이음은 경로 부재와 같은 층에만 선다 — 구성원이 층을 걸치면 세우지 않고 사유를 남긴다.
    joints = GC.joint_fittings(data["elements"], data.get("params"))
    ctx = {"hosts": {}, "openings": [], "materials": {}, "fittings_for_floor": {},
           "mep_volume": {"expected_mm3": 0.0, "built_mm3": 0.0},
           "fittings": {"built": 0, "by_kind": {}, "straight": joints["straight"],
                        "skipped": list(joints["skipped"])}}
    total = {"wall": 0, "column": 0, "slab": 0, "beam": 0, "zone": 0,
             "mep": 0, "fitting": 0, "opening": 0, "skip": 0, "qto": 0}
    storeys = []
    for fi, floor in enumerate(floors):
        name = floor.get("label") or floor.get("storey") or f"Level_{fi+1}"
        storeys.append(_add_storey(model, bld, name, float(floor.get("z", 0))))
    for fitting in joints["fittings"]:
        members = [rec for _c, rec, _p, _a in fitting["members"]]
        owners = {_floor_index(fitting["category"], rec, floors) if stacked else 0 for rec in members}
        if len(owners) != 1 or None in owners:
            ctx["fittings"]["skipped"].append({"joint": fitting["joint"], "eids": fitting["eids"],
                                               "reason": "members_on_different_storeys"})
            continue
        ctx["fittings_for_floor"].setdefault(id(storeys[owners.pop()]), []).append(fitting)
    for fi, floor in enumerate(floors):
        sto = storeys[fi]
        # ★ 목록을 자르지 않고 **거를 조건**을 넘긴다 — 개구부는 `wall_indices` 로 호스트를
        #   가리키므로 층별로 벽 목록을 자르면 그 번호가 밀려 엉뚱한 벽에 구멍이 난다.
        keep = (lambda c, r, _fi=fi: _floor_index(c, r, floors) == _fi) if stacked else (lambda c, r: True)
        partial = _build_elements(model, body, sb, sto, data,
                    z_offset=0 if stacked else z_base, connect=connect, qto=qto, ctx=ctx, keep=keep)
        for key, value in partial.items():
            total[key] = total.get(key, 0) + value
    el = data.get("elements", {})
    total.update({"schema_version": 2, "provenance": prov, "preflight": pre.to_dict(),
                  # intent = 입력 레코드 수, built = 실제 만든 객체 수. **받아 적기만** 한다 — V101 은
                  # `catalog` 단계 전용이라 여기서는 안 돈다. 부재가 빠졌는지는 `inspect_ifc` 의
                  # EID 대조("Missing exported EID")가 막는다.
                  "intent": {c: len(el.get(c, [])) for c in ("wall", "column", "slab", "beam")},
                  "built": {"walls": total["wall"], "columns": total["column"],
                            "slabs": total["slab"], "beams": total["beam"],
                            "spaces": total["zone"], "mep": total["mep"],
                            "fittings": total["fitting"], "floors": len(storeys)},
                  "opening_results": ctx["openings"],
                  "openings_void": total["opening"],
                  "opening_leaves": sum(1 for o in ctx["openings"] if o.get("leaf_built")),
                  "mep_volume": ctx["mep_volume"],
                  "fittings": dict(ctx["fittings"], skipped=ctx["fittings"]["skipped"][:20]),
                  "materials": dict(ctx["materials"]),
                  "construction_rules": CR.review(data)["receipt"],
                  "floor_orphans": 0, "floor_dups": 0, "invalid_shapes": 0, "bbox": None,
                  # 카테고리별 '객체를 못 만든 레코드' — 전부 0 이어야 정상이고, V106 이 막는다.
                  "unbuilt": {c: {"count": len([u for u in ctx.get("unbuilt", []) if u["category"] == c]),
                                  "detail": [u for u in ctx.get("unbuilt", []) if u["category"] == c][:10]}
                              for c in sorted({u["category"] for u in ctx.get("unbuilt", [])})},
                  "expected_products": data["_expected_products"],
                  "artifacts": {"ifc": {"status": "failed", "path": None, "provenance": prov}}})
    try:
        # ★ 경계 계산도 try 안이다 — 빈 메시 하나로 `ifc_product_bounds` 가 죽으면 영수증에 사유가
        #   안 남고 호출자는 트레이스백만 받았다.
        products = {product.Name: product for product in model.by_type("IfcProduct")}
        for expected in data["_expected_products"]:
            expected["bbox_mm"] = AV.ifc_product_bounds(products[expected["name"]])
        boxes = [e["bbox_mm"] for e in data["_expected_products"]]
        if boxes:
            # 모델 전체 경계 — 있어야 V104(폭주 솔리드)가 돈다. 종전엔 None 으로 박혀 꺼져 있었다.
            total["bbox"] = ([min(b[k] for b in boxes) for k in range(3)]
                             + [max(b[k] for b in boxes) for k in range(3, 6)])
        with tempfile.TemporaryDirectory(prefix="mep_ifc_", dir=os.path.dirname(dst)) as temp_dir:
            temp_ifc = os.path.join(temp_dir, "model.ifc")
            model.write(temp_ifc)
            report = verify_build(original, total, temp_ifc, stage="post_export")
            total["verify"] = report.to_dict()
            total["verify_ifc"] = report.to_dict()
            if report.failed:
                raise ValueError("IFC artifact verification failed: " + report.text())
            os.replace(temp_ifc, dst)
            total["artifacts"]["ifc"] = AV.artifact_receipt(dst, prov)
            total["status"] = "verified"
        return total
    except Exception as exc:
        total["status"] = "failed"
        total["runtime_errors"] = [str(exc)]
        raise
    finally:
        with open(report_path, "w", encoding="utf-8") as stream:
            json.dump(total, stream, ensure_ascii=False, indent=2)


def build_multi(floors, ifc_path, connect=False, qto=True):
    """다층 스태킹: floors=[{"geometry": path, "storey": 이름, "z": 바닥레벨mm}, ...]
    → 층별 IfcBuildingStorey 를 가진 단일 IFC. 반환: 층별 stats 리스트.

    z 를 생략하면 이전 층 z + 이전 층 벽 param 높이로 자동 누적."""
    merged = {"contract": GC.contract_block(), "floors": [], "elements": {}}
    z_auto = 0.0
    for i, floor in enumerate(floors):
        with open(floor["geometry"], encoding="utf-8") as stream:
            data = json.load(stream)
        from verify import verify_geometry
        preflight = verify_geometry(data)
        if preflight.failed:
            raise ValueError("Geometry preflight failed: " + preflight.text())
        z = float(floor["z"]) if floor.get("z") is not None else z_auto
        name = floor.get("storey") or f"Level_{i+1}"
        merged["floors"].append({"z": z, "label": name})
        params = data.get("params") or {}
        if not merged.get("params"):
            # 첫 층 params 를 합친 모델에 싣는다(`stack_build` 와 같은 규약). 레코드 치수는 아래에서
            # 층마다 얼려 두지만 개구부 커터(일반 개구부의 벽 높이)는 params 를 직접 읽는다 —
            # 종전엔 params 가 없어 4m 벽의 개구부가 2.9m 에서 멈췄다.
            merged["params"] = params
        AV.prepare_records(data)
        # ★ `wall_indices` 는 **그 층 안에서의** 위치다 — 이어붙인 만큼 밀지 않으면
        #   위층 문이 아래층 벽을 가리킨다(`stack_build` 와 같은 규약, 같은 사유).
        _wall_base = len(merged["elements"].get("wall", []))
        for rec in data.get("elements", {}).get("opening", []):
            if rec.get("wall_indices"):
                rec["wall_indices"] = [i + _wall_base for i in rec["wall_indices"]]
        for cat, _, rec in AV.records(data):
            rec["eid"] = name + ":" + rec["eid"]
            for joint in rec.get("joints") or []:   # 이음 id 도 층마다 따로(stack_build 와 같은 규약)
                if str(joint.get("id", "")).count(":") == 1:
                    joint["id"] = name + ":" + joint["id"]
            rec["level"] = name
            rec["z_base"] = GC.base_z(cat, rec) + z
            if rec.get("floor_z") is not None:          # 층 판정 높이도 같이(창 위 벽) — stack_build._shift 와 같다
                rec["floor_z"] = float(rec["floor_z"]) + z
            # ★ 벽 두께는 **얼리기 전에** 계약(`width_of`)으로 읽는다 — `_dim` 은 params 를
            #   `width_detected`(실측)보다 위에 두므로, 아래 루프가 먼저 overrides.width 를 채우면
            #   실측 250mm 벽이 전부 layer 기본값 200mm 로 서고 검사도 같은 틀린 수치로 통과했다.
            width = GC.width_of(rec, params) if cat == "wall" else None
            rec["overrides"] = dict(rec.get("overrides") or {})
            for dim in GC.DEFAULT_DIMS.get(cat, {}):
                rec["overrides"].setdefault(dim, GC._dim(cat, dim, rec, params))
            if cat == "wall":
                rec["overrides"]["width"] = width
            merged["elements"].setdefault(cat, []).append(rec)
        z_auto = z + GC.height_of({}, data.get("params"))
    stats = _build_verified(merged, ifc_path, connect=connect, qto=qto)
    return [dict(stats, storey=f["label"], z=f["z"]) for f in merged["floors"]]


def _extrude_polygon(model, body, sb, coords_mm, depth_mm, z_mm, ifc_class, name, holes=None):
    """world 좌표(mm) 닫힌 폴리곤 → z_mm 에서 depth 만큼 위로 돌출한 IFC 객체."""
    def _ring(coords):
        ring = [np.array([x / MM, y / MM]) for x, y in coords]
        if len(ring) >= 2 and abs(ring[0][0] - ring[-1][0]) < 1e-9 and abs(ring[0][1] - ring[-1][1]) < 1e-9:
            ring = ring[:-1]                      # 중복 끝점 제거
        return ring
    try:
        pts = _ring(coords_mm)
        if len(pts) < 3:
            return None
        poly = sb.polyline(pts, closed=True)
        inner = [sb.polyline(r, closed=True) for r in (_ring(h) for h in holes or []) if len(r) >= 3]
        profile = sb.profile(poly, inner_curves=inner) if inner else sb.profile(poly)
        obj = ifcopenshell.api.root.create_entity(model, ifc_class=ifc_class, name=name)
        # 프로파일은 world XY 에 있으므로 배치는 z 만 이동
        M = np.eye(4)
        M[2, 3] = z_mm / MM
        ifcopenshell.api.geometry.edit_object_placement(model, product=obj, matrix=M)
        rep = ifcopenshell.api.geometry.add_profile_representation(
            model, context=body, profile=profile, depth=depth_mm / MM,
            cardinal_point=10)  # 10 = geometric centroid → 오프셋 없음
        ifcopenshell.api.geometry.assign_representation(model, product=obj, representation=rep)
        return obj
    except Exception as e:
        print(f"[warn] {name} 빌드 실패: {e}", file=sys.stderr)
        return None


def main():
    ap = argparse.ArgumentParser(description="geometry.json → IFC (FreeCAD 불필요)")
    ap.add_argument("geometry", nargs="?", default=None,
                    help="geometry.json 경로 (--project 사용 시 생략)")
    ap.add_argument("out", nargs="?", default=None, help="출력 .ifc (기본 <geometry>.ifc)")
    ap.add_argument("--storey", default="Level", help="층 이름")
    ap.add_argument("--z", type=float, default=0.0, help="층 기준 Z(mm)")
    ap.add_argument("--project", default=None,
                    help="다층 스태킹: floors.json 경로 — "
                         '[{"geometry": "b3.json", "storey": "B3", "z": 0}, ...] '
                         "(z 생략 시 벽 높이로 자동 누적)")
    ap.add_argument("--connect", action="store_true",
                    help="맞닿는 벽을 IFC 표준으로 연결 → 코너 마이터 자동 생성")
    ap.add_argument("--no-qto", action="store_true",
                    help="표준 물량셋(Qto_*BaseQuantities) 기입 생략")
    args = ap.parse_args()
    for _s in (sys.stdout, sys.stderr):   # cp949 콘솔 한글 깨짐 방지
        try:
            _s.reconfigure(encoding="utf-8")
        except Exception:
            pass

    def _line(st):
        s = " ".join(f"{k}={st[k]}" for k in
                     ("wall", "column", "slab", "beam", "zone", "mep", "fitting", "opening")
                     if st.get(k))
        s += f" skipped={st['skip']}"
        if st.get("qto"):
            s += f" qto={st['qto']}"
        if "connected" in st:
            s += f" connected={st['connected']} regenerated={st['regenerated']}"
        return s

    if args.project:
        with open(args.project, encoding="utf-8") as f:
            floors = json.load(f)
        out = args.out or args.geometry or \
            (os.path.splitext(args.project)[0] + ".ifc")
        all_stats = build_multi(floors, out, connect=args.connect,
                                qto=not args.no_qto)
        print(f"[OK] 다층 IFC 빌드 -> {out}  ({len(all_stats)}개 층)")
        # `build_multi` 는 **합계** 하나를 층마다 복사해 돌려준다 — 층별로 찍으면 같은 숫자를
        # 층 이름만 바꿔 반복해 층마다 그만큼 지은 것처럼 읽힌다. 합계는 한 번만 말한다.
        for st in all_stats:
            print(f"  [{st['storey']}] z={st['z']:.0f}mm")
        print("  합계: " + _line(all_stats[0]))
        return

    if not args.geometry:
        ap.error("geometry.json 경로 또는 --project 를 지정하세요")
    out = args.out or (os.path.splitext(args.geometry)[0] + ".ifc")
    stats = build(args.geometry, out, storey=args.storey, z_base=args.z,
                  connect=args.connect, qto=not args.no_qto)
    print(f"[OK] IFC 빌드 -> {out}")
    print("  " + _line(stats))


if __name__ == "__main__":
    main()
