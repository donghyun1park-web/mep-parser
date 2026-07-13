"""
cfd_export.py — config(또는 도면 geometry.json) → OpenFOAM 케이스 생성기

Phase 1c: 파라메트릭 config → buoyantBoussinesqSimpleFoam 케이스(blockMesh 박스방 + 급/배기 +
발열 바닥). Phase 1b 에서 WSL로 '수렴 확인된' 케이스를 재현한다.
Phase 3 에서 --from-geometry(도면 zone/opening/equipment) 입력 모드 추가.

정적 dict(fvSchemes/fvSolution/controlDict/transportProperties/turbulenceProperties)는
cfd_templates/elec_heat_bsq/ 에서 복사, 파라메트릭 파일(blockMeshDict·g·0/*·Allrun)은 생성.
외부 의존성 없음(stdlib). 결정론.

사용:
  python cfd_export.py cfd_configs/elec_room_pilot.json -o case_pilot
  (그 다음: python cfd_run.py case_pilot  →  python cfd_report.py case_pilot)
"""
import argparse
import json
import os
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATE_DIR = os.path.join(HERE, "cfd_templates", "elec_heat_bsq")

# 박스 6면 정의: 이름 → (blockMesh 정점 인덱스 4개, 기본 역할)
# 정점: 0(0,0,0) 1(L,0,0) 2(L,W,0) 3(0,W,0) 4(0,0,H) 5(L,0,H) 6(L,W,H) 7(0,W,H)
_FACES = {
    "floor":   ((0, 3, 2, 1), "wall"),    # z=0
    "ceiling": ((4, 5, 6, 7), "wall"),    # z=H
    "x0":      ((0, 4, 7, 3), "wall"),    # x=0
    "xL":      ((1, 2, 6, 5), "wall"),    # x=L
    "y0":      ((0, 1, 5, 4), "wall"),    # y=0
    "yW":      ((3, 7, 6, 2), "wall"),    # y=W
}
_FOAM_HDR = ("FoamFile {{ version 2.0; format ascii; class {cls}; object {obj}; }}\n")


def _hdr(cls, obj):
    return _FOAM_HDR.format(cls=cls, obj=obj)


# 공기 물성(발열 kW ↔ 온도 변환용). Boussinesq 는 kinematic 이라 ρ0·cp 로 W↔K·m³/s 환산.
RHO0_AIR = 1.2      # kg/m³ (~20°C)
CP_AIR = 1005.0     # J/(kg·K)
RHO_CP = RHO0_AIR * CP_AIR   # 1206 J/(m³·K)


def _heat_mode(cfg):
    """발열 방식 판정.
    - volume: heat.power_kw 지정 → 바닥층 체적 발열원(fvOptions). 계산서 kW 직결, 에너지 폐합 검증 가능.
    - surface: heat.floor_T 지정 → 바닥 고정온도(구식, 하위호환).
    - none: 발열 없음."""
    heat = cfg.get("heat", {})
    if heat.get("power_kw") is not None:
        return {"mode": "volume", "power_w": float(heat["power_kw"]) * 1000.0,
                "zone_frac": float(heat.get("zone_frac", 0.4))}
    if heat.get("floor_T") is not None:
        return {"mode": "surface", "floor_T": heat["floor_T"]}
    return {"mode": "none"}


def _roles(cfg):
    """config → {face_name: role}. role: wall|heated|inlet|outlet.
    발열이 surface(고정온도) 모드일 때만 벽을 heated 로 표시. volume(kW) 모드는
    벽 전부 단열 → 발열은 체적원으로 주입."""
    roles = {name: base for name, (_, base) in _FACES.items()}
    heat = cfg.get("heat", {})
    if _heat_mode(cfg)["mode"] == "surface" and "wall" in heat:
        roles[heat["wall"]] = "heated"
    for key in ("inlet", "outlet"):
        spec = cfg.get(key)
        if spec and spec.get("wall"):
            roles[spec["wall"]] = key
    return roles


def gen_blockmesh(cfg, roles):
    L = cfg["room"]["L"]; W = cfg["room"]["W"]; H = cfg["room"]["H"]
    cell = cfg.get("mesh", {}).get("cell", 0.3)
    nx, ny, nz = max(1, round(L / cell)), max(1, round(W / cell)), max(1, round(H / cell))
    verts = [(0, 0, 0), (L, 0, 0), (L, W, 0), (0, W, 0),
             (0, 0, H), (L, 0, H), (L, W, H), (0, W, H)]
    s = _hdr("dictionary", "blockMeshDict") + "scale 1;\nvertices\n(\n"
    for v in verts:
        s += f"    ({v[0]} {v[1]} {v[2]})\n"
    s += ")\n;\n"
    s += f"blocks ( hex (0 1 2 3 4 5 6 7) ({nx} {ny} {nz}) simpleGrading (1 1 1) );\n"
    s += "edges ();\nboundary\n(\n"
    # 같은 역할끼리 묶되, wall(sideWalls)만 병합, 나머지는 개별 패치
    wall_faces = []
    for name, (idx, _) in _FACES.items():
        role = roles[name]
        ptype = "patch" if role in ("inlet", "outlet") else "wall"
        pname = {"inlet": "inlet", "outlet": "outlet",
                 "heated": "floor" if name == "floor" else name}.get(role, name)
        if role in ("wall",):
            wall_faces.append((name, idx))
            continue
        s += f"    {pname} {{ type {ptype}; faces ( ({idx[0]} {idx[1]} {idx[2]} {idx[3]}) ); }}\n"
    # heated floor 는 위에서 'floor' 이름으로 이미 나감. 나머지 wall 은 sideWalls 로 병합
    if wall_faces:
        faces_str = " ".join(f"({i[0]} {i[1]} {i[2]} {i[3]})" for _, i in wall_faces)
        s += f"    sideWalls {{ type wall; faces ( {faces_str} ); }}\n"
    s += ");\nmergePatchPairs ();\n"
    return s, {"nx": nx, "ny": ny, "nz": nz, "cells": nx * ny * nz}


