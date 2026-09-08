"""
ifc_builder.py  —  geometry.json → IFC (FreeCAD 불필요)

freecad_builder.py 의 IfcOpenShell 대안. 순수 Python(IfcOpenShell + numpy)으로
geometry.json 을 표준 IFC4 로 직접 빌드한다. FreeCAD 의 크래시/한글경로/recompute
문제 전부 회피. Revit/ArchiCAD/BlenderBIM/우리 preview.py 모두 읽음.

사용:
    python ifc_builder.py geometry.json out.ifc
    python ifc_builder.py geometry.json out.ifc --storey B3 --z 0

설계:
- 벽: centerline(2점) + width_detected(없으면 params.wall.width) + height
      → IfcWall + add_wall_representation(length,height,thickness) + 배치행렬(방향 회전).
      다중점 centerline 은 세그먼트별로 분해.
- 기둥: circle → 정사각 프로파일(2r), closed polyline → 그 폴리곤. IfcColumn.
- 슬래브: closed polyline → IfcSlab(아래로 thickness).
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


def _wall_type(model, thickness_mm, cache):
    """두께별 IfcWallType(+ IfcMaterialLayerSet). 같은 두께는 캐시 재사용."""
    key = round(float(thickness_mm), 1)
    if key in cache:
        return cache[key]
    if not cache:  # 재료는 프로젝트 1개 공유
        cache["_mat"] = ifcopenshell.api.material.add_material(model, name="Concrete")
    mat = cache["_mat"]
    lset = ifcopenshell.api.material.add_material_set(
        model, name=f"W{key:g}", set_type="IfcMaterialLayerSet")
    layer = ifcopenshell.api.material.add_layer(model, layer_set=lset, material=mat)
    ifcopenshell.api.material.edit_layer(
        model, layer=layer,
        attributes={"LayerThickness": key / MM, "Priority": 1})
    wt = ifcopenshell.api.root.create_entity(
        model, ifc_class="IfcWallType", name=f"WALL-{key:g}")
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


def _build_elements(model, body, sb, sto, data, z_offset=0.0, connect=False,
                    type_cache=None, qto=True):
    """한 층의 geometry dict → IFC 요소 생성. z_offset(mm)=층 바닥 레벨.
    요소별 z = z_offset + 레코드 z_base (레코드 값은 층 '내' 오프셋)."""
    params = data.get("params", {})
    el = data.get("elements", {})
    pw = float(params.get("wall", {}).get("width", 200.0))
    ph = float(params.get("wall", {}).get("height", 2800.0))
    pcol_h = float(params.get("column", {}).get("height", 3000.0))
    pslab_t = float(params.get("slab", {}).get("thickness", 200.0))
    stats = {"wall": 0, "column": 0, "slab": 0, "skip": 0, "qto": 0}
    made = []                       # 접합 모드에서 생성된 벽 목록
    if type_cache is None:
        type_cache = {}

    def container(prod, rec):
        prod.Name = sto.Name + ":" + prod.Name
        ifcopenshell.api.spatial.assign_container(
            model, relating_structure=sto, products=[prod])
        pset = ifcopenshell.api.pset.add_pset(model, product=prod, name="Pset_MEPParser")
        values = AV.qa_values(rec, data["_build_provenance"])
        values["EID"] = model.create_entity("IfcText", values["EID"])
        values["SourceEIDs"] = model.create_entity("IfcText", values["SourceEIDs"])
        ifcopenshell.api.pset.edit_pset(model, pset=pset, properties=values)
        data.setdefault("_expected_products", []).append({"name": prod.Name, "source_eids": AV.source_eids(rec),
                                                          "qa": AV.qa_values(rec, data["_build_provenance"])})

    # ── 벽 ───────────────────────────────────────────────────────────
    for i, w in enumerate(el.get("wall", [])):
        cl = w.get("centerline") or w.get("points") or []
        if len(cl) < 2:
            stats["skip"] += 1
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
                container(solid, w)
                if qto:
                    area = _poly_area(pts)
                    _add_qto(model, solid, "Qto_WallBaseQuantities", {
                        "Height": height / MM,
                        "GrossFootprintArea": area / MM ** 2,
                        "GrossVolume": (area * height) / MM ** 3,
                        "NetVolume": (area * height) / MM ** 3,
                    })
                    stats["qto"] += 1
                stats["wall"] += 1
            else:
                stats["skip"] += 1
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
                        relating_type=_wall_type(model, width, type_cache))
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
            container(wall, w)
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
    for i, c in enumerate(el.get("column", [])):
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
            stats["skip"] += 1
            continue
        if col is None:
            col = _extrude_polygon(model, body, sb, coords, h, zb, "IfcColumn", f"Col_{i}")
        if col:
            container(col, c)
            if qto:
                area = math.pi * r * r if c.get("kind") == "circle" else _poly_area(coords)
                _add_qto(model, col, "Qto_ColumnBaseQuantities", {
                    "Length": h / MM,
                    "CrossSectionArea": area / MM ** 2,
                    "GrossVolume": (area * h) / MM ** 3,
                    "NetVolume": (area * h) / MM ** 3,
                })
                stats["qto"] += 1
            stats["column"] += 1

    # ── 슬래브 ───────────────────────────────────────────────────────
    for i, s in enumerate(el.get("slab", [])):
        if s.get("kind") != "polyline" or not s.get("closed"):
            stats["skip"] += 1
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
            container(slab, s)
            if qto:
                area = _poly_area(coords)
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
    unsupported = [c for c, rows in original.get("elements", {}).items()
                   if rows and c not in ("wall", "column", "slab")]
    if unsupported:
        raise ValueError("Simple IFC does not support " + ", ".join(unsupported) +
                         "; use the FreeCAD build for these categories")
    pre = verify_geometry(original)
    if pre.failed:
        raise ValueError("Geometry preflight failed: " + pre.text())
    for rec in original.get("elements", {}).get("slab", []):
        if (rec.get("overrides") or {}).get("ifc_type", "Slab") != "Slab":
            raise ValueError("Simple IFC slab type override is unsupported; use FreeCAD")
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
    total = {"wall": 0, "column": 0, "slab": 0, "skip": 0, "qto": 0}
    for fi, floor in enumerate(floors):
        name = floor.get("label") or floor.get("storey") or f"Level_{fi+1}"
        z = float(floor.get("z", 0))
        sto = _add_storey(model, bld, name, z)
        subset = dict(data)
        if original.get("floors"):
            subset["elements"] = {c: [r for r in rows if abs(GC.base_z(c, r)-z) < 100 and
                                     (not r.get("level") or r["level"] in (floor.get("id"), name))]
                                  for c, rows in data["elements"].items() if c in ("wall", "column", "slab")}
        partial = _build_elements(model, body, sb, sto, subset,
                    z_offset=0 if original.get("floors") else z_base, connect=connect, qto=qto)
        for key, value in partial.items():
            total[key] = total.get(key, 0) + value
    products = {product.Name: product for product in model.by_type("IfcProduct")}
    for expected in data["_expected_products"]:
        expected["bbox_mm"] = AV.ifc_product_bounds(products[expected["name"]])
    total.update({"schema_version": 2, "provenance": prov, "preflight": pre.to_dict(),
                  "intent": {c: total[c] for c in ("wall", "column", "slab")},
                  "built": {"walls": total["wall"], "columns": total["column"], "slabs": total["slab"]},
                  "floor_orphans": 0, "floor_dups": 0, "invalid_shapes": 0, "bbox": None,
                  "unbuilt": {"records": {"count": total["skip"]}} if total["skip"] else {},
                  "expected_products": data["_expected_products"],
                  "artifacts": {"ifc": {"status": "failed", "path": None, "provenance": prov}}})
    try:
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
        AV.prepare_records(data)
        for cat, _, rec in AV.records(data):
            rec["eid"] = name + ":" + rec["eid"]
            rec["level"] = name
            rec["z_base"] = GC.base_z(cat, rec) + z
            rec["overrides"] = dict(rec.get("overrides") or {})
            for dim in GC.DEFAULT_DIMS.get(cat, {}):
                rec["overrides"].setdefault(dim, GC._dim(cat, dim, rec, data.get("params")))
            if cat == "wall":
                rec["overrides"]["width"] = GC.width_of(rec, data.get("params"))
            merged["elements"].setdefault(cat, []).append(rec)
        z_auto = z + GC.height_of({}, data.get("params"))
    stats = _build_verified(merged, ifc_path, connect=connect, qto=qto)
    return [dict(stats, storey=f["label"], z=f["z"]) for f in merged["floors"]]


def _extrude_polygon(model, body, sb, coords_mm, depth_mm, z_mm, ifc_class, name):
    """world 좌표(mm) 닫힌 폴리곤 → z_mm 에서 depth 만큼 위로 돌출한 IFC 객체."""
    try:
        pts = [np.array([x / MM, y / MM]) for x, y in coords_mm]
        # 중복 끝점 제거
        if len(pts) >= 2 and abs(pts[0][0] - pts[-1][0]) < 1e-9 and abs(pts[0][1] - pts[-1][1]) < 1e-9:
            pts = pts[:-1]
        if len(pts) < 3:
            return None
        poly = sb.polyline(pts, closed=True)
        profile = sb.profile(poly)
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
        s = (f"walls={st['wall']} columns={st['column']} slabs={st['slab']} "
             f"skipped={st['skip']}")
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
        for st in all_stats:
            print(f"  [{st['storey']}] z={st['z']:.0f}mm  " + _line(st))
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
