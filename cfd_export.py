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
from collections.abc import Mapping
import json
import math
import os
import re
import shutil
import sys
import tempfile
import time
import uuid

from heat_source_contract import (
    HeatSourceContractError,
    assert_unique_positive_source_ids,
    normalize_confirmed_heat_source,
    source_reference_kind,
)

try:  # Windows cp949 consoles cannot encode symbols used in friendly CLI messages.
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")
except (AttributeError, OSError):
    pass

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


# ---------------------------------------------------------------------------
# 정상상태 에너지수지 — 초기장·수렴판정·리포트 교차검증의 공통 기준값
# ---------------------------------------------------------------------------
# 배경(2026-08 실측 사고): 초기 T 를 300 K 로 고정 배포했더니, 환기율이 낮은 방
# (1.5 ACH → 공기교체 시간상수 ~40분)에서 1000 iteration 으로는 초기장이 빠져나가지
# 못했다. 배기온도가 초기값 300 K 에 머문 채 급기 289.15 K 와의 차 10.9 K 가
# "배기 엔탈피"로 잡혀 에너지폐합 158% (= 나가는 열이 넣은 열의 1.6배, 물리적 불가)
# 가 리포트에 실렸다. 해석적 평형온도로 초기화하면 이 잉여 에너지 자체가 없어져
# 폐합이 처음부터 100% 근처에서 시작하고, 수렴도 극적으로 빨라진다.

def supply_flow_m3s(cfg, patches=None):
    """설계 급기유량 [m³/s]. 개구부 모드는 패치 CMH 합, 벽 급기 모드는 |U|×벽면적."""
    if patches:
        cmh = sum((p.get("cmh") or 0.0) for p in patches if p.get("role") == "supply")
        if cmh > 0:
            return cmh / 3600.0
    inlet = cfg.get("inlet") or {}
    wall = inlet.get("wall")
    U = inlet.get("U")
    if not wall or not U:
        return None
    room = cfg.get("room") or {}
    try:
        L, W, H = float(room["L"]), float(room["W"]), float(room["H"])
    except (KeyError, TypeError, ValueError):
        return None
    area = {"floor": L * W, "ceiling": L * W,
            "x0": W * H, "xL": W * H,
            "y0": L * H, "yW": L * H}.get(wall)
    if not area:
        return None
    speed = sum(float(c) ** 2 for c in U) ** 0.5
    return area * speed if speed > 0 else None


def equilibrium_temperature(cfg, patches=None):
    """단열벽·정상상태 실내(배기) 평형온도 [K]와 산출근거.

    에너지수지  Q = ρ·cp·V̇·(T_eq − T_sup)  →  T_eq = T_sup + Q/(ρ·cp·V̇)

    이 한 값이 세 곳의 기준이 된다:
      (1) 0/T 초기장 — 여기서 시작하면 "초기장 배출" 과도기가 사라진다
      (2) 배기 inletOutlet 의 inletValue — 역류 시 주입될 공기온도
      (3) 리포트 교차검증 — CFD 배기온도가 이 값과 크게 다르면 미수렴/오설정

    반환: (T_eq 또는 None, info dict). 발열·급기 정보가 없으면 T_eq=None.
    """
    info = {}
    hm = _heat_mode(cfg)
    power_w = float(hm.get("power_w") or 0.0) if hm.get("mode") == "volume" else 0.0
    equipment_heat = _equipment_heat_summary(cfg) if not power_w else None
    if equipment_heat:
        power_w = equipment_heat["applied_convective_power_w"]
    vdot = supply_flow_m3s(cfg, patches)

    T_sup = None
    if patches:
        sups = [p for p in patches if p.get("role") == "supply"]
        if sups and sups[0].get("T") is not None:
            T_sup = float(sups[0]["T"])
    if T_sup is None:
        T_sup = (cfg.get("inlet") or {}).get("T")
    info.update({"power_w": power_w, "vdot_m3s": vdot, "T_supply_K": T_sup})
    if equipment_heat and equipment_heat["source_count"]:
        info.update({
            "heat_source": "obstacles",
            "input_power_w": equipment_heat["input_power_w"],
            "excluded_radiative_power_w": equipment_heat[
                "excluded_radiative_power_w"
            ],
        })

    if not power_w or not vdot or T_sup is None:
        info["reason"] = "발열 또는 급기 정보 부족 — 해석적 평형온도 산출 불가"
        return None, info

    mcp = RHO_CP * vdot                      # W/K
    dT = power_w / mcp
    T_eq = float(T_sup) + dT
    room = cfg.get("room") or {}
    try:
        vol = float(room["L"]) * float(room["W"]) * float(room["H"])
        info["ach"] = vdot * 3600.0 / vol if vol > 0 else None
        # 공기 1회 교체 시간 = 초기장이 밀려나가는 시간상수
        info["flush_time_s"] = vol / vdot if vdot > 0 else None
    except (KeyError, TypeError, ValueError):
        pass
    info.update({"mcp_w_per_k": mcp, "delta_T_K": dT, "T_eq_K": T_eq})
    return T_eq, info


def resolve_init_T(cfg, patches=None):
    """0/T 초기장 온도 [K] 결정. 해석적 평형온도 우선, 없으면 cfg init.T 폴백.

    반환: (T_init, note) — note 는 meta 에 남겨 리포트가 근거를 표시한다."""
    configured = (cfg.get("init") or {}).get("T")
    T_eq, info = equilibrium_temperature(cfg, patches)
    if T_eq is None:
        value = float(configured) if configured is not None else 300.0
        return value, {"source": "config" if configured is not None else "default",
                       "T_init_K": value, **info}
    note = {"source": "equilibrium", "T_init_K": T_eq, **info}
    if configured is not None and abs(float(configured) - T_eq) > 0.5:
        # 사용자가 지정한 값이 물리 평형과 어긋나면 평형값을 쓰되 근거를 남긴다.
        note["configured_T_K"] = float(configured)
        note["overridden"] = True
    return T_eq, note


# 급배기구 한 변이 최소 이 개수의 셀로 표현돼야 제트가 형상을 유지한다.
# 1셀 미만이면 토출면이 격자에 뭉개져 제트 도달거리·확산·최대유속이 모두 부정확해진다.
MIN_CELLS_PER_DIFFUSER_SIDE = 2.0
QUADRANT_BALANCE_TOLERANCE = 0.15


def _opening_group_key(patch):
    """Return a stable parent key, preferring the source/DXF identity."""
    name = str((patch or {}).get("name") or "")
    return str((patch or {}).get("opening_id") or
               (patch or {}).get("parent_name") or
               name.split("_q", 1)[0] or name)


def _opening_groups(patches):
    groups = {}
    for patch in patches or []:
        groups.setdefault(_opening_group_key(patch), []).append(patch)
    return groups


def _rect_extent(rectangles):
    """Return the bounding rectangle of usable 2D opening rectangles."""
    usable = []
    for rect in rectangles:
        if not isinstance(rect, (list, tuple)) or len(rect) != 4:
            continue
        try:
            values = [float(value) for value in rect]
        except (TypeError, ValueError):
            continue
        if values[2] > values[0] and values[3] > values[1]:
            usable.append(values)
    if not usable:
        return None
    return [min(item[0] for item in usable), min(item[1] for item in usable),
            max(item[2] for item in usable), max(item[3] for item in usable)]


def _rect_dimensions(rect):
    if not isinstance(rect, (list, tuple)) or len(rect) != 4:
        return None
    try:
        width = float(rect[2]) - float(rect[0])
        height = float(rect[3]) - float(rect[1])
    except (TypeError, ValueError):
        return None
    return (width, height) if width > 0 and height > 0 else None


def _legacy_diffuser_resolution(cfg, patches, min_cells=MIN_CELLS_PER_DIFFUSER_SIDE):
    """급배기구가 격자 대비 충분히 해상되는지 점검.

    실측 사고: 셀 0.15 m 인데 디퓨저가 0.04 m²(0.2×0.2 m ≈ 1.3셀), 일부는 0.02 m² 로
    한 셀에도 못 미쳤다. 토출속도 3 m/s 가 1~2셀에 뭉개지면서 제트가 실제보다 빨리
    흩어지고 최대유속(5.4 m/s)도 이 언더리졸브의 산물이었다.

    반환: {cell_m, min_cells, worst: {...}, under: [...], recommended_cell_m} 또는 None.
    """
    if not patches:
        return None
    cell = (cfg.get("mesh") or {}).get("cell")
    if not cell:
        return None
    cell = float(cell)
    items = []
    for p in patches:
        if p.get("role") not in ("supply", "exhaust"):
            continue
        area = p.get("area")
        if not area or area <= 0:
            continue
        side = float(area) ** 0.5              # 등가 정사각 한 변 [m]
        items.append({"name": p.get("name"), "role": p.get("role"),
                      "area_m2": float(area), "side_m": side,
                      "cells_per_side": side / cell})
    if not items:
        return None
    items.sort(key=lambda d: d["cells_per_side"])
    under = [d for d in items if d["cells_per_side"] < min_cells]
    worst = items[0]
    return {"cell_m": cell, "min_cells": min_cells, "n_total": len(items),
            "worst": worst, "under": under,
            # 가장 작은 개구부까지 min_cells 로 담으려면 필요한 셀 크기
            "recommended_cell_m": worst["side_m"] / min_cells}