def gen_g(cfg):
    g = cfg.get("g", [0, 0, -9.81])
    return (_hdr("uniformDimensionedVectorField", "g")
            + "dimensions [0 1 -2 0 0 0 0];\n"
            + f"value ({g[0]} {g[1]} {g[2]});\n")


# ── v2: 사각 급배기구(openings) — topoSet faceSet + createPatch ──────────────
# 실측 크기·위치의 디퓨저/취출구/배기구 N개를 균일 blockMesh 격자 위에 패치로 분리.
# (WSL v1912 수동 검증 완료: boxToFace faceSet 4면 → createPatch sup0, Mesh OK)

# wall → (법선축, 평면좌표 키, (cx축, cy축)) — cx,cy 는 그 벽 평면의 2D 좌표(m)
_WALL_PLANES = {
    "x0": ("x", 0.0, ("y", "z")), "xL": ("x", "L", ("y", "z")),
    "y0": ("y", 0.0, ("x", "z")), "yW": ("y", "W", ("x", "z")),
    "floor": ("z", 0.0, ("x", "y")), "ceiling": ("z", "H", ("x", "y")),
}
_WALL_NORMAL_IN = {  # 실내로 향하는 법선(급기 취출 방향)
    "x0": (1, 0, 0), "xL": (-1, 0, 0), "y0": (0, 1, 0), "yW": (0, -1, 0),
    "floor": (0, 0, 1), "ceiling": (0, 0, -1),
}


def _snap_1d(lo, hi, h, nmax):
    """[lo,hi] 구간을 셀 경계(간격 h)에 스냅. 최소 1셀 보장. 반환 (lo', hi')."""
    i0 = int(round(lo / h))
    i1 = int(round(hi / h))
    i0 = max(0, min(i0, nmax - 1))
    i1 = max(i0 + 1, min(i1, nmax))
    return i0 * h, i1 * h


def resolve_openings(cfg, meshinfo):
    """cfg['openings'] → 패치 정의 목록(스냅·풍량보존·취출벡터 계산).
    반환: [{name, role, type, wall, rect_req, rect_snap, area, U, T, cmh, cmh_req}, ...]
    4way 는 4분면 패치 4개로 전개(각각 바깥쪽 수평+하향 벡터)."""
    room = cfg["room"]
    L, W, H = room["L"], room["W"], room["H"]
    dims = {"x": L, "y": W, "z": H}
    ncell = {"x": meshinfo["nx"], "y": meshinfo["ny"], "z": meshinfo["nz"]}
    Tsup_default = float(cfg.get("inlet", {}).get("T", 293.0))
    patches = []
    n_sup = n_exh = 0
    for op in cfg.get("openings", []):
        wall = op.get("wall")
        if wall not in _WALL_PLANES:
            raise SystemExit(f"openings: wall '{wall}' 는 {list(_WALL_PLANES)} 중 하나")
        nax, _, (uax, vax) = _WALL_PLANES[wall]
        hu = dims[uax] / ncell[uax]
        hv = dims[vax] / ncell[vax]
        cx, cy = float(op["cx"]), float(op["cy"])
        w, h = float(op["w"]), float(op["h"])
        u0, u1 = _snap_1d(cx - w / 2, cx + w / 2, hu, ncell[uax])
        v0, v1 = _snap_1d(cy - h / 2, cy + h / 2, hv, ncell[vax])
        if not (0 <= u0 < u1 <= dims[uax] and 0 <= v0 < v1 <= dims[vax]):
            raise SystemExit(f"openings: '{wall}' ({cx},{cy}) {w}x{h} 가 벽 범위 밖")
        area = (u1 - u0) * (v1 - v0)
        role = op.get("role", "supply")
        typ = op.get("type", "grille")
        n = _WALL_NORMAL_IN[wall]
        base = {"role": role, "type": typ, "wall": wall,
                "rect_req": [cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2],
                "rect_snap": [round(u0, 4), round(v0, 4), round(u1, 4), round(v1, 4)],
                "uax": uax, "vax": vax}
        if role == "exhaust":
            patches.append({**base, "name": f"exh{n_exh}", "area": round(area, 4),
                            "U": None, "T": None, "cmh": None, "cmh_req": None})
            n_exh += 1
            continue
        cmh = op.get("cmh")
        if cmh is None:
            raise SystemExit(f"openings: supply '{wall}' 에 cmh 필수(계산서 풍량)")
        Q = float(cmh) / 3600.0
        Tsup = float(op.get("T", Tsup_default))
        k_in = float(op.get("k", 0.01))          # 급기 난류(벤치마크 재현용 오버라이드)
        eps_in = float(op.get("epsilon", 0.01))
        if typ == "4way":
            # 4분면 분할(스냅된 중앙선 기준) — 각 분면 바깥쪽 대각 수평 + 질량용 하향
            um = _snap_1d(cx, cx, hu, ncell[uax])[0]
            vm = _snap_1d(cy, cy, hv, ncell[vax])[0]
            um = min(max(um, u0 + hu), u1 - hu) if (u1 - u0) > 2 * hu else (u0 + u1) / 2
            vm = min(max(vm, v0 + hv), v1 - hv) if (v1 - v0) > 2 * hv else (v0 + v1) / 2
            quads = [(u0, v0, um, vm, -1, -1), (um, v0, u1, vm, +1, -1),
                     (u0, vm, um, v1, -1, +1), (um, vm, u1, v1, +1, +1)]
            u_n = Q / area                                  # 법선(질량) 성분
            u_h = float(op.get("throat_v", max(1.0, 4.0 * u_n)))  # 수평 취출 성분
            for qi, (a0, b0, a1, b1, su, sv) in enumerate(quads):
                qarea = (a1 - a0) * (b1 - b0)
                if qarea <= 0:
                    continue
                vec = {"x": 0.0, "y": 0.0, "z": 0.0}
                vec[uax] = su * u_h
                vec[vax] = sv * u_h
                # 법선 성분(실내 방향)
                for ax, comp in zip(("x", "y", "z"), n):
                    if comp:
                        vec[ax] = comp * u_n
                patches.append({**base, "name": f"sup{n_sup}_q{qi}",
                                "rect_snap": [round(a0, 4), round(b0, 4), round(a1, 4), round(b1, 4)],
                                "area": round(qarea, 4),
                                "U": [round(vec["x"], 4), round(vec["y"], 4), round(vec["z"], 4)],
                                "T": Tsup, "cmh": round(u_n * qarea * 3600, 1),
                                "cmh_req": float(cmh) / 4, "k": k_in, "epsilon": eps_in})
        else:
            # grille(법선 취출) / down(천장 하향 = ceiling 의 법선과 동일)
            u_mag = Q / area                                # 스냅 실면적으로 역산 → CMH 정확
            vec = [round(n[0] * u_mag, 4), round(n[1] * u_mag, 4), round(n[2] * u_mag, 4)]
            patches.append({**base, "name": f"sup{n_sup}", "area": round(area, 4),
                            "U": vec, "T": Tsup,
                            "cmh": round(u_mag * area * 3600, 1), "cmh_req": float(cmh),
                            "k": k_in, "epsilon": eps_in})
        n_sup += 1
    if patches and not any(p["role"] == "exhaust" for p in patches):
        raise SystemExit("openings: exhaust(배기) 가 최소 1개 필요(압력출구)")
    return patches


