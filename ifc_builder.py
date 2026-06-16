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
    import ifcopenshell.util.shape_builder
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


def _setup(model, storey_name):
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
    sto = ifcopenshell.api.root.create_entity(
        model, ifc_class="IfcBuildingStorey", name=storey_name)
    ifcopenshell.api.aggregate.assign_object(model, relating_object=proj, products=[site])
    ifcopenshell.api.aggregate.assign_object(model, relating_object=site, products=[bld])
    ifcopenshell.api.aggregate.assign_object(model, relating_object=bld, products=[sto])
    return body, sto


def build(geom_path, ifc_path, storey="Level", z_base=0.0):
    with open(geom_path, encoding="utf-8") as f:
        data = json.load(f)
    params = data.get("params", {})
    el = data.get("elements", {})
    pw = float(params.get("wall", {}).get("width", 200.0))
    ph = float(params.get("wall", {}).get("height", 2800.0))
    pcol_h = float(params.get("column", {}).get("height", 3000.0))
    pslab_t = float(params.get("slab", {}).get("thickness", 200.0))

    model = ifcopenshell.api.project.create_file()  # IFC4
    body, sto = _setup(model, storey)
    sb = ifcopenshell.util.shape_builder.ShapeBuilder(model)
    stats = {"wall": 0, "column": 0, "slab": 0, "skip": 0}

    def container(prod):
        ifcopenshell.api.spatial.assign_container(
            model, relating_structure=sto, products=[prod])

    # ── 벽 ───────────────────────────────────────────────────────────
    for i, w in enumerate(el.get("wall", [])):
        cl = w.get("centerline") or w.get("points") or []
        if len(cl) < 2:
            stats["skip"] += 1
            continue
        width = float(w.get("width_detected") or w.get("overrides", {}).get("width", pw))
        height = float(w.get("overrides", {}).get("height", ph))
        zb = float(w.get("z_base", z_base))
        # 다중점 centerline → 세그먼트별 벽
        for k in range(len(cl) - 1):
            p1, p2 = cl[k], cl[k + 1]
            L = math.hypot(p2[0] - p1[0], p2[1] - p1[1])
            if L < 1.0:
                continue
            wall = ifcopenshell.api.root.create_entity(
                model, ifc_class="IfcWall", name=f"Wall_{i}_{k}")
            ifcopenshell.api.geometry.edit_object_placement(
                model, product=wall, matrix=_placement_matrix(p1, p2, zb))
            rep = ifcopenshell.api.geometry.add_wall_representation(
                model, context=body, length=L / MM,
                height=height / MM, thickness=width / MM)
            ifcopenshell.api.geometry.assign_representation(
                model, product=wall, representation=rep)
            container(wall)
            stats["wall"] += 1

    # ── 기둥 ─────────────────────────────────────────────────────────
    for i, c in enumerate(el.get("column", [])):
        zb = float(c.get("z_base", z_base))
        h = float(c.get("overrides", {}).get("height", pcol_h))
        coords = None
        if c.get("kind") == "circle":
            cx, cy = c.get("center", [0, 0])
            r = float(c.get("radius", 200.0))
            coords = [(cx - r, cy - r), (cx + r, cy - r), (cx + r, cy + r), (cx - r, cy + r)]
        elif c.get("kind") == "polyline" and c.get("closed") and len(c.get("points", [])) >= 3:
            coords = [(p[0], p[1]) for p in c["points"]]
        if not coords:
            stats["skip"] += 1
            continue
        col = _extrude_polygon(model, body, sb, coords, h, zb, "IfcColumn", f"Col_{i}")
        if col:
            container(col)
            stats["column"] += 1

    # ── 슬래브 ───────────────────────────────────────────────────────
    for i, s in enumerate(el.get("slab", [])):
        if s.get("kind") != "polyline" or not s.get("closed"):
            stats["skip"] += 1
            continue
        pts = s.get("points", [])
        if len(pts) < 3:
            continue
        zb = float(s.get("z_base", z_base))
        thk = float(s.get("overrides", {}).get("thickness", pslab_t))
        coords = [(p[0], p[1]) for p in pts]
        slab = _extrude_polygon(model, body, sb, coords, thk, zb - thk, "IfcSlab", f"Slab_{i}")
        if slab:
            container(slab)
            stats["slab"] += 1

    model.write(ifc_path)
    return stats


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
    ap.add_argument("geometry", help="geometry.json 경로")
    ap.add_argument("out", nargs="?", default=None, help="출력 .ifc (기본 <geometry>.ifc)")
    ap.add_argument("--storey", default="Level", help="층 이름")
    ap.add_argument("--z", type=float, default=0.0, help="층 기준 Z(mm)")
    args = ap.parse_args()
    out = args.out or (os.path.splitext(args.geometry)[0] + ".ifc")
    stats = build(args.geometry, out, storey=args.storey, z_base=args.z)
    print(f"[OK] IFC 빌드 -> {out}")
    print(f"  walls={stats['wall']} columns={stats['column']} slabs={stats['slab']} "
          f"skipped={stats['skip']}")


if __name__ == "__main__":
    main()