# A 4-way diffuser has four CFD child patches but one physical terminal.  The
# parent-level resolution below is deliberately separate from the historical
# leaf-patch checker above: area/flow summaries must use the parent, while a
# one-cell quadrant must still make jet and maximum-velocity metrics unsafe.
def diffuser_resolution(cfg, patches, min_cells=MIN_CELLS_PER_DIFFUSER_SIDE):
    """Assess parent terminal and directional 4-way resolution separately."""
    if not patches:
        return None
    cell = (cfg.get("mesh") or {}).get("cell")
    if not cell:
        return None
    try:
        cell = float(cell)
    except (TypeError, ValueError):
        return None
    if cell <= 0:
        return None
    # Historical saved cases stored area only.  Keep their previous
    # leaf-level diagnostic rather than pretending a parent rectangle exists.
    if not any(_rect_dimensions(item.get("rect_snap")) for item in patches):
        return _legacy_diffuser_resolution(cfg, patches, min_cells)

    groups = _opening_groups(patches)
    if any(_rect_extent([item.get("rect_snap") for item in children]) is None
           for children in groups.values()):
        return _legacy_diffuser_resolution(cfg, patches, min_cells)
    items = []
    for opening_id, children in groups.items():
        first = children[0]
        if first.get("role") not in ("supply", "exhaust"):
            continue
        rect = _rect_extent([item.get("rect_snap") for item in children])
        dimensions = _rect_dimensions(rect)
        area = sum(float(item.get("area") or 0.0) for item in children)
        if dimensions is None or area <= 0:
            continue
        width, height = dimensions
        parent_cells = min(width, height) / cell
        child_cells = []
        child_sides = []
        for child in children:
            child_dimensions = _rect_dimensions(child.get("rect_snap"))
            if child_dimensions is not None:
                child_side = min(child_dimensions)
                child_sides.append(child_side)
                child_cells.append(child_side / cell)
        is_4way = first.get("type") == "4way" and len(children) == 4
        quadrant_areas = [float(item.get("area") or 0.0) for item in children]
        mean_area = sum(quadrant_areas) / len(quadrant_areas) if quadrant_areas else 0.0
        quadrant_balance_ok = (
            not is_4way or (mean_area > 0 and
                            max(abs(value / mean_area - 1.0)
                                for value in quadrant_areas) <= QUADRANT_BALANCE_TOLERANCE)
        )
        directional_resolution_ok = (
            not is_4way or (bool(child_cells) and min(child_cells) >= min_cells)
        )
        parent_resolution_ok = parent_cells >= min_cells
        parent_recommended_cell = min(width, height) / min_cells
        directional_recommended_cell = (min(child_sides) / min_cells
                                        if is_4way and child_sides else parent_recommended_cell)
        items.append({
            "opening_id": opening_id,
            "name": first.get("parent_name") or first.get("name"),
            "role": first.get("role"),
            "area_m2": area,
            "width_m": width,
            "height_m": height,
            "side_m": min(width, height),
            "cells_per_side": parent_cells,
            "parent_resolution_ok": parent_resolution_ok,
            "quadrant_resolution_ok": directional_resolution_ok,
            "quadrant_balance_ok": quadrant_balance_ok,
            "child_min_cells_per_side": min(child_cells) if child_cells else None,
            "recommended_cell_m": (directional_recommended_cell
                                   if not directional_resolution_ok or not quadrant_balance_ok
                                   else parent_recommended_cell),
            "jet_metrics_citable": bool(parent_resolution_ok and
                                          directional_resolution_ok and
                                          quadrant_balance_ok),
        })
    if not items:
        return None
    items.sort(key=lambda item: item["cells_per_side"])
    under = [item for item in items if not item["jet_metrics_citable"]]
    worst = under[0] if under else items[0]
    return {
        "cell_m": cell,
        "min_cells": min_cells,
        "n_total": len(items),
        "worst": worst,
        "under": under,
        "recommended_cell_m": worst["recommended_cell_m"],
        "opening_resolution_ok": not under,
        "jet_metrics_citable": not under,
        "terminals": items,
    }


# A local workstation can become unresponsive long before OpenFOAM starts when a
# mistyped mesh size creates tens of millions of cells.  Keep the default guard
# deliberately conservative; advanced users can still use the CLI to split a
# model into smaller rooms or choose an appropriate coarser screening mesh.
MAX_DESKTOP_CELLS = 2_000_000