def _face_box(p, room):
    """패치의 boxToFace 박스(법선 방향 ±1mm 얇은 박스) 좌표."""
    L, W, H = room["L"], room["W"], room["H"]
    nax, plane, (uax, vax) = _WALL_PLANES[p["wall"]]
    pc = {"L": L, "W": W, "H": H}[plane] if isinstance(plane, str) else plane
    a0, b0, a1, b1 = p["rect_snap"]
    lo = {"x": None, "y": None, "z": None}
    hi = {"x": None, "y": None, "z": None}
    lo[nax], hi[nax] = pc - 0.001, pc + 0.001
    lo[uax], hi[uax] = a0, a1
    lo[vax], hi[vax] = b0, b1
    return (lo["x"], lo["y"], lo["z"], hi["x"], hi["y"], hi["z"])


def gen_toposet_all(cfg, patches):
    """topoSetDict 통합: openings faceSet 들 + (발열 volume 모드면) heatZone cellZone."""
    room = cfg["room"]
    s = _hdr("dictionary", "topoSetDict") + "actions\n(\n"
    for p in patches:
        b = _face_box(p, room)
        s += (f"    {{ name {p['name']}f; type faceSet; action new; source boxToFace;\n"
              f"      box ({b[0]:.4f} {b[1]:.4f} {b[2]:.4f}) ({b[3]:.4f} {b[4]:.4f} {b[5]:.4f}); }}\n")
    if _heat_mode(cfg)["mode"] == "volume":
        L, W, H = room["L"], room["W"], room["H"]
        zh = round(_heat_mode(cfg)["zone_frac"] * H, 4)
        s += (f"    {{ name heatZone; type cellSet;     action new; source boxToCell; box (0 0 0) ({L} {W} {zh}); }}\n"
              "    { name heatZone; type cellZoneSet; action new; source setToCellZone; set heatZone; }\n")
    s += ");\n"
    return s


def gen_createpatch(patches):
    """createPatchDict — faceSet 마다 패치 생성(원본 벽 패치에서 면 분리)."""
    s = _hdr("dictionary", "createPatchDict") + "pointSync false;\npatches\n(\n"
    for p in patches:
        s += (f"    {{ name {p['name']}; patchInfo {{ type patch; }} "
              f"constructFrom set; set {p['name']}f; }}\n")
    s += ");\n"
    return s


def gen_0_openings(cfg, patches):
    """openings 모드 0/ 필드: 벽 전부 단열(sideWalls) + 급기/배기 패치별 BC."""
    Tinit = cfg.get("init", {}).get("T", 300)
    sups = [p for p in patches if p["role"] == "supply"]
    exhs = [p for p in patches if p["role"] == "exhaust"]

    def bc(wall_e, sup_e, exh_e):
        out = f"    sideWalls {wall_e}\n"
        for p in sups:
            out += f"    {p['name']} {sup_e(p)}\n"
        for p in exhs:
            out += f"    {p['name']} {exh_e}\n"
        return out

    files = {}
    files["U"] = (_hdr("volVectorField", "U") + "dimensions [0 1 -1 0 0 0 0];\n"
                  "internalField uniform (0 0 0);\nboundaryField\n{\n"
                  + bc("{ type noSlip; }",
                       lambda p: f"{{ type fixedValue; value uniform ({p['U'][0]} {p['U'][1]} {p['U'][2]}); }}",
                       "{ type pressureInletOutletVelocity; value uniform (0 0 0); }")
                  + "}\n")
    files["T"] = (_hdr("volScalarField", "T") + "dimensions [0 0 0 1 0 0 0];\n"
                  f"internalField uniform {Tinit};\nboundaryField\n{{\n"
                  + bc("{ type zeroGradient; }",
                       lambda p: f"{{ type fixedValue; value uniform {p['T']}; }}",
                       f"{{ type inletOutlet; inletValue uniform {Tinit}; value uniform {Tinit}; }}")
                  + "}\n")
    files["p_rgh"] = (_hdr("volScalarField", "p_rgh") + "dimensions [0 2 -2 0 0 0 0];\n"
                      "internalField uniform 0;\nboundaryField\n{\n"
                      + bc("{ type fixedFluxPressure; rho rhok; value uniform 0; }",
                           lambda p: "{ type fixedFluxPressure; rho rhok; value uniform 0; }",
                           "{ type fixedValue; value uniform 0; }")
                      + "}\n")
    files["p"] = (_hdr("volScalarField", "p") + "dimensions [0 2 -2 0 0 0 0];\n"
                  'internalField uniform 0;\nboundaryField { ".*" { type calculated; value uniform 0; } }\n')
    files["k"] = (_hdr("volScalarField", "k") + "dimensions [0 2 -2 0 0 0 0];\n"
                  "internalField uniform 0.01;\nboundaryField\n{\n"
                  + bc("{ type kqRWallFunction; value uniform 0.01; }",
                       lambda p: f"{{ type fixedValue; value uniform {p.get('k', 0.01):.6g}; }}",
                       "{ type inletOutlet; inletValue uniform 0.01; value uniform 0.01; }")
                  + "}\n")
    files["epsilon"] = (_hdr("volScalarField", "epsilon") + "dimensions [0 2 -3 0 0 0 0];\n"
                        "internalField uniform 0.01;\nboundaryField\n{\n"
                        + bc("{ type epsilonWallFunction; value uniform 0.01; }",
                             lambda p: f"{{ type fixedValue; value uniform {p.get('epsilon', 0.01):.6g}; }}",
                             "{ type inletOutlet; inletValue uniform 0.01; value uniform 0.01; }")
                        + "}\n")
    files["nut"] = (_hdr("volScalarField", "nut") + "dimensions [0 2 -1 0 0 0 0];\n"
                    "internalField uniform 0;\nboundaryField\n{\n"
                    + bc("{ type nutkWallFunction; value uniform 0; }",
                         lambda p: "{ type calculated; value uniform 0; }",
                         "{ type calculated; value uniform 0; }")
                    + "}\n")
    files["alphat"] = (_hdr("volScalarField", "alphat") + "dimensions [0 2 -1 0 0 0 0];\n"
                       "internalField uniform 0;\nboundaryField\n{\n"
                       + bc("{ type alphatJayatillekeWallFunction; Prt 0.85; value uniform 0; }",
                            lambda p: "{ type calculated; value uniform 0; }",
                            "{ type calculated; value uniform 0; }")
                       + "}\n")
    return files


def gen_toposet(cfg):
    """바닥층 cellZone(heatZone) — 체적 발열원을 넣을 영역. 방 바닥~zone_frac·H 높이."""
    L, W, H = cfg["room"]["L"], cfg["room"]["W"], cfg["room"]["H"]
    zh = round(_heat_mode(cfg)["zone_frac"] * H, 4)
    return (_hdr("dictionary", "topoSetDict") + "actions\n(\n"
            f"    {{ name heatZone; type cellSet;     action new; source boxToCell; box (0 0 0) ({L} {W} {zh}); }}\n"
            "    { name heatZone; type cellZoneSet; action new; source setToCellZone; set heatZone; }\n"
            ");\n")


def gen_fvoptions(cfg):
    """체적 발열원(scalarSemiImplicitSource). Su = P/(ρ0·cp) [K·m³/s], volumeMode absolute.
    → 총 발열량(계산서 kW)을 정확히 주입, 에너지 폐합 검증의 기준값."""
    hm = _heat_mode(cfg)
    su = hm["power_w"] / RHO_CP
    return (_hdr("dictionary", "fvOptions")
            + "heatSource\n{\n"
            "    type            scalarSemiImplicitSource;\n"
            "    volumeMode      absolute;\n"
            "    selectionMode   cellZone;\n"
            "    cellZone        heatZone;\n"
            f"    injectionRateSuSp {{ T ({su:.6g} 0); }}\n"
            "}\n")