def _finite_number(value, label, *, positive=False, nonnegative=False):
    """Return ``value`` as float or stop with a field-specific Korean message."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise SystemExit(f"{label}: 숫자를 입력하세요")
    if not math.isfinite(number):
        raise SystemExit(f"{label}: 유한한 숫자만 사용할 수 있습니다")
    if positive and number <= 0:
        raise SystemExit(f"{label}: 0보다 커야 합니다")
    if nonnegative and number < 0:
        raise SystemExit(f"{label}: 0 이상이어야 합니다")
    return number


def validate_config(cfg, max_cells=MAX_DESKTOP_CELLS):
    """Validate user-facing CFD inputs before touching an existing case folder.

    The GUI is aimed at non-specialists, so malformed values must fail here with
    an actionable message rather than later as a ZeroDivisionError or an opaque
    OpenFOAM dictionary error.  Returns the resolved mesh counts for tests and
    preflight callers.
    """
    if not isinstance(cfg, dict):
        raise SystemExit("해석 설정 형식이 올바르지 않습니다")
    room = cfg.get("room")
    if not isinstance(room, dict):
        raise SystemExit("방 치수(room) 설정이 없습니다")
    L = _finite_number(room.get("L"), "방 길이 L(m)", positive=True)
    W = _finite_number(room.get("W"), "방 너비 W(m)", positive=True)
    H = _finite_number(room.get("H"), "방 높이 H(m)", positive=True)
    cell = _finite_number(cfg.get("mesh", {}).get("cell", 0.3),
                          "격자 셀 크기(m)", positive=True)
    nx, ny, nz = max(1, round(L / cell)), max(1, round(W / cell)), max(1, round(H / cell))
    cells = nx * ny * nz
    if cells > max_cells:
        suggested = (L * W * H / max_cells) ** (1.0 / 3.0)
        raise SystemExit(
            f"격자가 {cells:,}셀입니다. 이 PC용 안전 한도({max_cells:,}셀)를 넘어 "
            f"실행하지 않았습니다. 셀 크기를 약 {suggested:.3f}m 이상으로 키세요."
        )

    end_time = cfg.get("endTime", 400)
    end_num = _finite_number(end_time, "최대 반복 횟수", positive=True)
    if int(end_num) != end_num:
        raise SystemExit("최대 반복 횟수: 양의 정수를 입력하세요")

    heat = cfg.get("heat") or {}
    if heat.get("power_kw") is not None:
        _finite_number(heat["power_kw"], "총발열(kW)", nonnegative=True)
    if heat.get("floor_T") is not None:
        _finite_number(heat["floor_T"], "바닥 온도(K)", positive=True)

    valid_walls = set(_WALL_PLANES)
    if not cfg.get("openings"):
        inlet = cfg.get("inlet") or {}
        outlet = cfg.get("outlet") or {}
        iw, ow = inlet.get("wall"), outlet.get("wall")
        if iw not in valid_walls:
            raise SystemExit(f"급기 벽: {sorted(valid_walls)} 중 하나를 선택하세요")
        if ow not in valid_walls:
            raise SystemExit(f"배기 벽: {sorted(valid_walls)} 중 하나를 선택하세요")
        if iw == ow:
            raise SystemExit("급기 벽과 배기 벽은 다르게 선택하세요")

    openings = cfg.get("openings") or []
    for i, op in enumerate(openings):
        label = f"급배기구 {i + 1}"
        if op.get("role") not in ("supply", "exhaust"):
            raise SystemExit(f"{label}: 역할은 supply 또는 exhaust여야 합니다")
        if op.get("wall") not in valid_walls:
            raise SystemExit(f"{label}: 벽 위치가 올바르지 않습니다")
        if op.get("type", "grille") not in ("4way", "round", "down", "grille"):
            raise SystemExit(f"{label}: 타입은 4way/round/down/grille 중 하나여야 합니다")
        for key, desc in (("cx", "중심 cx"), ("cy", "중심 cy"),
                          ("w", "너비 w"), ("h", "높이 h")):
            _finite_number(op.get(key), f"{label} {desc}(m)", positive=key in ("w", "h"))
        if op.get("role") == "supply":
            _finite_number(op.get("cmh"), f"{label} 풍량(CMH)", positive=True)
        elif op.get("cmh") not in (None, ""):
            # A pressure outlet cannot enforce this value; it remains a
            # design target to compare with the solved boundary ``phi``.
            _finite_number(op.get("cmh"), f"{label} 설계 배기풍량(CMH)", positive=True)

    for i, obstacle in enumerate(cfg.get("obstacles") or []):
        label = f"장애물 {i + 1}"
        if obstacle.get("bbox"):
            if len(obstacle["bbox"]) != 4:
                raise SystemExit(f"{label}: bbox는 x0,y0,x1,y1 4개 숫자여야 합니다")
            x0, y0, x1, y1 = [_finite_number(v, f"{label} bbox") for v in obstacle["bbox"]]
            if x1 <= x0 or y1 <= y0:
                raise SystemExit(f"{label}: x1>x0, y1>y0 이되도록 범위를 확인하세요")
        elif obstacle.get("footprint"):
            if len(obstacle["footprint"]) < 3:
                raise SystemExit(f"{label}: footprint는 점이 3개 이상 필요합니다")
            for p in obstacle["footprint"]:
                if not isinstance(p, (list, tuple)) or len(p) < 2:
                    raise SystemExit(f"{label}: footprint 좌표 형식이 올바르지 않습니다")
                _finite_number(p[0], f"{label} x")
                _finite_number(p[1], f"{label} y")
        else:
            raise SystemExit(f"{label}: bbox 또는 footprint가 필요합니다")
        if obstacle.get("h") not in (None, ""):
            _finite_number(obstacle["h"], f"{label} 높이(m)", positive=True)
        if obstacle.get("kw") not in (None, ""):
            _finite_number(obstacle["kw"], f"{label} 발열(kW)", nonnegative=True)

    polygon = cfg.get("room_polygon")
    if polygon:
        if len(polygon) < 3:
            raise SystemExit("방 실형상 폴리곤은 점이 3개 이상 필요합니다")
        pts = []
        for p in polygon:
            if not isinstance(p, (list, tuple)) or len(p) < 2:
                raise SystemExit("방 폴리곤 좌표 형식이 올바르지 않습니다")
            pts.append((_finite_number(p[0], "방 폴리곤 x"),
                        _finite_number(p[1], "방 폴리곤 y")))
        area2 = abs(sum(x0 * y1 - x1 * y0
                        for (x0, y0), (x1, y1) in zip(pts, pts[1:] + pts[:1])))
        if area2 <= 1e-9:
            raise SystemExit("방 실형상 폴리곤의 면적이 0입니다")

    return {"nx": nx, "ny": ny, "nz": nz, "cells": cells}


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
    seen_opening_ids = set()
    for op_index, op in enumerate(cfg.get("openings", []), 1):
        wall = op.get("wall")
        if wall not in _WALL_PLANES:
            raise SystemExit(f"openings: wall '{wall}' 는 {list(_WALL_PLANES)} 중 하나")
        nax, _, (uax, vax) = _WALL_PLANES[wall]
        hu = dims[uax] / ncell[uax]
        hv = dims[vax] / ncell[vax]
        cx, cy = float(op["cx"]), float(op["cy"])
        w, h = float(op["w"]), float(op["h"])
        if w <= 0 or h <= 0:
            raise SystemExit(f"openings: '{wall}' 개구부 크기 w/h는 0보다 커야 합니다")
        req_u0, req_u1 = cx - w / 2, cx + w / 2
        req_v0, req_v1 = cy - h / 2, cy + h / 2
        # Do not silently clamp a mistyped opening to the nearest edge cell.
        # That changes both its location and the airflow path without the user
        # noticing, which is far more dangerous than an early actionable error.
        eps = 1e-9
        if req_u0 < -eps or req_u1 > dims[uax] + eps or req_v0 < -eps or req_v1 > dims[vax] + eps:
            fixes = []
            if req_u0 < -eps or req_u1 > dims[uax] + eps:
                if w <= dims[uax]:
                    fixed_cx = min(max(cx, w / 2), dims[uax] - w / 2)
                    fixes.append(f"cx를 {fixed_cx:.3f}")
                if 0 <= cx <= dims[uax]:
                    fixes.append(f"w를 {2 * min(cx, dims[uax] - cx):.3f} 이하")
            if req_v0 < -eps or req_v1 > dims[vax] + eps:
                if h <= dims[vax]:
                    fixed_cy = min(max(cy, h / 2), dims[vax] - h / 2)
                    fixes.append(f"cy를 {fixed_cy:.3f}")
                if 0 <= cy <= dims[vax]:
                    fixes.append(f"h를 {2 * min(cy, dims[vax] - cy):.3f} 이하")
            fix_text = (" 해결: " + " 또는 ".join(fixes) + "로 바꾸세요."
                        if fixes else " 중심·크기를 확인하세요.")
            raise SystemExit(
                f"openings: {op_index}번 '{wall}' 개구부 범위({req_u0:.3f}~{req_u1:.3f}, "
                f"{req_v0:.3f}~{req_v1:.3f}m)가 벽 크기(0~{dims[uax]:.3f}, "
                f"0~{dims[vax]:.3f}m) 밖입니다.{fix_text}"
            )
        u0, u1 = _snap_1d(cx - w / 2, cx + w / 2, hu, ncell[uax])
        v0, v1 = _snap_1d(cy - h / 2, cy + h / 2, hv, ncell[vax])
        if not (0 <= u0 < u1 <= dims[uax] and 0 <= v0 < v1 <= dims[vax]):
            raise SystemExit(f"openings: '{wall}' ({cx},{cy}) {w}x{h} 가 벽 범위 밖")
        area = (u1 - u0) * (v1 - v0)
        role = op.get("role", "supply")
        typ = op.get("type", "grille")
        n = _WALL_NORMAL_IN[wall]
        parent_name = f"exh{n_exh}" if role == "exhaust" else f"sup{n_sup}"
        raw_opening_id = op.get("opening_id") or op.get("source_id") or f"opening_{op_index}"
        opening_id = str(raw_opening_id).strip()
        if not opening_id:
            raise SystemExit(f"openings: {op_index}번 opening_id가 비어 있습니다.")
        if opening_id in seen_opening_ids:
            raise SystemExit(f"openings: 중복 opening_id '{opening_id}'가 있습니다.")
        seen_opening_ids.add(opening_id)
        source_id = op.get("source_id")
        source_label = op.get("source_label") or op.get("label")
        source_type = op.get("source_type")
        source_ref = op.get("source_ref")
        base = {"role": role, "type": typ, "wall": wall,
                "opening_id": opening_id,
                "parent_name": parent_name,
                "source_id": str(source_id) if source_id not in (None, "") else None,
                "source_label": str(source_label) if source_label not in (None, "") else None,
                "source_type": str(source_type) if source_type not in (None, "") else None,
                "source_ref": dict(source_ref) if isinstance(source_ref, dict) else None,
                "override_of_dxf": op.get("override_of_dxf") is True,
                "requested_area_m2": round(w * h, 6),
                "rect_req": [cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2],
                "rect_snap": [round(u0, 4), round(v0, 4), round(u1, 4), round(v1, 4)],
                "uax": uax, "vax": vax}
        if role == "exhaust":
            target_cmh = op.get("cmh")
            try:
                target_cmh = float(target_cmh) if target_cmh not in (None, "") else None
            except (TypeError, ValueError):
                raise SystemExit(f"openings: exhaust '{wall}'의 설계 CMH를 확인하세요.")
            patches.append({**base, "name": parent_name, "area": round(area, 4),
                            "U": None, "T": None, "cmh": None, "cmh_req": None,
                            "design_cmh": target_cmh,
                            "flow_control": "pressure_outlet"})
            n_exh += 1
            continue
        cmh = op.get("cmh")
        if cmh is None:
            raise SystemExit(f"openings: supply '{wall}' 에 cmh 필수(계산서 풍량)")
        Q = float(cmh) / 3600.0
        base["parent_cmh_req"] = float(cmh)
        base["design_cmh"] = float(cmh)
        base["flow_control"] = "fixed_normal_velocity"
        Tsup = float(op.get("T", Tsup_default))
        k_in = float(op.get("k", 0.01))          # 급기 난류(벤치마크 재현용 오버라이드)
        eps_in = float(op.get("epsilon", 0.01))
        if typ == "4way":
            nu = int(round((u1 - u0) / hu))
            nv = int(round((v1 - v0) / hv))
            if nu < 2 or nv < 2:
                raise SystemExit(
                    f"openings: 4방향 '{wall}'이 격자에서 {nu}×{nv}셀만 차지합니다. "
                    "4방향을 나누려면 개구부를 키우거나 격자 셀을 더 작게 하세요."
                )
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
                patches.append({**base, "name": f"{parent_name}_q{qi}",
                                "rect_snap": [round(a0, 4), round(b0, 4), round(a1, 4), round(b1, 4)],
                                "area": round(qarea, 4),
                                "U": [round(vec["x"], 4), round(vec["y"], 4), round(vec["z"], 4)],
                                "T": Tsup, "cmh": round(u_n * qarea * 3600, 1),
                                "cmh_req": float(cmh) / 4, "k": k_in, "epsilon": eps_in})
        else:
            # round/down/grille: 현재 격자에서는 설치면 법선 방향 단일 패치 근사.
            # 방사형 원형 디퓨저가 필요하면 사용자가 4way와 더 작은 격자를 선택한다.
            u_mag = Q / area                                # 스냅 실면적으로 역산 → CMH 정확
            vec = [round(n[0] * u_mag, 4), round(n[1] * u_mag, 4), round(n[2] * u_mag, 4)]
            patches.append({**base, "name": parent_name, "area": round(area, 4),
                            "U": vec, "T": Tsup,
                            "cmh": round(u_mag * area * 3600, 1), "cmh_req": float(cmh),
                            "k": k_in, "epsilon": eps_in})
        n_sup += 1
    # createPatch cannot assign one boundary face to two patches.  Detect this
    # while the user can still correct the coordinates instead of surfacing an
    # opaque topoSet/createPatch failure later.
    for i, a in enumerate(patches):
        for b in patches[i + 1:]:
            if a["wall"] != b["wall"]:
                continue
            a0, b0, a1, b1 = a["rect_snap"]
            c0, d0, c1, d1 = b["rect_snap"]
            if min(a1, c1) - max(a0, c0) > 1e-9 and min(b1, d1) - max(b0, d0) > 1e-9:
                raise SystemExit(
                    f"openings: '{a['name']}'과 '{b['name']}'이 {a['wall']} 벽에서 겹칩됩니다. "
                    "중심·크기를 조정하세요."
                )
    if patches and not any(p["role"] == "exhaust" for p in patches):
        raise SystemExit("openings: exhaust(배기) 가 최소 1개 필요(압력출구)")
    return patches


def _legacy_opening_preflight(cfg, patches, area_tolerance=0.15, flow_tolerance=0.01):
    """Aggregate snapped patch evidence by the user-facing terminal.

    A 4-way diffuser is represented by four OpenFOAM patches, but it is one
    physical terminal. Area and flow checks must therefore use the summed
    child patches or the UI would report a false 75% area loss for every
    quadrant.
    """
    groups = {}
    for patch in patches or []:
        name = str(patch.get("name") or "")
        parent = str(patch.get("parent_name") or name.split("_q", 1)[0] or name)
        opening_id = str(patch.get("opening_id") or parent)
        group = groups.setdefault(opening_id, {
            "opening_id": opening_id,
            "parent_name": parent,
            "role": patch.get("role"),
            "type": patch.get("type"),
            "wall": patch.get("wall"),
            "patches": [],
        })
        group["patches"].append(patch)

    terminals = []
    warnings = []
    for group in groups.values():
        children = group["patches"]
        first = children[0]
        requested_area = first.get("requested_area_m2")
        if requested_area is None:
            rect = first.get("rect_req") or []
            if len(rect) == 4:
                requested_area = (float(rect[2]) - float(rect[0])) * (
                    float(rect[3]) - float(rect[1])
                )
        try:
            requested_area = float(requested_area)
        except (TypeError, ValueError):
            requested_area = None
        snapped_area = sum(float(item.get("area") or 0.0) for item in children)

        requested_cmh = next(
            (item.get("parent_cmh_req") for item in children
             if item.get("parent_cmh_req") is not None),
            None,
        )
        if requested_cmh is None:
            requested_values = [
                item.get("cmh_req") for item in children
                if item.get("cmh_req") is not None
            ]
            requested_cmh = sum(float(value) for value in requested_values) if requested_values else None
        try:
            requested_cmh = float(requested_cmh) if requested_cmh is not None else None
        except (TypeError, ValueError):
            requested_cmh = None
        actual_values = [
            item.get("cmh") for item in children if item.get("cmh") is not None
        ]
        actual_cmh = sum(float(value) for value in actual_values) if actual_values else None

        area_ratio = (snapped_area / requested_area
                      if requested_area is not None and requested_area > 0 else None)
        flow_ratio = (actual_cmh / requested_cmh
                      if requested_cmh is not None and requested_cmh > 0
                      and actual_cmh is not None else None)
        area_ok = area_ratio is not None and abs(area_ratio - 1.0) <= area_tolerance
        flow_ok = (flow_ratio is None or abs(flow_ratio - 1.0) <= flow_tolerance)
        row = {
            "opening_id": group["opening_id"],
            "parent_name": group["parent_name"],
            "role": group["role"],
            "type": group["type"],
            "wall": group["wall"],
            "child_patch_count": len(children),
            "child_patch_names": [str(item.get("name") or "") for item in children],
            "requested_area_m2": round(requested_area, 6) if requested_area is not None else None,
            "snapped_area_m2": round(snapped_area, 6),
            "area_ratio": round(area_ratio, 6) if area_ratio is not None else None,
            "area_within_tolerance": area_ok,
            "requested_cmh": round(requested_cmh, 3) if requested_cmh is not None else None,
            "actual_cmh": round(actual_cmh, 3) if actual_cmh is not None else None,
            "flow_ratio": round(flow_ratio, 6) if flow_ratio is not None else None,
            "flow_within_tolerance": flow_ok,
            "requested_face_velocity_m_s": (
                round(requested_cmh / 3600.0 / requested_area, 6)
                if requested_cmh is not None and requested_area not in (None, 0) else None
            ),
            "snapped_face_velocity_m_s": (
                round(actual_cmh / 3600.0 / snapped_area, 6)
                if actual_cmh is not None and snapped_area > 0 else None
            ),
            "status": "PASS" if area_ok and flow_ok else "WARN",
        }
        terminals.append(row)
        if row["status"] != "PASS":
            warnings.append(row["parent_name"])
    return {
        "contract": "opening_preflight.v2",
        "area_tolerance": area_tolerance,
        "flow_tolerance": flow_tolerance,
        "terminal_count": len(terminals),
        "terminals": terminals,
        "warnings": warnings,
    }


def _ratio_to_mean(values):
    values = [float(value) for value in values if value is not None]
    if not values:
        return None, None, False
    mean = sum(values) / len(values)
    if mean <= 0:
        return None, None, False
    ratios = [value / mean for value in values]
    return min(ratios), max(ratios), True


def opening_preflight(cfg, patches, area_tolerance=0.15,
                      flow_tolerance=0.01):
    """Summarise each physical terminal before the solver starts.

    ``applied_normal_cmh`` is the flow encoded by the generated supply
    boundary condition.  It is intentionally distinct from ``solved_cmh``:
    the latter is available only from a post-run ``phi`` artifact.  A pressure
    outlet preserves its design CMH as a target/reference, never as an applied
    OpenFOAM boundary flow.
    """
    resolution = diffuser_resolution(cfg, patches, MIN_CELLS_PER_DIFFUSER_SIDE)
    resolution_by_id = {
        str(item.get("opening_id")): item
        for item in ((resolution or {}).get("terminals") or [])
    }
    terminals = []
    warnings = []
    result_required = []
    for opening_id, children in _opening_groups(patches).items():
        first = children[0]
        role = first.get("role")
        parent_name = first.get("parent_name") or first.get("name")
        requested_area = first.get("requested_area_m2")
        if requested_area is None:
            dimensions = _rect_dimensions(first.get("rect_req"))
            requested_area = dimensions[0] * dimensions[1] if dimensions else None
        try:
            requested_area = float(requested_area)
        except (TypeError, ValueError):
            requested_area = None
        snapped_area = sum(float(item.get("area") or 0.0) for item in children)
        snapped_rect = _rect_extent([item.get("rect_snap") for item in children])
        requested_rect = _rect_extent([item.get("rect_req") for item in children])
        area_ratio = (snapped_area / requested_area
                      if requested_area is not None and requested_area > 0 else None)
        area_ok = area_ratio is not None and abs(area_ratio - 1.0) <= area_tolerance

        design_cmh = first.get("design_cmh")
        if design_cmh is None:
            design_cmh = first.get("parent_cmh_req")
        if design_cmh is None and role == "supply":
            values = [item.get("cmh_req") for item in children
                      if item.get("cmh_req") is not None]
            design_cmh = sum(float(value) for value in values) if values else None
        try:
            design_cmh = float(design_cmh) if design_cmh is not None else None
        except (TypeError, ValueError):
            design_cmh = None

        flow_control = (first.get("flow_control") or
                        ("pressure_outlet" if role == "exhaust"
                         else "fixed_normal_velocity"))
        applied_normal_cmh = None
        if role == "supply":
            values = [item.get("cmh") for item in children
                      if item.get("cmh") is not None]
            applied_normal_cmh = sum(float(value) for value in values) if values else None
        flow_ratio = (applied_normal_cmh / design_cmh
                      if applied_normal_cmh is not None and design_cmh not in (None, 0)
                      else None)
        if flow_control == "pressure_outlet":
            flow_status = "RESULT_REQUIRED" if design_cmh is not None else "TARGET_MISSING"
            flow_ok = None
        elif flow_ratio is None:
            flow_status = "NOT_APPLIED"
            flow_ok = False
        else:
            flow_ok = abs(flow_ratio - 1.0) <= flow_tolerance
            flow_status = "APPLIED" if flow_ok else "WARN"

        is_4way = first.get("type") == "4way" and len(children) == 4
        quadrant_areas = [float(item.get("area") or 0.0) for item in children]
        quadrant_flows = [float(item.get("cmh") or 0.0) for item in children]
        area_ratio_min, area_ratio_max, area_ratio_valid = _ratio_to_mean(quadrant_areas)
        flow_ratio_min, flow_ratio_max, flow_ratio_valid = _ratio_to_mean(quadrant_flows)
        quadrant_balance_ok = (
            not is_4way or (area_ratio_valid and flow_ratio_valid and
                            area_ratio_min >= 1.0 - QUADRANT_BALANCE_TOLERANCE and
                            area_ratio_max <= 1.0 + QUADRANT_BALANCE_TOLERANCE and
                            flow_ratio_min >= 1.0 - QUADRANT_BALANCE_TOLERANCE and
                            flow_ratio_max <= 1.0 + QUADRANT_BALANCE_TOLERANCE)
        )
        res = resolution_by_id.get(str(opening_id), {})
        parent_resolution_ok = bool(res.get("parent_resolution_ok"))
        quadrant_resolution_ok = bool(res.get("quadrant_resolution_ok"))
        jet_metrics_citable = bool(
            area_ok and parent_resolution_ok and quadrant_resolution_ok and
            quadrant_balance_ok and flow_status == "APPLIED"
        ) if role == "supply" else False
        source_labels = {str(item.get("source_label")) for item in children
                         if item.get("source_label") not in (None, "")}
        source_ids = {str(item.get("source_id")) for item in children
                      if item.get("source_id") not in (None, "")}
        inconsistent_parent = (len({str(item.get("parent_name") or "") for item in children}) > 1 or
                               len({str(item.get("role") or "") for item in children}) > 1 or
                               len({str(item.get("wall") or "") for item in children}) > 1)
        if inconsistent_parent:
            status = "WARN"
        elif not area_ok or (flow_control != "pressure_outlet" and not flow_ok):
            status = "WARN"
        elif role == "supply" and not jet_metrics_citable:
            status = "WARN"
        elif flow_control == "pressure_outlet":
            status = "RESULT_REQUIRED"
        else:
            status = "PASS"
        row = {
            "opening_id": str(opening_id),
            "parent_name": parent_name,
            "source_id": next(iter(source_ids), None),
            "source_label": next(iter(source_labels), None),
            "role": role,
            "type": first.get("type"),
            "wall": first.get("wall"),
            "child_patch_count": len(children),
            "child_patch_names": [str(item.get("name") or "") for item in children],
            "requested_rect": requested_rect,
            "snapped_rect": snapped_rect,
            "requested_area_m2": round(requested_area, 6) if requested_area is not None else None,
            "snapped_area_m2": round(snapped_area, 6),
            "area_ratio": round(area_ratio, 6) if area_ratio is not None else None,
            "area_within_tolerance": area_ok,
            "design_cmh": round(design_cmh, 3) if design_cmh is not None else None,
            "flow_control": flow_control,
            "applied_normal_cmh": (round(applied_normal_cmh, 3)
                                   if applied_normal_cmh is not None else None),
            "solved_cmh": None,
            "flow_ratio": round(flow_ratio, 6) if flow_ratio is not None else None,
            "flow_within_tolerance": flow_ok,
            "flow_status": flow_status,
            "requested_face_velocity_m_s": (
                round(design_cmh / 3600.0 / requested_area, 6)
                if role == "supply" and design_cmh is not None and requested_area not in (None, 0)
                else None
            ),
            "snapped_face_velocity_m_s": (
                round(applied_normal_cmh / 3600.0 / snapped_area, 6)
                if applied_normal_cmh is not None and snapped_area > 0 else None
            ),
            "quadrant_area_ratio_min": round(area_ratio_min, 6) if area_ratio_min is not None else None,
            "quadrant_area_ratio_max": round(area_ratio_max, 6) if area_ratio_max is not None else None,
            "quadrant_normal_cmh_ratio_min": round(flow_ratio_min, 6) if flow_ratio_min is not None else None,
            "quadrant_normal_cmh_ratio_max": round(flow_ratio_max, 6) if flow_ratio_max is not None else None,
            "quadrant_balance_ok": quadrant_balance_ok,
            "parent_resolution_ok": parent_resolution_ok,
            "quadrant_resolution_ok": quadrant_resolution_ok,
            "jet_metrics_citable": jet_metrics_citable,
            "inconsistent_parent": inconsistent_parent,
            "status": status,
        }
        terminals.append(row)
        if status == "WARN":
            warnings.append(parent_name)
        elif status == "RESULT_REQUIRED":
            result_required.append(parent_name)
    return {
        "contract": "opening_preflight.v2",
        "area_tolerance": area_tolerance,
        "flow_tolerance": flow_tolerance,
        "terminal_count": len(terminals),
        "terminals": terminals,
        "warnings": warnings,
        "result_required": result_required,
        "opening_resolution_ok": bool(terminals) and all(
            item["parent_resolution_ok"] for item in terminals
        ),
        "jet_metrics_citable": bool(terminals) and all(
            item["jet_metrics_citable"] for item in terminals if item["role"] == "supply"
        ),
    }


OPENING_BOUNDARY_VERIFICATION_CONTRACT = "opening_boundary_verification.v1"
OPENING_BOUNDARY_VERIFICATION_FILENAME = "opening_boundary_verification.v1.json"


def _latest_positive_time_dir(case_dir):
    candidates = []
    try:
        names = os.listdir(case_dir)
    except OSError:
        return None
    for name in names:
        path = os.path.join(case_dir, name)
        try:
            value = float(name)
        except (TypeError, ValueError):
            continue
        if value > 0 and os.path.isdir(path):
            candidates.append((value, name))
    return os.path.join(case_dir, max(candidates)[1]) if candidates else None


def _atomic_json_write(path, payload):
    directory = os.path.dirname(path) or "."
    fd, temporary = tempfile.mkstemp(prefix=".opening-verification.", suffix=".tmp",
                                     dir=directory, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def verify_opening_boundary_areas(case_dir, *, area_tolerance=0.03,
                                  flow_tolerance=0.10, write=True):
    """Verify predicted opening data against a recovered mesh/result.

    The build-time ``cfd_case_meta.json`` remains immutable provenance.  This
    result-side artifact may be regenerated after each solve and records both
    independently measured boundary face area (when ``polyMesh`` was kept)
    and signed/absolute ``phi`` flow where a result time is available.
    """
    case_dir = os.path.abspath(case_dir)
    meta_path = os.path.join(case_dir, "cfd_case_meta.json")
    try:
        with open(meta_path, encoding="utf-8") as stream:
            meta = json.load(stream)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return {
            "contract": OPENING_BOUNDARY_VERIFICATION_CONTRACT,
            "status": "NOT_AVAILABLE",
            "reason": f"case_meta_unavailable:{type(exc).__name__}",
            "terminals": [],
        }
    preflight = meta.get("opening_preflight")
    if not isinstance(preflight, dict) or preflight.get("contract") != "opening_preflight.v2":
        preflight = opening_preflight(meta.get("config") or {}, meta.get("patches") or [])

    mesh_metrics = None
    mesh_reason = None
    poly_mesh = os.path.join(case_dir, "constant", "polyMesh")
    if os.path.isdir(poly_mesh):
        try:
            import cfd_mesh
            mesh_metrics = cfd_mesh.patch_metrics(poly_mesh)
        except (OSError, ValueError, ImportError) as exc:
            mesh_reason = f"mesh_read_failed:{type(exc).__name__}"
    else:
        mesh_reason = "polyMesh_not_recovered"

    time_dir = _latest_positive_time_dir(case_dir)
    phi_path = os.path.join(time_dir, "phi") if time_dir else None
    phi_reason = None if phi_path and os.path.isfile(phi_path) else "phi_not_recovered"
    read_patch = None
    if phi_reason is None:
        try:
            from cfd_report import read_patch_field
            read_patch = read_patch_field
        except ImportError:
            phi_reason = "phi_reader_unavailable"

    rows = []
    has_warning = False
    has_partial = False
    for terminal in preflight.get("terminals") or []:
        children = [str(name) for name in terminal.get("child_patch_names") or []]
        predicted_area = terminal.get("snapped_area_m2")
        try:
            predicted_area = float(predicted_area)
        except (TypeError, ValueError):
            predicted_area = None
        actual_area = None
        missing_mesh_patches = []
        if mesh_metrics is not None:
            areas = []
            for name in children:
                metric = mesh_metrics.get(name)
                if metric is None:
                    missing_mesh_patches.append(name)
                else:
                    areas.append(float(metric.get("area_m2") or 0.0))
            if not missing_mesh_patches:
                actual_area = sum(areas)
        area_ratio = (actual_area / predicted_area
                      if actual_area is not None and predicted_area not in (None, 0) else None)
        if actual_area is None:
            area_status = "NOT_AVAILABLE"
        elif area_ratio is not None and abs(area_ratio - 1.0) <= area_tolerance:
            area_status = "PASS"
        else:
            area_status = "WARN"

        signed_cmh = None
        if read_patch is not None:
            values = []
            missing_phi_patches = []
            for name in children:
                phi = read_patch(phi_path, name)
                if isinstance(phi, tuple) and len(phi) == 2 and phi[0] == "uniform":
                    face_count = (mesh_metrics or {}).get(name, {}).get("faces")
                    try:
                        phi = [float(phi[1])] * int(face_count)
                    except (TypeError, ValueError):
                        phi = None
                if not isinstance(phi, list):
                    missing_phi_patches.append(name)
                else:
                    values.extend(float(value) for value in phi)
            if not missing_phi_patches:
                signed_cmh = sum(values) * 3600.0
        else:
            missing_phi_patches = list(children)
        solved_cmh = abs(signed_cmh) if signed_cmh is not None else None
        design_cmh = terminal.get("design_cmh")
        try:
            design_cmh = float(design_cmh) if design_cmh is not None else None
        except (TypeError, ValueError):
            design_cmh = None
        if signed_cmh is None:
            flow_status = "NOT_AVAILABLE"
            flow_ratio = None
        elif design_cmh is None or design_cmh <= 0:
            flow_status = "NO_TARGET"
            flow_ratio = None
        else:
            flow_ratio = solved_cmh / design_cmh
            # For the normal OpenFOAM sign convention a supply is inward
            # (negative) and an exhaust is outward (positive).  A sign flip is
            # a reverse-flow result, never silently treated as a flow match.
            expected_sign = 1.0 if terminal.get("role") == "exhaust" else -1.0
            sign_ok = signed_cmh * expected_sign > 0
            flow_status = ("PASS" if sign_ok and
                           abs(flow_ratio - 1.0) <= flow_tolerance else "WARN")
        row = {
            "opening_id": terminal.get("opening_id"),
            "parent_name": terminal.get("parent_name"),
            "role": terminal.get("role"),
            "flow_control": terminal.get("flow_control"),
            "child_patch_names": children,
            "predicted_area_m2": predicted_area,
            "actual_boundary_area_m2": round(actual_area, 8) if actual_area is not None else None,
            "area_ratio": round(area_ratio, 8) if area_ratio is not None else None,
            "area_status": area_status,
            "missing_mesh_patches": missing_mesh_patches,
            "design_cmh": design_cmh,
            "solved_signed_cmh": round(signed_cmh, 5) if signed_cmh is not None else None,
            "solved_cmh": round(solved_cmh, 5) if solved_cmh is not None else None,
            "flow_ratio": round(flow_ratio, 8) if flow_ratio is not None else None,
            "flow_status": flow_status,
            "missing_phi_patches": missing_phi_patches,
        }
        rows.append(row)
        has_warning = has_warning or area_status == "WARN" or flow_status == "WARN"
        has_partial = has_partial or area_status == "NOT_AVAILABLE" or flow_status == "NOT_AVAILABLE"
    payload = {
        "contract": OPENING_BOUNDARY_VERIFICATION_CONTRACT,
        "case": case_dir,
        "area_tolerance": area_tolerance,
        "flow_tolerance": flow_tolerance,
        "mesh_status": "AVAILABLE" if mesh_metrics is not None else "NOT_AVAILABLE",
        "mesh_reason": mesh_reason,
        "phi_time": os.path.basename(time_dir) if time_dir else None,
        "phi_status": "AVAILABLE" if read_patch is not None else "NOT_AVAILABLE",
        "phi_reason": phi_reason,
        "terminals": rows,
        "status": "WARN" if has_warning else ("PARTIAL" if has_partial else "PASS"),
    }
    if write:
        _atomic_json_write(os.path.join(case_dir, OPENING_BOUNDARY_VERIFICATION_FILENAME), payload)
    return payload


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
    """topoSetDict(1차): openings faceSet 만.
    ★cellZone 은 여기 넣지 않는다 — createPatch 가 메시를 재작성하며 기존 cellZone 을
    절단하는 것을 실측 확인(250→100셀). zone 은 gen_toposet_zones(2차, createPatch 후)."""
    room = cfg["room"]
    s = _hdr("dictionary", "topoSetDict") + "actions\n(\n"
    for p in patches:
        b = _face_box(p, room)
        s += (f"    {{ name {p['name']}f; type faceSet; action new; source boxToFace;\n"
              f"      box ({b[0]:.4f} {b[1]:.4f} {b[2]:.4f}) ({b[3]:.4f} {b[4]:.4f} {b[5]:.4f}); }}\n")
    s += ");\n"
    return s


# ── V3a: 실형상(방 폴리곤 + 장애물) — 고체 셀 라벨 분류 ─────────────────────
# 검증(G-V0): 셀 라벨 = i + nx·j + nx·ny·k (blockMesh 단일 hex, 파이썬 분류가
# boxToCell 과 250/250 일치). 고체화 = explicitPorositySource(중첩 DarcyForchheimerCoeffs,
# d=1e9, implicit 저항) → 장애물 내부 |U| ~1e-4. vectorFixedValueConstraint 는 압력보정
# 재구성 누설(~0.12 m/s)로 부적합 판정.

SOLID_DARCY = 1.0e9


def _cad_identity_token(value):
    """Return true only for scalar DXF-handle-like identity values."""
    return (isinstance(value, (str, int)) and not isinstance(value, bool)
            and bool(str(value).strip()))


def _cad_identity_in_reference(source_ref):
    """Whether a source-reference mapping carries a CAD/DXF identity."""
    if not isinstance(source_ref, Mapping):
        return False
    for key in ("handle", "source_handle"):
        if _cad_identity_token(source_ref.get(key)):
            return True
    handles = source_ref.get("handles")
    if _cad_identity_token(handles):
        return True
    if isinstance(handles, (list, tuple, set, frozenset)):
        return any(_cad_identity_token(handle) for handle in handles)
    return False


def _legacy_obstacle_cad_identity_path(obstacle):
    """Return the path of CAD identity in a legacy obstacle, if any.

    Older V3a payloads did not carry ``source_type``.  That compatibility
    path is safe only for a genuinely manual record.  A handle is durable DXF
    provenance, whether it is stored in the current ``source_ref`` or in an
    imported/original compatibility field, and may never be re-labelled as a
    manual heat source simply because the review marker is missing.
    """
    if not isinstance(obstacle, Mapping):
        return None
    reference_fields = (
        "source_ref",
        "original_source_ref",
        "original_imported_source_ref",
        "imported_source_ref",
        "dxf_source_ref",
    )
    for field in reference_fields:
        source_ref = obstacle.get(field)
        if _cad_identity_in_reference(source_ref):
            return field
        # Keep the shared provenance classifier in the loop for its canonical
        # ``source_handle`` handling; the direct check above also accepts
        # historical scalar ``handles`` values.
        if isinstance(source_ref, Mapping):
            try:
                if source_reference_kind(
                        source_ref, obstacle.get("source_id") or obstacle.get("id")) == "dxf":
                    return field
            except HeatSourceContractError:
                pass
    top_level_fields = (
        "handle", "source_handle", "handles", "source_handles",
        "original_handle", "original_source_handle",
        "original_handles", "original_source_handles",
        "imported_handle", "imported_source_handle",
        "imported_handles", "imported_source_handles",
        "dxf_handle", "dxf_source_handle", "dxf_handles",
    )
    for field in top_level_fields:
        value = obstacle.get(field)
        if _cad_identity_token(value):
            return field
        if isinstance(value, (list, tuple, set, frozenset)) and any(
                _cad_identity_token(handle) for handle in value):
            return field
    return None


def _obstacle_heat_contract(obstacle, index):
    """Normalize one legacy porous-equipment heat source.

    The V3a model can only inject convective heat into fluid cells.  Preserve
    the full input and the intentionally unmodelled radiative remainder so it
    agrees with the body-fitted thermal contract instead of silently applying
    the entire nameplate load.
    """
    # The reviewed geometry contract is W-based, while older V3a UI payloads
    # used only ``kw``.  A truthy-``kw`` fallback would silently turn a valid
    # ``input_power_w`` or ``power_kw`` source into a solid-only obstacle.
    has_nonzero_power = False
    for field, multiplier in (("input_power_w", 1.0), ("power_kw", 1000.0),
                              ("kw", 1000.0)):
        value = obstacle.get(field)
        if value in (None, ""):
            continue
        try:
            power_w = float(value) * multiplier
        except (TypeError, ValueError):
            # Let the shared normalizer reject malformed aliases below.
            has_nonzero_power = True
            break
        if not math.isfinite(power_w) or power_w != 0.0:
            has_nonzero_power = True
            break
    try:
        power_kw = float(obstacle.get("kw") or 0.0)
    except (TypeError, ValueError):
        raise SystemExit(f"장애물 {index + 1}: 발열(kW) 값이 올바르지 않습니다")
    if power_kw < 0:
        raise SystemExit(f"장애물 {index + 1}: 발열(kW)은 0 이상이어야 합니다")
    raw_fraction = obstacle.get("convective_fraction")
    if raw_fraction in (None, ""):
        # Historic manual screening cases had only `kw`.  Treat those as an
        # explicit all-convective legacy assumption, never as a claimed
        # confirmed equipment fraction.
        fraction = 1.0
        fraction_source = "legacy_default_all_convective"
    else:
        try:
            fraction = float(raw_fraction)
        except (TypeError, ValueError):
            raise SystemExit(
                f"장애물 {index + 1}: 대류분율은 0보다 크고 1 이하여야 합니다"
            )
        if not 0 < fraction <= 1:
            raise SystemExit(
                f"장애물 {index + 1}: 대류분율은 0보다 크고 1 이하여야 합니다"
            )
        fraction_source = "user_confirmed"
    source_id = str(obstacle.get("source_id") or obstacle.get("id")
                    or f"obstacle_{index}")
    source_label = str(obstacle.get("source_label") or obstacle.get("name")
                       or source_id)
    source_type_input = str(obstacle.get("source_type") or "").strip()
    source_type = source_type_input or "legacy_manual_input"
    source_ref = obstacle.get("source_ref")
    source_type_key = source_type.casefold()
    if has_nonzero_power:
        cad_identity_path = _legacy_obstacle_cad_identity_path(obstacle)
        if source_type_key == "dxf_detected":
            raise SystemExit(
                f"장애물 {index + 1}: DXF 검출 항목은 kW·대류분율·근거를 검토해 "
                "user_confirmed 열원으로 전환한 뒤 사용하세요"
            )
        if source_type_key == "legacy_manual_input" and cad_identity_path:
            raise SystemExit(
                f"장애물 {index + 1}: {cad_identity_path}에 CAD/DXF 식별자가 있어 "
                "source_type 없이 legacy_manual_input 열원으로 사용할 수 없습니다. "
                "검토 후 user_confirmed로 지정하세요"
            )
    # Older Studio payloads recorded a reviewed manual source by source_id
    # but predate the explicit source_ref field.  Give that *manual* path a
    # generated, unambiguous provenance record; never synthesize a DXF ref.
    if (source_type.casefold() == "user_confirmed"
            and (not isinstance(source_ref, dict) or not source_ref)):
        source_ref = {
            "layer": "USER_CONFIRMED",
            "block_name": source_label,
            "entity_type": "LEGACY_UI_INPUT",
            "source_id": source_id,
        }
    if power_kw == 0 and not has_nonzero_power:
        # Solid-only obstacles still pass through ``solid_labels``; they are
        # not heat sources and therefore do not enter the positive-load
        # normalizer.
        return {
            "i": index,
            "source_id": source_id,
            "source_label": source_label,
            "source_type": source_type,
            "source_ref": source_ref,
            "evidence": str(obstacle.get("evidence") or ""),
            "convective_fraction": 0.0,
            "radiative_fraction": 0.0,
            "input_power_w": 0.0,
            "convective_power_w": 0.0,
            "radiative_power_w": 0.0,
            "excluded_radiative_power_w": 0.0,
        }
    try:
        canonical = normalize_confirmed_heat_source({
            **obstacle,
            "source_id": source_id,
            "source_label": source_label,
            "source_type": source_type,
            "source_ref": source_ref,
            "convective_fraction": fraction,
        })
    except HeatSourceContractError as exc:
        raise SystemExit(f"장애물 {index + 1}: {exc}") from exc
    return {
        "i": index,
        **canonical,
        "convective_fraction_source": fraction_source,
        "override_of_dxf": obstacle.get("override_of_dxf") is True,
    }


def _equipment_heat_summary(cfg):
    """Return the shared input/applied/unmodelled heat contract for V3a."""
    sources = []
    for index, obstacle in enumerate((cfg or {}).get("obstacles") or []):
        contract = _obstacle_heat_contract(obstacle, index)
        if contract["input_power_w"] > 0.0:
            sources.append(contract)
    try:
        assert_unique_positive_source_ids(sources)
    except HeatSourceContractError as exc:
        raise SystemExit(f"장비별 발열원 계약 오류: {exc}") from exc
    return {
        "sources": sources,
        "source_count": len(sources),
        "input_power_w": sum(item["input_power_w"] for item in sources),
        "applied_convective_power_w": sum(
            item["convective_power_w"] for item in sources
        ),
        "radiative_power_w": sum(item["radiative_power_w"] for item in sources),
        "excluded_radiative_power_w": sum(
            item["excluded_radiative_power_w"] for item in sources
        ),
    }


def solid_labels(cfg, meshinfo):
    """cfg 의 room_polygon/obstacles → 셀 라벨 분류.
    반환 {"solid": [...], "equip": [(장애물 인덱스, kw, [라벨...]), ...]} (전부 정렬, 결정론).
    solid = 방 밖 ∪ 모든 장애물. equip = kw 지정 장애물별 라벨(발열 zone)."""
    from shapely.geometry import Point, Polygon, box as sbox
    room = cfg["room"]
    L, W, H = room["L"], room["W"], room["H"]
    nx, ny, nz = meshinfo["nx"], meshinfo["ny"], meshinfo["nz"]
    hu, hv, hz = L / nx, W / ny, H / nz

    poly = None
    if cfg.get("room_polygon"):
        poly = Polygon(cfg["room_polygon"])
        if not poly.is_valid:
            poly = poly.buffer(0)
    obs = []
    for oi, o in enumerate(cfg.get("obstacles", []) or []):
        if o.get("footprint"):
            g = Polygon(o["footprint"])
        elif o.get("bbox"):
            b = o["bbox"]
            g = sbox(b[0], b[1], b[2], b[3])
        else:
            raise SystemExit(f"obstacles[{oi}]: footprint 또는 bbox 필요")
        if not g.is_valid:
            g = g.buffer(0)
        h = float(o.get("h", H if o.get("kind") == "column" else 2.0))
        obs.append((oi, g, min(h, H), _obstacle_heat_contract(o, oi)))

    # 2D 선분류(z 무관) 캐시 → 3D 전개
    inside_room = [[True] * nx for _ in range(ny)]
    if poly is not None:
        for j in range(ny):
            y = (j + 0.5) * hv
            for i in range(nx):
                inside_room[j][i] = poly.contains(Point((i + 0.5) * hu, y))
    obs2d = []   # (oi, heat_contract, kmax, [(i,j)...])
    for oi, g, h, heat_contract in obs:
        cells = []
        for j in range(ny):
            y = (j + 0.5) * hv
            for i in range(nx):
                if g.contains(Point((i + 0.5) * hu, y)):
                    cells.append((i, j))
        if not cells:
            kind = (cfg.get("obstacles") or [])[oi].get("kind", "equipment")
            raise SystemExit(
                f"장애물 {oi + 1}({kind})이 현재 격자에서 한 셀도 차지하지 않습니다. "
                f"격자 셀({hu:.3f}×{hv:.3f}m)을 더 작게 하거나 장애물 크기·위치를 확인하세요."
            )
        kmax = max(1, min(nz, int(round(h / hz))))
        obs2d.append((oi, heat_contract, kmax, cells))

    solid = set()
    equip = []
    equip_contract = []
    plane = nx * ny
    if poly is not None:
        out2d = [(i, j) for j in range(ny) for i in range(nx) if not inside_room[j][i]]
        for k in range(nz):
            base = plane * k
            for (i, j) in out2d:
                solid.add(base + i + nx * j)
    for oi, heat_contract, kmax, cells in obs2d:
        labels = []
        for k in range(kmax):
            base = plane * k
            for (i, j) in cells:
                labels.append(base + i + nx * j)
        solid.update(labels)
        if heat_contract["convective_power_w"] > 0:
            equip.append((oi, heat_contract["convective_power_w"] / 1000.0,
                          sorted(labels)))
            equip_contract.append(dict(heat_contract, cells=len(labels)))
    heat_fluid = []
    if _heat_mode(cfg)["mode"] == "volume":
        # Match the former bottom boxToCell region, but explicitly remove the
        # room-exterior and obstacle cells.  Otherwise an L-shaped room can put
        # part of the declared total heat into hidden porous cells while still
        # reporting a deceptively perfect energy-closure percentage.
        kmax = max(1, min(nz, int(round(_heat_mode(cfg)["zone_frac"] * nz))))
        for k in range(kmax):
            base = plane * k
            for j in range(ny):
                for i in range(nx):
                    label = base + i + nx * j
                    if label not in solid:
                        heat_fluid.append(label)
    return {
        "solid": sorted(solid),
        "equip": equip,
        "equip_contract": equip_contract,
        "heat_fluid": heat_fluid,
    }


def gen_toposet_zones(cfg, labels):
    """topoSetDict.zones(2차, createPatch 후 실행): heatZone(기존 바닥층) 또는
    solidZone + 장비별 eqZone_i. labelToCell(파이썬 결정론 라벨)."""
    s = _hdr("dictionary", "topoSetDict.zones").replace("topoSetDict.zones", "topoSetDict") \
        + "actions\n(\n"
    if labels and labels["solid"]:
        vals = " ".join(map(str, labels["solid"]))
        s += (f"    {{ name solidCells; type cellSet;     action new; source labelToCell; value ({vals}); }}\n"
              "    { name solidZone;  type cellZoneSet; action new; source setToCellZone; set solidCells; }\n")
        for oi, kw, labs in labels["equip"]:
            vals = " ".join(map(str, labs))
            s += (f"    {{ name eqCells{oi}; type cellSet;     action new; source labelToCell; value ({vals}); }}\n"
                  f"    {{ name eqZone{oi};  type cellZoneSet; action new; source setToCellZone; set eqCells{oi}; }}\n")
    if _heat_mode(cfg)["mode"] == "volume":
        if labels is not None:
            heat_labels = labels.get("heat_fluid") or []
            if not heat_labels:
                raise SystemExit("발열원을 배치할 유체 셀이 없습니다 — 방 폴리곤·장애물·격자를 확인하세요")
            vals = " ".join(map(str, heat_labels))
            s += (f"    {{ name heatCells; type cellSet;     action new; source labelToCell; value ({vals}); }}\n"
                  "    { name heatZone;  type cellZoneSet; action new; source setToCellZone; set heatCells; }\n")
        else:
            room = cfg["room"]
            L, W, H = room["L"], room["W"], room["H"]
            zh = round(_heat_mode(cfg)["zone_frac"] * H, 4)
            s += (f"    {{ name heatZone; type cellSet;     action new; source boxToCell; box (0 0 0) ({L} {W} {zh}); }}\n"
                  "    { name heatZone; type cellZoneSet; action new; source setToCellZone; set heatZone; }\n")
    s += ");\n"
    return s


def gen_fvoptions_v3(cfg, labels):
    """fvOptions: 고체 다공 + 장비별 발열(또는 기존 바닥층 발열)."""
    s = _hdr("dictionary", "fvOptions")
    if labels and labels["solid"]:
        s += ("solidBlock\n{\n"
              "    type            explicitPorositySource;\n"
              "    active          yes;\n"
              "    explicitPorositySourceCoeffs\n    {\n"
              "        selectionMode   cellZone;\n"
              "        cellZone        solidZone;\n"
              "        type            DarcyForchheimer;\n"
              "        DarcyForchheimerCoeffs\n        {\n"
              f"            d   ({SOLID_DARCY:g} {SOLID_DARCY:g} {SOLID_DARCY:g});\n"
              "            f   (0 0 0);\n"
              "            coordinateSystem\n            {\n"
              "                origin (0 0 0);\n"
              "                rotation { type axesRotation; e1 (1 0 0); e2 (0 1 0); }\n"
              "            }\n        }\n    }\n}\n")
        for oi, kw, _labs in labels["equip"]:
            su = kw * 1000.0 / RHO_CP
            s += (f"heat_eq{oi}\n{{\n"
                  "    type            scalarSemiImplicitSource;\n"
                  "    volumeMode      absolute;\n"
                  "    selectionMode   cellZone;\n"
                  f"    cellZone        eqZone{oi};\n"
                  f"    injectionRateSuSp {{ T ({su:.6g} 0); }}\n"
                  "}\n")
    if _heat_mode(cfg)["mode"] == "volume":
        su = _heat_mode(cfg)["power_w"] / RHO_CP
        s += ("heatSource\n{\n"
              "    type            scalarSemiImplicitSource;\n"
              "    volumeMode      absolute;\n"
              "    selectionMode   cellZone;\n"
              "    cellZone        heatZone;\n"
              f"    injectionRateSuSp {{ T ({su:.6g} 0); }}\n"
              "}\n")
    return s


def validate_openings_fluid(cfg, patches, labels, meshinfo):
    """급배기 패치 안쪽의 *모든* 인접 셀이 유체인지 검사.

    A centre-point-only check allowed part of a diffuser on a sloped/L-shaped
    polygon edge to inject flow directly into a porous solid cell.
    """
    if not labels or not labels["solid"]:
        return
    solid = set(labels["solid"])
    room = cfg["room"]
    L, W, H = room["L"], room["W"], room["H"]
    nx, ny, nz = meshinfo["nx"], meshinfo["ny"], meshinfo["nz"]
    hu = {"x": L / nx, "y": W / ny, "z": H / nz}
    ncell = {"x": nx, "y": ny, "z": nz}
    edge_idx = {"x0": ("x", 0), "xL": ("x", nx - 1), "y0": ("y", 0), "yW": ("y", ny - 1),
                "floor": ("z", 0), "ceiling": ("z", nz - 1)}
    for p in patches:
        a0, b0, a1, b1 = p["rect_snap"]
        nax, nidx = edge_idx[p["wall"]]
        iu0 = max(0, int(round(a0 / hu[p["uax"]])))
        iu1 = min(ncell[p["uax"]], int(round(a1 / hu[p["uax"]])))
        iv0 = max(0, int(round(b0 / hu[p["vax"]])))
        iv1 = min(ncell[p["vax"]], int(round(b1 / hu[p["vax"]])))
        blocked = []
        for iu in range(iu0, iu1):
            for iv in range(iv0, iv1):
                idx = {"x": 0, "y": 0, "z": 0}
                idx[nax] = nidx
                idx[p["uax"]] = iu
                idx[p["vax"]] = iv
                label = idx["x"] + nx * idx["y"] + nx * ny * idx["z"]
                if label in solid:
                    blocked.append(label)
        if blocked:
            raise SystemExit(
                f"급배기구 '{p['name']}'({p['wall']}) 안쪽 {len(blocked)}개 셀이 "
                "고체(방 밖/장애물)입니다 — "
                "방 폴리곤이 그 벽에 닿는지, 장애물이 개구부를 막지 않는지 확인하세요.")


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
    # 초기장·배기역류 온도는 해석적 평형온도(에너지수지 해)로 통일한다.
    # 고정 300 K 로 두면 저환기 방에서 초기장이 안 빠져 폐합율이 부풀려진다.
    _tinit, _ = resolve_init_T(cfg, patches)
    Tinit = f"{_tinit:.6g}"
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


def gen_allrun(need_toposet, need_createpatch=False, need_zones=False):
    """Allrun 생성. openings 면 topoSet(faceSet)→createPatch, zone 은 그 뒤 별도
    topoSet(-dict topoSetDict.zones) — createPatch 가 cellZone 을 절단하므로(실측)."""
    toposet = ('echo "=== topoSet ==="\ntopoSet > log.topoSet 2>&1 || '
               '{ echo "topoSet FAILED"; tail -20 log.topoSet; exit 1; }\n') if need_toposet else ""
    createpatch = ('echo "=== createPatch ==="\ncreatePatch -overwrite > log.createPatch 2>&1 || '
                   '{ echo "createPatch FAILED"; tail -20 log.createPatch; exit 1; }\n') if need_createpatch else ""
    zones = ('echo "=== topoSet(zones) ==="\ntopoSet -dict system/topoSetDict.zones '
             '> log.topoSetZones 2>&1 || '
             '{ echo "topoSet zones FAILED"; tail -20 log.topoSetZones; exit 1; }\n') if need_zones else ""
    return ("#!/bin/bash\n"
            "set -o pipefail\n"
            "# RunFunctions 비의존(apt OpenFOAM 패키지엔 없음): 직접 호출 + 로그 리다이렉트.\n"
            'cd "${0%/*}" || exit\n'
            r"APP=$(sed -n 's/^application  *\([A-Za-z][A-Za-z]*\);.*/\1/p' system/controlDict)" + "\n"
            ': "${APP:=buoyantBoussinesqSimpleFoam}"\n'
            'echo "=== blockMesh ==="\n'
            'blockMesh > log.blockMesh 2>&1 || { echo "blockMesh FAILED"; tail -20 log.blockMesh; exit 1; }\n'
            + toposet + createpatch + zones
            + 'echo "=== checkMesh ==="\n'
            "checkMesh > log.checkMesh 2>&1; mesh_rc=$?; "
            "grep -E 'Mesh OK|\\*\\*\\*' log.checkMesh | head -3; "
            "[ \"$mesh_rc\" -eq 0 ] || { echo \"checkMesh FAILED (exit $mesh_rc)\"; "
            "tail -20 log.checkMesh; exit \"$mesh_rc\"; }\n"
            'echo "=== solver ($APP) ==="\n'
            '"$APP" 2>&1 | tee "log.$APP"\n'
            'solver_rc=${PIPESTATUS[0]}\n'
            '[ "$solver_rc" -eq 0 ] || { echo "solver FAILED (exit $solver_rc)"; exit "$solver_rc"; }\n'
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
    _tinit, _ = resolve_init_T(cfg)          # 해석적 평형온도 우선(위 주석 참조)
    Tinit = f"{_tinit:.6g}"
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


def _build_case_into(cfg, out_dir):
    """Build a case in an expendable directory (use ``build_case`` publicly)."""
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
    # openings/실형상 케이스: residualControl 제거 — 잔차 자가종료가 에너지 폐합 전에
    # 멈추는 것을 실측(G-V1: 770 iter 자가종료, 폐합 50%). 수렴 판정은 폐합 배지가 담당.
    if cfg.get("openings"):
        import re
        fs_path = os.path.join(out_dir, "system", "fvSolution")
        with open(fs_path, encoding="utf-8") as f:
            fs = f.read()
        fs2 = re.sub(r"residualControl\s*\{[^{}]*\}\s*", "", fs)
        if fs2 != fs:
            with open(fs_path, "w", encoding="utf-8") as f:
                f.write(fs2)
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
    labels = None
    # V3a 실형상: room_polygon(방 실폴리곤) / obstacles(기둥·장비 고체+개별발열)
    v3 = bool(cfg.get("room_polygon") or cfg.get("obstacles"))
    equipment_heat = _equipment_heat_summary(cfg)
    eq_input_w = equipment_heat["input_power_w"]
    eq_convective_w = equipment_heat["applied_convective_power_w"]
    if v3:
        if not has_openings:
            raise SystemExit("실형상(room_polygon/obstacles)은 급배기구(openings) 모드에서 사용하세요"
                             " — 벽 전체 급기와 방 폴리곤은 양립 불가")
        if eq_input_w > 0 and hm["mode"] != "none":
            raise SystemExit("발열은 obstacles[].kw 또는 heat.power_kw 중 하나만 지정")
        labels = solid_labels(cfg, meshinfo)
    if has_openings:
        patches = resolve_openings(cfg, meshinfo)
        if labels:
            validate_openings_fluid(cfg, patches, labels, meshinfo)
        for name, txt in gen_0_openings(cfg, patches).items():
            _w(os.path.join(out_dir, "0", name), txt)
        _w(os.path.join(out_dir, "system", "topoSetDict"), gen_toposet_all(cfg, patches))
        _w(os.path.join(out_dir, "system", "createPatchDict"), gen_createpatch(patches))
        need_zones = (hm["mode"] == "volume") or bool(labels and labels["solid"])
        if need_zones:
            # ★zone 은 createPatch 후 별도 topoSet 로 생성(절단 함정 실측 — G-V0)
            _w(os.path.join(out_dir, "system", "topoSetDict.zones"),
               gen_toposet_zones(cfg, labels))
            _w(os.path.join(out_dir, "constant", "fvOptions"),
               gen_fvoptions_v3(cfg, labels))
        _w(os.path.join(out_dir, "Allrun"),
           gen_allrun(need_toposet=True, need_createpatch=True, need_zones=need_zones))
    else:
        for name, txt in gen_0(cfg, roles).items():
            _w(os.path.join(out_dir, "0", name), txt)
        # 발열 kW 모드: 바닥층 cellZone + 체적 발열원 (v1 경로 — 파일 구성 불변)
        if hm["mode"] == "volume":
            _w(os.path.join(out_dir, "system", "topoSetDict"), gen_toposet(cfg))
            _w(os.path.join(out_dir, "constant", "fvOptions"), gen_fvoptions(cfg))
        _w(os.path.join(out_dir, "Allrun"),
           gen_allrun(need_toposet=(hm["mode"] == "volume"), need_createpatch=False))
    os.chmod(os.path.join(out_dir, "Allrun"), 0o755)
    # 생성 요약(리포트에서 가정값 표기용)
    heat_meta = dict(hm)
    if eq_input_w > 0:
        heat_meta = {
            "mode": "volume",
            # ``power_w`` remains the actually injected value for legacy
            # closure and equilibrium calculations.
            "power_w": eq_convective_w,
            "input_power_w": eq_input_w,
            "applied_convective_power_w": eq_convective_w,
            "radiative_power_w": equipment_heat["radiative_power_w"],
            "excluded_radiative_power_w": equipment_heat[
                "excluded_radiative_power_w"
            ],
            "source_count": equipment_heat["source_count"],
            "via": "obstacles",
            "model": "porous_voxel_equipment_heat_v1",
        }
    meta = {"config": cfg, "mesh": meshinfo, "roles": roles, "heat": heat_meta}
    # 실제로 0/T 에 쓰인 초기장 온도와 그 근거를 남긴다. cfg["init"]["T"] 는 사용자가
    # 적어 넣은 값이라, 평형온도로 덮어쓴 경우 둘이 달라진다 — 이력 추적을 위해 분리 기록.
    try:
        _t_init, _t_note = resolve_init_T(cfg, patches)
        meta["init_applied"] = {"T_K": _t_init, **{k: v for k, v in _t_note.items()
                                                   if k != "T_init_K"}}
    except Exception:
        pass
    if v3:
        meta["model_quality"] = {
            "method": "porous_voxel_screening",
            "design_ready": False,
            "warning": ("도면 폴리곤·장애물을 균일격자 다공성 셀로 근사한 예비 스크리닝 모델입니다. "
                        "body-fitted wall mesh/고체 열전달 모델이 아니므로 확정설계 판정용으로 사용하지 마세요.")
        }
    if patches:
        meta["patches"] = [{k: v for k, v in p.items() if k not in ("uax", "vax")}
                           for p in patches]
        meta["opening_preflight"] = opening_preflight(cfg, patches)
    if labels:
        meta["solid_n"] = len(labels["solid"])
        if labels.get("heat_fluid"):
            meta["heat_fluid_n"] = len(labels["heat_fluid"])
        meta["equip_zones"] = [
            {
                **source,
                "kw": source["convective_power_w"] / 1000.0,
            }
            for source in labels.get("equip_contract") or []
        ]
    _w(os.path.join(out_dir, "cfd_case_meta.json"),
       json.dumps(meta, ensure_ascii=False, indent=2))
    return meshinfo, roles


def _is_generated_case(path):
    """True only for a directory that this exporter can safely replace."""
    return (os.path.isdir(path)
            and os.path.isfile(os.path.join(path, "cfd_case_meta.json"))
            and os.path.isfile(os.path.join(path, "Allrun"))
            and os.path.isdir(os.path.join(path, "system"))
            and os.path.isdir(os.path.join(path, "constant")))


def _replace_with_retry(src, dst, attempts=6):
    """Rename on Windows despite a short-lived antivirus/indexer file lock."""
    for attempt in range(attempts):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if attempt + 1 == attempts:
                raise
            time.sleep(0.05 * (attempt + 1))


def build_case(cfg, out_dir):
    """Validate and atomically publish a generated OpenFOAM case.

    The previous implementation unconditionally removed ``out_dir``.  A typo
    such as ``-o .`` could therefore delete unrelated project files, and a
    generation error left a half-built folder that the Studio could not retry.
    Build in a sibling staging directory, refuse unknown existing directories,
    and keep the previous valid case until the replacement is complete.
    """
    validate_config(cfg)
    out_dir = os.path.abspath(os.fspath(out_dir))
    parent = os.path.dirname(out_dir)
    name = os.path.basename(out_dir.rstrip("\\/"))
    if not name:
        raise SystemExit("출력 케이스 폴더 경로가 올바르지 않습니다")
    os.makedirs(parent, exist_ok=True)
    if os.path.exists(out_dir) and not _is_generated_case(out_dir):
        raise SystemExit(
            f"안전을 위해 기존 폴더를 덮어쓰지 않았습니다: {out_dir}\n"
            "cfd_case_meta.json과 Allrun이 있는 기존 CFD 케이스만 재생성할 수 있습니다."
        )

    stage = tempfile.mkdtemp(prefix=f".{name}.building-", dir=parent)
    backup = None
    try:
        result = _build_case_into(cfg, stage)
        if os.path.exists(out_dir):
            backup = os.path.join(parent, f".{name}.previous-{uuid.uuid4().hex[:8]}")
            _replace_with_retry(out_dir, backup)
        _replace_with_retry(stage, out_dir)
        stage = None
        if backup:
            try:
                shutil.rmtree(backup)
            except OSError:
                # The newly published case is valid.  A locked old result file
                # should not roll it back; the hidden backup remains recoverable.
                pass
        return result
    except BaseException:
        if backup and not os.path.exists(out_dir) and os.path.exists(backup):
            _replace_with_retry(backup, out_dir)
        raise
    finally:
        if stage and os.path.exists(stage):
            shutil.rmtree(stage, ignore_errors=True)


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
        if r.get("kind") == "circle" and r.get("center"):
            cx, cy = r["center"][:2]
            radius = abs(float(r.get("radius") or 0.0))
            xs.extend((cx - radius, cx + radius))
            ys.extend((cy - radius, cy + radius))
    if not xs:
        return None
    return (min(xs), min(ys), max(xs), max(ys))


_AIR_TERMINAL_RE = re.compile(
    r"HFB(?:[-_ ]?\d+)?|DIFF|DIFFUSER|GRILLE|REGISTER|AIR.?TERMINAL|SUPPLY.?AIR|RETURN.?AIR|EXHAUST.?AIR|"
    r"디퓨저|그릴|급기구|배기구|환기구", re.IGNORECASE)
_EXHAUST_TERMINAL_RE = re.compile(
    r"RETURN|EXHAUST|RELIEF|RA(?:_|-)|EA(?:_|-)|배기|환기", re.IGNORECASE)


def _equipment_semantics(record):
    """Classify a parsed equipment record for CFD suggestions.

    ``equipment`` is retained in the shared geometry schema for compatibility,
    but a pump/panel must never be proposed as a diffuser.  The parser now keeps
    ``block_name``; layer/name remain useful for older geometry files.
    """
    explicit = dict(record.get("semantic") or {})
    if explicit.get("kind") == "air_terminal":
        return {
            "kind": "air_terminal",
            "role": explicit.get("role") or "unresolved",
            "suggested_role": explicit.get("suggested_role"),
            "role_suggestion_confidence": explicit.get("role_suggestion_confidence"),
            "role_suggestion_source": explicit.get("role_suggestion_source"),
            "type": explicit.get("terminal_type") or explicit.get("type")
                    or ("round" if record.get("kind") == "circle" else "4way"),
            "airflow_cmh": explicit.get("airflow_cmh"),
            "host_surface": explicit.get("host_surface") or "ceiling",
            "requires_role_review": explicit.get("role") not in ("supply", "exhaust"),
            "source_type": explicit.get("source_type"),
            "override_of_dxf": explicit.get("override_of_dxf") is True,
        }
    text = " ".join(str(record.get(k) or "") for k in
                    ("block_name", "name", "layer", "source_layer"))
    if _AIR_TERMINAL_RE.search(text):
        role = "exhaust" if _EXHAUST_TERMINAL_RE.search(text) else "supply"
        typ = "grille" if re.search(r"GRILLE|REGISTER|그릴", text, re.IGNORECASE) else "4way"
        return {"kind": "air_terminal", "role": role, "type": typ,
                "suggested_role": role, "requires_role_review": False}
    return {"kind": "equipment"}


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
        semantic = _equipment_semantics(eq)
        if semantic["kind"] != "air_terminal":
            continue
        e = _xy_extent([eq])
        if not e:
            continue
        cx, cy = (e[0] + e[2]) / 2, (e[1] + e[3]) / 2
        if not (x0 <= cx <= x1 and y0 <= cy <= y1):
            continue
        source_ref = dict(eq.get("source_ref") or {})
        source_id = str(eq.get("id") or source_ref.get("handle") or "").strip()
        source_label = str(
            eq.get("block_name") or eq.get("layer")
            or source_ref.get("block_name") or source_ref.get("layer")
            or source_id or "air_terminal"
        )
        source_type = str(
            semantic.get("source_type") or eq.get("source_type")
            or ("dxf_detected" if source_id else "")
        ).strip()
        out.append({"cx": round((cx - x0) / 1000.0, 3),
                    "cy": round((cy - y0) / 1000.0, 3),
                    "w": max(0.1, round((e[2] - e[0]) / 1000.0, 3)),
                    "h": max(0.1, round((e[3] - e[1]) / 1000.0, 3)),
                    "name": source_label,
                    "source_id": source_id or None,
                    "source_label": source_label,
                    "source_ref": source_ref,
                    "source_type": source_type or None,
                    "override_of_dxf": semantic.get("override_of_dxf") is True,
                    "role": semantic["role"], "suggested_role": semantic.get("suggested_role"),
                    "role_suggestion_confidence": semantic.get("role_suggestion_confidence"),
                    "role_suggestion_source": semantic.get("role_suggestion_source"),
                    "type": semantic["type"],
                    "airflow_cmh": semantic.get("airflow_cmh"),
                    "host_surface": semantic.get("host_surface") or "ceiling",
                    "requires_role_review": bool(semantic.get("requires_role_review"))})
    return out


def obstacles_from_geometry(geom, zone=None, bbox=None):
    """도면 columns/equipment → V3a 장애물 후보(방 로컬 m).
    반환 {"room_polygon": [[x,y]..]|None, "obstacles": [{kind,bbox,h?,name}...]}.
    kw 는 도면에 없음 — 사용자가 지정(정직)."""
    el = geom.get("elements", {})
    src_poly = None
    if zone is not None:
        zones = el.get("zone", [])
        if zone >= len(zones):
            raise SystemExit(f"zone {zone} 없음")
        src_poly = zones[zone].get("points")
        ext = _xy_extent([zones[zone]])
    elif bbox is not None:
        ext = tuple(bbox)
    else:
        ext = _xy_extent(el.get("wall", []))
    if not ext:
        return {"room_polygon": None, "obstacles": []}
    x0, y0, x1, y1 = ext

    def to_local(px, py):
        return [round((px - x0) / 1000.0, 3), round((py - y0) / 1000.0, 3)]

    room_poly = None
    if src_poly and len(src_poly) >= 3:
        room_poly = [to_local(p[0], p[1]) for p in src_poly]

    obstacles = []
    for col in el.get("column", []):
        if col.get("kind") == "circle" and col.get("center"):
            c, r = col["center"], float(col.get("radius") or 200.0)
            e = (c[0] - r, c[1] - r, c[0] + r, c[1] + r)
        else:
            e = _xy_extent([col])
        if not e:
            continue
        cx, cy = (e[0] + e[2]) / 2, (e[1] + e[3]) / 2
        if not (x0 <= cx <= x1 and y0 <= cy <= y1):
            continue
        lo, hi = to_local(e[0], e[1]), to_local(e[2], e[3])
        obstacles.append({"kind": "column", "bbox": [lo[0], lo[1], hi[0], hi[1]],
                          "name": col.get("layer") or "column"})
    for eq in el.get("equipment", []):
        if _equipment_semantics(eq)["kind"] == "air_terminal":
            continue   # terminal is a boundary opening, not a solid obstacle
        semantic = eq.get("semantic") or {}
        if semantic.get("needs_review") and eq.get("confirmed") is not True:
            # Inferred equipment (for example an EHP candidate reconstructed
            # from nearby SA/RA text) has neither a measured footprint nor a
            # verified installation height.  Preserve it in geometry for CAD
            # review, but never turn it into a CFD solid automatically.
            continue
        e = _xy_extent([eq])
        if not e:
            continue
        cx, cy = (e[0] + e[2]) / 2, (e[1] + e[3]) / 2
        if not (x0 <= cx <= x1 and y0 <= cy <= y1):
            continue
        lo, hi = to_local(e[0], e[1]), to_local(e[2], e[3])
        source_ref = dict(eq.get("source_ref") or {})
        source_id = str(eq.get("id") or source_ref.get("handle")
                        or f"equipment_{len(obstacles)}")
        source_label = str(eq.get("block_name") or eq.get("layer")
                           or source_ref.get("block_name")
                           or source_ref.get("layer") or source_id)
        height_mm = semantic.get("height_mm")
        try:
            height_m = float(height_mm) / 1000.0 if height_mm not in (None, "") else 2.0
        except (TypeError, ValueError):
            height_m = 2.0
        record = {
            "kind": "equipment",
            "bbox": [lo[0], lo[1], hi[0], hi[1]],
            "h": height_m if height_m > 0 else 2.0,
            "name": source_label,
            "source_id": source_id,
            "source_label": source_label,
            "source_ref": source_ref,
            "source_type": str(semantic.get("source_type") or "").strip() or None,
            "override_of_dxf": semantic.get("override_of_dxf") is True,
        }
        if semantic.get("role") == "heat_source":
            try:
                input_power = semantic.get("input_power_w")
                if input_power not in (None, ""):
                    input_power_w = float(input_power)
                    power_kw = input_power_w / 1000.0
                else:
                    power_kw = float(semantic.get("power_kw") or 0.0)
                    input_power_w = power_kw * 1000.0
                fraction = float(semantic.get("convective_fraction"))
            except (TypeError, ValueError):
                input_power_w, power_kw, fraction = 0.0, 0.0, 0.0
            heat_source_type = str(semantic.get("source_type") or "").strip().casefold()
            if (eq.get("confirmed") is True and input_power_w > 0
                    and 0 < fraction <= 1
                    and heat_source_type == "user_confirmed"):
                record.update({
                    "input_power_w": input_power_w,
                    "kw": power_kw,
                    "convective_fraction": fraction,
                    "evidence": str(semantic.get("evidence") or ""),
                    "source_type": heat_source_type,
                    "override_of_dxf": semantic.get("override_of_dxf") is True,
                })
            else:
                # A DXF match and a confirmed solid footprint are not a
                # reviewed thermal-load decision.  Keep the obstacle for the
                # V3a geometry preview, but never inject its nominal power
                # until the user explicitly confirms a heat-source contract.
                record["heat_input_needs_review"] = True
        obstacles.append(record)
    return {"room_polygon": room_poly, "obstacles": obstacles}


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