def gen_allrun(need_toposet, need_createpatch=False):
    """Allrun 생성. 발열 kW/openings 면 topoSet, openings 면 createPatch 단계 추가."""
    toposet = ('echo "=== topoSet ==="\ntopoSet > log.topoSet 2>&1 || '
               '{ echo "topoSet FAILED"; tail -20 log.topoSet; exit 1; }\n') if need_toposet else ""
    createpatch = ('echo "=== createPatch ==="\ncreatePatch -overwrite > log.createPatch 2>&1 || '
                   '{ echo "createPatch FAILED"; tail -20 log.createPatch; exit 1; }\n') if need_createpatch else ""
    return ("#!/bin/sh\n"
            "# RunFunctions 비의존(apt OpenFOAM 패키지엔 없음): 직접 호출 + 로그 리다이렉트.\n"
            'cd "${0%/*}" || exit\n'
            r"APP=$(sed -n 's/^application  *\([A-Za-z][A-Za-z]*\);.*/\1/p' system/controlDict)" + "\n"
            ': "${APP:=buoyantBoussinesqSimpleFoam}"\n'
            'echo "=== blockMesh ==="\n'
            'blockMesh > log.blockMesh 2>&1 || { echo "blockMesh FAILED"; tail -20 log.blockMesh; exit 1; }\n'
            + toposet + createpatch
            + 'echo "=== checkMesh ==="\n'
            "checkMesh > log.checkMesh 2>&1; grep -E 'Mesh OK|\\*\\*\\*' log.checkMesh | head -3\n"
            'echo "=== solver ($APP) ==="\n'
            '"$APP" 2>&1 | tee "log.$APP"\n'
            'echo "=== done: $APP ==="\n')


def _patch_names(roles):
    """생성된 실제 패치 이름 목록 + 벽패치(정규식용) 목록."""
    names = set()
    walls = []
    for face, role in roles.items():
        if role == "inlet":
            names.add("inlet")
        elif role == "outlet":
            names.add("outlet")
        elif role == "heated":
            names.add("floor" if face == "floor" else face)
        else:
            walls.append(face)
    if walls:
        names.add("sideWalls")
    heated = [("floor" if f == "floor" else f) for f, r in roles.items() if r == "heated"]
    return sorted(names), heated


def gen_0(cfg, roles):
    """0/ 필드 8종 생성 (elec_min 검증본과 동일 BC 구성)."""
    inlet = cfg.get("inlet", {})
    U = inlet.get("U", [0.05, 0, 0])
    Tsup = inlet.get("T", 293)
    Tfloor = cfg.get("heat", {}).get("floor_T", 313)
    Tinit = cfg.get("init", {}).get("T", 300)
    names, heated = _patch_names(roles)
    wall_re = "|".join(n for n in names if n in ("floor", "ceiling", "sideWalls") or n in heated)
    has_inlet = "inlet" in names
    has_outlet = "outlet" in names

    def wl(entry_wall, entry_inlet, entry_outlet):
        """패치별 엔트리 조립."""
        out = ""
        for n in names:
            if n == "inlet":
                out += f"    inlet {entry_inlet}\n"
            elif n == "outlet":
                out += f"    outlet {entry_outlet}\n"
            else:
                out += f"    {n} {entry_wall(n)}\n"
        return out

    files = {}
    # U
    files["U"] = (_hdr("volVectorField", "U") + "dimensions [0 1 -1 0 0 0 0];\n"
                  "internalField uniform (0 0 0);\nboundaryField\n{\n"
                  + wl(lambda n: "{ type noSlip; }",
                       f"{{ type fixedValue; value uniform ({U[0]} {U[1]} {U[2]}); }}",
                       "{ type pressureInletOutletVelocity; value uniform (0 0 0); }")
                  + "}\n")
    # T
    def T_wall(n):
        return (f"{{ type fixedValue; value uniform {Tfloor}; }}" if n in heated
                else "{ type zeroGradient; }")
    files["T"] = (_hdr("volScalarField", "T") + "dimensions [0 0 0 1 0 0 0];\n"
                  f"internalField uniform {Tinit};\nboundaryField\n{{\n"
                  + wl(T_wall,
                       f"{{ type fixedValue; value uniform {Tsup}; }}",
                       f"{{ type inletOutlet; inletValue uniform {Tsup}; value uniform {Tsup}; }}")
                  + "}\n")
    # p_rgh
    files["p_rgh"] = (_hdr("volScalarField", "p_rgh") + "dimensions [0 2 -2 0 0 0 0];\n"
                      "internalField uniform 0;\nboundaryField\n{\n"
                      + wl(lambda n: "{ type fixedFluxPressure; rho rhok; value uniform 0; }",
                           "{ type fixedFluxPressure; rho rhok; value uniform 0; }",
                           "{ type fixedValue; value uniform 0; }")
                      + "}\n")
    # p
    files["p"] = (_hdr("volScalarField", "p") + "dimensions [0 2 -2 0 0 0 0];\n"
                  'internalField uniform 0;\nboundaryField { ".*" { type calculated; value uniform 0; } }\n')
    # k
    files["k"] = (_hdr("volScalarField", "k") + "dimensions [0 2 -2 0 0 0 0];\n"
                  "internalField uniform 0.01;\nboundaryField\n{\n"
                  + wl(lambda n: "{ type kqRWallFunction; value uniform 0.01; }",
                       "{ type fixedValue; value uniform 0.01; }",
                       "{ type inletOutlet; inletValue uniform 0.01; value uniform 0.01; }")
                  + "}\n")
    # epsilon
    files["epsilon"] = (_hdr("volScalarField", "epsilon") + "dimensions [0 2 -3 0 0 0 0];\n"
                        "internalField uniform 0.01;\nboundaryField\n{\n"
                        + wl(lambda n: "{ type epsilonWallFunction; value uniform 0.01; }",
                             "{ type fixedValue; value uniform 0.01; }",
                             "{ type inletOutlet; inletValue uniform 0.01; value uniform 0.01; }")
                        + "}\n")
    # nut
    files["nut"] = (_hdr("volScalarField", "nut") + "dimensions [0 2 -1 0 0 0 0];\n"
                    "internalField uniform 0;\nboundaryField\n{\n"
                    + wl(lambda n: "{ type nutkWallFunction; value uniform 0; }",
                         "{ type calculated; value uniform 0; }",
                         "{ type calculated; value uniform 0; }")
                    + "}\n")
    # alphat
    files["alphat"] = (_hdr("volScalarField", "alphat") + "dimensions [0 2 -1 0 0 0 0];\n"
                       "internalField uniform 0;\nboundaryField\n{\n"
                       + wl(lambda n: "{ type alphatJayatillekeWallFunction; Prt 0.85; value uniform 0; }",
                            "{ type calculated; value uniform 0; }",
                            "{ type calculated; value uniform 0; }")
                       + "}\n")
    return files


def build_case(cfg, out_dir):
    roles = _roles(cfg)
    if not os.path.isdir(TEMPLATE_DIR):
        raise SystemExit(f"템플릿 없음: {TEMPLATE_DIR}")
    if os.path.exists(out_dir):
        shutil.rmtree(out_dir)
    os.makedirs(os.path.join(out_dir, "0"))
    os.makedirs(os.path.join(out_dir, "system"), exist_ok=True)
    os.makedirs(os.path.join(out_dir, "constant"), exist_ok=True)
    # 정적 템플릿 복사
    for sub in ("system", "constant"):
        for fn in os.listdir(os.path.join(TEMPLATE_DIR, sub)):
            shutil.copy(os.path.join(TEMPLATE_DIR, sub, fn), os.path.join(out_dir, sub, fn))
    # controlDict endTime 반영
    if "endTime" in cfg:
        cd_path = os.path.join(out_dir, "system", "controlDict")
        with open(cd_path, encoding="utf-8") as f:
            cd = f.read()
        import re
        cd = re.sub(r"endTime\s+[\d.]+", f"endTime         {cfg['endTime']}", cd)
        with open(cd_path, "w", encoding="utf-8") as f:
            f.write(cd)
    # 파라메트릭 생성
    has_openings = bool(cfg.get("openings"))
    if has_openings:
        # v2 급배기구 모드: 6면 전부 벽(sideWalls) → 패치는 topoSet+createPatch 로 분리
        if _heat_mode(cfg)["mode"] == "surface":
            raise SystemExit("openings 모드는 발열을 power_kw(체적)로 지정하세요(floor_T 불가)")
        roles = {name: "wall" for name in _FACES}
    bm, meshinfo = gen_blockmesh(cfg, roles)
    _w(os.path.join(out_dir, "system", "blockMeshDict"), bm)
    _w(os.path.join(out_dir, "constant", "g"), gen_g(cfg))
    hm = _heat_mode(cfg)
    patches = []
    if has_openings:
        patches = resolve_openings(cfg, meshinfo)
        for name, txt in gen_0_openings(cfg, patches).items():
            _w(os.path.join(out_dir, "0", name), txt)
        _w(os.path.join(out_dir, "system", "topoSetDict"), gen_toposet_all(cfg, patches))
        _w(os.path.join(out_dir, "system", "createPatchDict"), gen_createpatch(patches))
        if hm["mode"] == "volume":
            _w(os.path.join(out_dir, "constant", "fvOptions"), gen_fvoptions(cfg))
    else:
        for name, txt in gen_0(cfg, roles).items():
            _w(os.path.join(out_dir, "0", name), txt)
        # 발열 kW 모드: 바닥층 cellZone + 체적 발열원
        if hm["mode"] == "volume":
            _w(os.path.join(out_dir, "system", "topoSetDict"), gen_toposet(cfg))
            _w(os.path.join(out_dir, "constant", "fvOptions"), gen_fvoptions(cfg))
    _w(os.path.join(out_dir, "Allrun"),
       gen_allrun(need_toposet=(hm["mode"] == "volume" or has_openings),
                  need_createpatch=has_openings))
    os.chmod(os.path.join(out_dir, "Allrun"), 0o755)
    # 생성 요약(리포트에서 가정값 표기용)
    meta = {"config": cfg, "mesh": meshinfo, "roles": roles, "heat": hm}
    if patches:
        meta["patches"] = [{k: v for k, v in p.items() if k not in ("uax", "vax")}
                           for p in patches]
    _w(os.path.join(out_dir, "cfd_case_meta.json"),
       json.dumps(meta, ensure_ascii=False, indent=2))
    return meshinfo, roles


def _w(path, text):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


# ── Phase 3: 도면 geometry.json → cfg (치수 자동 추출) ───────────────────────
# 핵심 시너지: 수제 파이프라인이 손으로 베끼던 DXF 치수를 자동 추출한다.
# v1은 검증된 blockMesh '박스방'을 재사용(방=경계 bbox 근사). 비직사각 폴리곤 압출·
# 장비 장애물(snappyHexMesh)은 후속. 급기/배기는 도면에 없으므로 사람이 지정(정직).

def _xy_extent(records):
    """레코드 목록(wall centerline/points, zone points) → (xmin,ymin,xmax,ymax) mm."""
    xs, ys = [], []
    for r in records:
        pts = r.get("centerline") or r.get("points") or []
        for p in pts:
            xs.append(p[0]); ys.append(p[1])
    if not xs:
        return None
    return (min(xs), min(ys), max(xs), max(ys))


def diffusers_from_geometry(geom, zone=None, bbox=None):
    """도면 equipment 블록(footprint) → 천장 디퓨저/급배기구 후보 목록(방 좌표계 m).
    반환 [{cx, cy, w, h, name}]. 역할·CMH 는 도면에 없으므로 사용자가 지정(정직).
    D4: MEP 도면의 디퓨저 심볼은 블록(INSERT)→equipment 로 추출되어 있음."""
    el = geom.get("elements", {})
    if zone is not None:
        zones = el.get("zone", [])
        if zone >= len(zones):
            raise SystemExit(f"zone {zone} 없음")
        ext = _xy_extent([zones[zone]])
    elif bbox is not None:
        ext = tuple(bbox)
    else:
        ext = _xy_extent(el.get("wall", []))
    if not ext:
        ext = _xy_extent(el.get("equipment", []))   # MEP 전용 도면(벽 없음) 폴백
    if not ext:
        return []
    x0, y0, x1, y1 = ext
    out = []
    for eq in el.get("equipment", []):
        e = _xy_extent([eq])
        if not e:
            continue
        cx, cy = (e[0] + e[2]) / 2, (e[1] + e[3]) / 2
        if not (x0 <= cx <= x1 and y0 <= cy <= y1):
            continue
        out.append({"cx": round((cx - x0) / 1000.0, 3),
                    "cy": round((cy - y0) / 1000.0, 3),
                    "w": max(0.1, round((e[2] - e[0]) / 1000.0, 3)),
                    "h": max(0.1, round((e[3] - e[1]) / 1000.0, 3)),
                    "name": eq.get("layer") or "equipment"})
    return out


def _opening_wall(cx, cy, ext, tol):
    """개구부 중심이 bbox 어느 변에 붙었나 → x0|xL|y0|yW|None."""
    x0, y0, x1, y1 = ext
    d = {"x0": abs(cx - x0), "xL": abs(cx - x1), "y0": abs(cy - y0), "yW": abs(cy - y1)}
    w = min(d, key=d.get)
    return w if d[w] <= tol else None


def cfg_from_geometry(geom, zone=None, bbox=None, height=None, cell=0.3,
                      supply="x0", exhaust="xL", supply_u=0.05, supply_T=293.0,
                      floor_T=313.0, power_kw=None, init_T=300.0, endTime=400, name=None):
    """geometry.json(dict) + 선택(zone/bbox) → build_case 호환 cfg + 진단정보.
    반환: (cfg, info)  info={extent_mm, source, openings_by_wall, warnings}"""
    el = geom.get("elements", {})
    warnings = []
    src_poly = None
    if zone is not None:
        zones = el.get("zone", [])
        if zone >= len(zones):
            raise SystemExit(f"zone {zone} 없음 (zone 수={len(zones)}). --room-bbox 사용 권장.")
        src_poly = zones[zone].get("points")
        ext = _xy_extent([zones[zone]])
        source = f"zone[{zone}]"
    elif bbox is not None:
        ext = tuple(bbox)
        source = "room-bbox"
    else:
        ext = _xy_extent(el.get("wall", []))
        source = "전체 벽 extent(건물 전체 — 방 1개가 아님 주의)"
        warnings.append("zone/bbox 미지정 → 도면 전체 bbox 사용. 방 1개는 --zone N 또는 --room-bbox x0,y0,x1,y1 로 지정.")
    if not ext:
        raise SystemExit("치수 추출 실패: wall/zone 좌표가 없음.")
    x0, y0, x1, y1 = ext
    L = (x1 - x0) / 1000.0
    W = (y1 - y0) / 1000.0
    if height is not None:
        H = height
    else:
        H = geom.get("params", {}).get("wall", {}).get("height", 2800.0) / 1000.0
        warnings.append(f"층고 미지정 → params 벽높이 {H:.2f} m 사용(방 실제 층고 확인 권장).")
    # 경계 개구부 탐지(어느 벽에 있나) — 사용자가 급/배기 지정할 근거
    tol = max(L, W) * 1000 * 0.03 + 300  # bbox 대비 3% + 300mm
    openings_by_wall = {}
    for op in el.get("opening", []):
        c = op.get("center")
        if not c:
            continue
        if bbox is not None or zone is not None:
            if not (x0 - tol <= c[0] <= x1 + tol and y0 - tol <= c[1] <= y1 + tol):
                continue
        w = _opening_wall(c[0], c[1], ext, tol)
        if w:
            openings_by_wall.setdefault(w, 0)
            openings_by_wall[w] += 1

    if not name:
        import re as _re
        base = os.path.splitext(os.path.basename(geom.get("source", "drawing").replace("\\", "/")))[0]
        base = _re.sub(r"[^\w가-힣.-]", "_", base).strip("_") or "drawing"
        name = base + "_room"
    cfg = {
        "name": name,
        "_note": f"도면 자동추출({source}) · 원본 {geom.get('source','?')} · 치수=DXF, 급배기·발열=가정값(리포트 명시)",
        "room": {"L": round(L, 3), "W": round(W, 3), "H": round(H, 3)},
        "mesh": {"cell": cell},
        "g": [0, 0, -9.81],
        "inlet": {"wall": supply, "U": [supply_u, 0, 0], "T": supply_T,
                  "_desc": f"급기(가정) — {supply} 벽"},
        "outlet": {"wall": exhaust, "_desc": f"배기(가정) — {exhaust} 벽"},
        # 발열: power_kw 주면 체적 발열원(계산서 kW 직결·에너지폐합 검증), 아니면 바닥 고정온도
        "heat": ({"power_kw": power_kw, "_desc": f"장비 총발열(가정) {power_kw} kW = 바닥층 체적발열원"}
                 if power_kw is not None
                 else {"wall": "floor", "floor_T": floor_T, "_desc": "발열 바닥(가정) = 장비 총발열 단순화"}),
        "init": {"T": init_T},
        "endTime": endTime,
    }
    # inlet U 방향을 급기벽 법선 안쪽으로 정렬
    inflow = {"x0": [abs(supply_u), 0, 0], "xL": [-abs(supply_u), 0, 0],
              "y0": [0, abs(supply_u), 0], "yW": [0, -abs(supply_u), 0]}.get(supply)
    if inflow:
        cfg["inlet"]["U"] = inflow
    info = {"extent_mm": ext, "source": source, "openings_by_wall": openings_by_wall,
            "warnings": warnings, "equipment": len(el.get("equipment", [])),
            "src_polygon": src_poly}
    return cfg, info


def main():
    ap = argparse.ArgumentParser(description="config(또는 도면 geometry.json) → OpenFOAM 케이스 생성")
    ap.add_argument("config", nargs="?", help="cfd config.json (또는 --from-geometry 사용)")
    ap.add_argument("-o", "--out", default=None, help="출력 케이스 디렉토리")
    ap.add_argument("--from-geometry", metavar="G.JSON", help="도면 geometry.json 에서 치수 자동추출")
    ap.add_argument("--zone", type=int, help="방으로 쓸 zone 인덱스")
    ap.add_argument("--room-bbox", help="방 bbox x0,y0,x1,y1 (mm, 도면좌표)")
    ap.add_argument("--height", type=float, help="층고(m). 생략시 params 벽높이")
    ap.add_argument("--cell", type=float, default=0.3, help="격자 셀 크기(m)")
    ap.add_argument("--supply", default="x0", help="급기 벽 (x0|xL|y0|yW)")
    ap.add_argument("--exhaust", default="xL", help="배기 벽 (x0|xL|y0|yW)")
    ap.add_argument("--supply-u", type=float, default=0.05, help="급기 유속(m/s)")
    ap.add_argument("--floor-t", type=float, default=313.0, help="발열 바닥 온도(K, 구식 surface 모드)")
    ap.add_argument("--power-kw", type=float, help="장비 총발열(kW) — 체적 발열원(계산서 직결, 에너지폐합 검증). floor-t 대신 권장")
    ap.add_argument("--endtime", type=int, default=400, help="반복 수(정상상태)")
    ap.add_argument("--name", help="케이스 이름")
    args = ap.parse_args()

    if args.from_geometry:
        with open(args.from_geometry, encoding="utf-8") as f:
            geom = json.load(f)
        bbox = None
        if args.room_bbox:
            bbox = [float(x) for x in args.room_bbox.split(",")]
            if len(bbox) != 4:
                ap.error("--room-bbox 는 x0,y0,x1,y1 (4개)")
        cfg, info = cfg_from_geometry(
            geom, zone=args.zone, bbox=bbox, height=args.height, cell=args.cell,
            supply=args.supply, exhaust=args.exhaust, supply_u=args.supply_u,
            floor_T=args.floor_t, power_kw=args.power_kw, endTime=args.endtime, name=args.name)
        print(f"[도면추출] {info['source']}  방 {cfg['room']['L']}×{cfg['room']['W']}×{cfg['room']['H']} m")
        if args.power_kw is not None:
            print(f"  발열: {args.power_kw} kW 체적발열원(에너지폐합 검증 가능)")
        if info["openings_by_wall"]:
            print(f"  경계 개구부(급/배기 후보): {info['openings_by_wall']}  → --supply/--exhaust 로 지정")
        if info["equipment"]:
            print(f"  장비 {info['equipment']}개 감지(v1은 바닥발열로 단순화 — 장애물화는 후속 snappy)")
        for w in info["warnings"]:
            print(f"  ⚠ {w}")
    elif args.config:
        with open(args.config, encoding="utf-8") as f:
            cfg = json.load(f)
        info = None
    else:
        ap.error("config 또는 --from-geometry 필요")

    out = args.out or ("case_" + cfg.get("name", "cfd"))
    meshinfo, roles = build_case(cfg, out)
    # 도면추출 정보를 meta 에 병합(리포트 표기용)
    if info:
        meta_path = os.path.join(out, "cfd_case_meta.json")
        meta = json.load(open(meta_path, encoding="utf-8"))
        meta["from_geometry"] = {k: v for k, v in info.items() if k != "src_polygon"}
        _w(meta_path, json.dumps(meta, ensure_ascii=False, indent=2))
    print(f"케이스 생성 -> {out}")
    print(f"  메시: {meshinfo['nx']}x{meshinfo['ny']}x{meshinfo['nz']} = {meshinfo['cells']} cells")
    print(f"  패치 역할: {roles}")
    print(f"  다음: python cfd_run.py {out}  →  python cfd_report.py {out}")


if __name__ == "__main__":
    main()
