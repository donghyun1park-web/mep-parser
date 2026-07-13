"""
cfd_report.py — OpenFOAM 해석 결과 리포트 (도면→CFD 파이프라인)

Phase 1a: 잔차 로그 파서 + 수렴 그래프 ("측정 먼저" 하니스).
Phase 2 에서 지표 표 + 단면 컨투어 + 자립 HTML 로 확장.

사용:
  python cfd_report.py <solver.log>            # 로그 → 잔차 그래프 PNG
  python cfd_report.py <case_dir>              # case/log.* 자동 탐색
  python cfd_report.py <log> -o residuals.png

설계: 눈으로 로그를 읽어 "수렴한 것 같다"고 판단하지 않는다. 이 파서가 iteration 별 잔차·
continuity·rho·bounding(불안정 신호)·크래시를 수치로 뽑아, 안정화 시도 전/후를 객관 비교한다.
외부 의존성 없음(stdlib + matplotlib, 이미 프로젝트에서 사용).
"""
import argparse
import os
import re
import sys

# ── 로그 파싱 정규식 (표준 OpenFOAM SIMPLE/PIMPLE 로그) ──────────────────────
_RE_TIME = re.compile(r"^Time = ([\d.eE+-]+)\s*$")
_RE_RESID = re.compile(
    r"Solving for (\w+),\s*Initial residual = ([\d.eE+-]+),\s*Final residual = ([\d.eE+-]+)")
_RE_CONT = re.compile(
    r"time step continuity errors : sum local = ([\d.eE+-]+), global = ([\d.eE+-]+)")
_RE_RHO = re.compile(r"rho min/max\s*:\s*([\d.eE+-]+)\s+([\d.eE+-]+)")
_RE_BOUND = re.compile(r"bounding (\w+), min: ([\d.eE+-]+) max: ([\d.eE+-]+)")
# 실제 크래시 시그니처만 (시작 배너 "trapFpe: ... trapping enabled (FOAM_SIGFPE)" 는 정상 → 제외)
_RE_CRASH = re.compile(r"sigFpe::sigHandler|error::printStack|\(core dumped\)|"
                       r"Foam::fatalError|#\d+\s+Foam::")


def _f(s):
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


def parse_log(text):
    """OpenFOAM 솔버 로그 텍스트 → 구조화 결과.
    반환: {
      iters: [time,...],                 # 각 Time = N (SIMPLE 반복)
      residuals: {field: [initial,...]}, # iteration별 초기잔차
      continuity_local: [...], continuity_global: [...],
      rho_min: [...], rho_max: [...],    # (압축성 솔버만; Boussinesq 는 빔)
      bounding: [(time, field, min, max), ...],  # 불안정 신호
      crashed: bool, n_iters: int
    }"""
    iters = []
    residuals = {}
    cont_local, cont_global = [], []
    rho_min, rho_max = [], []
    bounding = []
    crashed = False
    cur_time = None
    seen_fields_this_step = set()

    for line in text.splitlines():
        m = _RE_TIME.match(line)
        if m:
            cur_time = _f(m.group(1))
            iters.append(cur_time)
            seen_fields_this_step = set()
            continue
        if _RE_CRASH.search(line):
            crashed = True
            continue
        m = _RE_RESID.search(line)
        if m and cur_time is not None:
            field, init = m.group(1), _f(m.group(2))
            # 한 스텝에서 같은 필드는 첫(초기) 잔차만 (nCorrectors 대비)
            if field in seen_fields_this_step:
                continue
            seen_fields_this_step.add(field)
            residuals.setdefault(field, {})[cur_time] = init
            continue
        m = _RE_CONT.search(line)
        if m and cur_time is not None:
            cont_local.append((cur_time, _f(m.group(1))))
            cont_global.append((cur_time, _f(m.group(2))))
            continue
        m = _RE_RHO.search(line)
        if m and cur_time is not None:
            rho_min.append((cur_time, _f(m.group(1))))
            rho_max.append((cur_time, _f(m.group(2))))
            continue
        m = _RE_BOUND.search(line)
        if m:
            bounding.append((cur_time, m.group(1), _f(m.group(2)), _f(m.group(3))))

    # 필드별 시계열을 iter 순서로 정렬된 리스트로
    resid_series = {}
    for field, d in residuals.items():
        resid_series[field] = [d.get(t) for t in iters]

    return {
        "iters": iters,
        "residuals": resid_series,
        "continuity_local": cont_local,
        "continuity_global": cont_global,
        "rho_min": rho_min,
        "rho_max": rho_max,
        "bounding": bounding,
        "crashed": crashed,
        "n_iters": len(iters),
    }


def diagnose(parsed):
    """파싱 결과 → 사람이 읽는 진단 요약 문자열 목록."""
    out = []
    n = parsed["n_iters"]
    out.append(f"반복(iteration): {n}")
    if parsed["crashed"]:
        out.append("★ 크래시(sigFpe/발산) 감지 — 솔버가 도중 종료됨.")
    # 마지막 초기잔차
    for field in ("Ux", "Uy", "Uz", "p_rgh", "h", "k", "omega", "epsilon"):
        ser = parsed["residuals"].get(field)
        if ser:
            vals = [v for v in ser if v is not None]
            if vals:
                out.append(f"  {field:7s} 초기잔차: 시작 {vals[0]:.2e} → 끝 {vals[-1]:.2e}"
                           + ("  (하강)" if vals[-1] < vals[0] else "  (미하강/발산)"))
    # rho 음수(압축성 발산 신호)
    if parsed["rho_min"]:
        mn = min(v for _, v in parsed["rho_min"] if v is not None)
        if mn < 0:
            out.append(f"★ 음의 밀도 감지(rho min={mn:.2f}) — 압축성 솔버 발산의 전형. "
                       "Boussinesq(비압축) 전환 권장.")
    if parsed["bounding"]:
        flds = sorted({b[1] for b in parsed["bounding"]})
        out.append(f"  bounding 경고 {len(parsed['bounding'])}회 (필드: {', '.join(flds)}) — 국소 불안정.")
    # 유량수지(continuity global)
    if parsed["continuity_global"]:
        last = parsed["continuity_global"][-1][1]
        out.append(f"  최종 continuity(global): {last:.2e}" + ("  (양호)" if abs(last) < 1e-3 else "  (큼)"))
    return out


def plot_residuals(parsed, out_png, title="OpenFOAM convergence (initial residuals)"):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    iters = parsed["iters"]
    if not iters:
        return None
    fig, ax = plt.subplots(figsize=(11, 6))
    order = ["Ux", "Uy", "Uz", "p_rgh", "h", "T", "k", "omega", "epsilon"]
    plotted = 0
    for field in order:
        ser = parsed["residuals"].get(field)
        if not ser:
            continue
        xs = [t for t, v in zip(iters, ser) if v is not None and v > 0]
        ys = [v for v in ser if v is not None and v > 0]
        if len(ys) >= 2:
            ax.semilogy(xs, ys, label=field, lw=1.3)
            plotted += 1
    ax.set_xlabel("iteration")
    ax.set_ylabel("initial residual (log)")
    ax.set_title(title + (f"  —  ★크래시" if parsed["crashed"] else ""))
    ax.grid(True, which="both", alpha=0.3)
    if plotted:
        ax.legend(fontsize=9, ncol=2)
    # 크래시/bounding 표시
    if parsed["crashed"] and iters:
        ax.axvline(iters[-1], color="red", ls="--", alpha=0.6)
        ax.text(iters[-1], ax.get_ylim()[1], " crash", color="red", va="top", fontsize=9)
    fig.tight_layout()
    fig.savefig(out_png, dpi=110)
    plt.close(fig)
    return out_png


# ── 결과 필드 판독 (함수객체 우회: ascii 필드 직접 파싱) ────────────────────
# 이 OpenFOAM v1912 apt 빌드는 함수객체(인라인·postProcess)가 SHA1 버그로 깨졌다.
# 대신 writeFormat=ascii + 균일 구조격자(blockMesh 단일 hex)라는 사실을 이용해,
# 최종 time 디렉토리의 필드를 직접 읽고 셀 인덱스(i + nx*j + nx*ny*k)로 좌표를 복원한다.

def read_field(path):
    """OpenFOAM ascii volScalar/volVectorField 의 internalField 판독.
    반환: ('scalar', [float,...]) | ('vector', [(x,y,z),...])
          | ('uniform_scalar', v) | ('uniform_vector', (x,y,z)) | None"""
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8", errors="replace") as f:
        txt = f.read()
    m = re.search(r"internalField\s+(uniform|nonuniform)", txt)
    if not m:
        return None
    if m.group(1) == "uniform":
        rest = txt[m.end():].lstrip()
        if rest.startswith("("):
            vec = tuple(float(x) for x in re.match(r"\(([^)]*)\)", rest).group(1).split())
            return ("uniform_vector", vec)
        val = float(re.match(r"[-\d.eE+]+", rest).group(0))
        return ("uniform_scalar", val)
    cm = re.search(r"List<(scalar|vector)>\s*\n?\s*(\d+)\s*\n?\s*\(", txt)
    if not cm:
        return None
    is_vec = cm.group(1) == "vector"
    start = cm.end()
    end = txt.index("\n)", start)
    body = txt[start:end]
    if is_vec:
        data = [tuple(float(x) for x in v.split())
                for v in re.findall(r"\(([^)]+)\)", body)]
        return ("vector", data)
    return ("scalar", [float(x) for x in body.split()])


def _as_array(field, ncells):
    """read_field 결과 → numpy 배열(uniform 은 상수 확장). scalar:(N,) vector:(N,3)."""
    import numpy as np
    if field is None:
        return None
    kind, data = field
    if kind == "scalar":
        return np.asarray(data, float)
    if kind == "vector":
        return np.asarray(data, float)
    if kind == "uniform_scalar":
        return np.full(ncells, float(data))
    if kind == "uniform_vector":
        return np.tile(np.asarray(data, float), (ncells, 1))
    return None


def find_latest_time(case_dir):
    """case 디렉토리의 최종(0 아님) time 디렉토리 경로. 없으면 None."""
    times = []
    for name in os.listdir(case_dir):
        full = os.path.join(case_dir, name)
        if os.path.isdir(full) and re.fullmatch(r"\d+(\.\d+)?", name):
            times.append((float(name), full))
    times = [t for t in times if t[0] > 0]
    if not times:
        return None
    return max(times, key=lambda t: t[0])[1]


def read_patch_field(path, patch):
    """time-dir 필드파일의 boundaryField[patch] 값 판독 → [float,...] 또는 상수.
    inletOutlet/calculated 등도 'value' 리스트를 씀. 없으면 None."""
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8", errors="replace") as f:
        txt = f.read()
    # boundaryField 안의 해당 patch 블록만 잘라내기(중괄호 균형)
    m = re.search(r"\b" + re.escape(patch) + r"\s*\{", txt)
    if not m:
        return None
    i = m.end(); depth = 1
    while i < len(txt) and depth:
        if txt[i] == "{":
            depth += 1
        elif txt[i] == "}":
            depth -= 1
        i += 1
    seg = txt[m.end():i - 1]
    mv = re.search(r"value\s+nonuniform\s+List<scalar>\s*\n?\s*(\d+)\s*\n?\s*\(", seg)
    if mv:
        s = mv.end()
        e = seg.find("\n)", s)
        if e == -1:                       # 소형 패치는 한 줄 인라인 `N(a b c)` 포맷
            e = seg.index(")", s)
        return [float(x) for x in seg[s:e].split()]
    mu = re.search(r"value\s+uniform\s+([-\d.eE]+)", seg)
    if mu:
        return ("uniform", float(mu.group(1)))
    return None


def energy_closure(case_dir, meta):
    """발열 kW(체적발열원) 케이스의 에너지 폐합 검증.
    정상상태 + 단열벽이면 주입열 P = 유량가중 배기 엔탈피유출 ρcp·Σ(phi·(T-Tref)).
    잔차가 아니라 이 폐합율(≈100%)이 발열 케이스의 진짜 수렴/신뢰 지표.
    v2: meta['patches'](급배기구 모드)면 배기 패치 전부 합산 + 질량수지 추가.
    반환: {closure_pct, outlet_dT, power_w, vdot[, mass_err_pct]} 또는 None."""
    heat = meta.get("heat", {})
    if heat.get("mode") != "volume":
        return None
    tdir = find_latest_time(case_dir)
    if not tdir:
        return None
    Tref = float(meta["config"].get("inlet", {}).get("T", 293))
    exh_names = ["outlet"]
    patches = meta.get("patches")
    if patches:
        exh_names = [p["name"] for p in patches if p["role"] == "exhaust"]
        sups = [p for p in patches if p["role"] == "supply"]
        if sups:
            Tref = float(sups[0].get("T") or Tref)   # 기준 = 급기온도

    # 회수된 모든 time 스냅샷(최근 3개)에 대해 폐합 계산 → 평균.
    # 이유(실측): 4way 등 제트 충돌 유동은 진동 정상상태(limit cycle) — 단일 스냅샷
    # 폐합이 87~104% 로 요동해 수렴을 오판. 반복 평균이 올바른 판정.
    tdirs = []
    for name in os.listdir(case_dir):
        full = os.path.join(case_dir, name)
        if os.path.isdir(full) and re.fullmatch(r"\d+(\.\d+)?", name) and float(name) > 0:
            tdirs.append((float(name), full))
    tdirs = [d for _, d in sorted(tdirs)][-3:]   # 최근 3개만(과거 과도기 스냅샷 배제)
    power_w = float(heat.get("power_w", 0))
    su = power_w / 1206.0                             # 주입 Su (ρ0·cp=1206)
    samples = []                                      # (closure, vdot, outlet_dT)
    for td in tdirs:
        vdot = enth = 0.0
        got = False
        for nm in exh_names:
            phi = read_patch_field(os.path.join(td, "phi"), nm)
            T = read_patch_field(os.path.join(td, "T"), nm)
            if not isinstance(phi, list) or not isinstance(T, list) or len(phi) != len(T):
                continue
            got = True
            vdot += sum(phi)                                     # 순 배기유량(m³/s)
            enth += sum(p * (t - Tref) for p, t in zip(phi, T))  # Σ phi·ΔT (K·m³/s)
        if got and su:
            samples.append((enth / su * 100.0, vdot, (enth / vdot if vdot else None)))
    if not samples:
        return None
    clos = [s[0] for s in samples]
    closure = sum(clos) / len(clos)
    osc = (max(clos) - min(clos)) / 2 if len(clos) > 1 else 0.0
    vdot = samples[-1][1]
    dts = [s[2] for s in samples if s[2] is not None]
    out = {"closure_pct": closure, "closure_osc": round(osc, 1),
           "closure_n": len(samples),
           "outlet_dT": (sum(dts) / len(dts) if dts else None),
           "power_w": power_w, "vdot": vdot}
    if patches:
        qin = sum((p.get("cmh") or 0) for p in patches if p["role"] == "supply") / 3600.0
        if qin > 0:
            out["mass_err_pct"] = (vdot - qin) / qin * 100.0   # 배기순유량 vs 급기설계유량
    return out


def field_metrics(case_dir, meta):
    """최종 time 디렉토리 → T/U 통계 + 유량·환기 지표(가정값 명시)."""
    import numpy as np
    tdir = find_latest_time(case_dir)
    if not tdir:
        return None
    n = meta["mesh"]["cells"]
    T = _as_array(read_field(os.path.join(tdir, "T")), n)
    U = _as_array(read_field(os.path.join(tdir, "U")), n)
    room = meta["config"]["room"]
    L, W, H = room["L"], room["W"], room["H"]
    inlet = meta["config"].get("inlet", {})
    Tsup_K = float(inlet.get("T", 293))
    out = {
        "time_dir": os.path.basename(tdir),
        "T_supply_C": Tsup_K - 273.15,
        "room_volume": L * W * H,
    }
    if T is not None:
        Tc = T - 273.15
        out.update(T_avg_C=float(Tc.mean()), T_max_C=float(Tc.max()),
                   T_min_C=float(Tc.min()), dT_rise=float(Tc.mean() - (Tsup_K - 273.15)))
    if U is not None and U.ndim == 2:
        mag = np.linalg.norm(U, axis=1)
        out.update(U_max=float(mag.max()), U_avg=float(mag.mean()))
    # 급기 풍량: fixedValue inlet BC(정확) × 벽 면적. 최소모델은 '벽 전체' → 비현실적일 수 있어 명시.
    roles = meta.get("roles", {})
    wall = inlet.get("wall")
    area = {"x0": W * H, "xL": W * H, "y0": L * H, "yW": L * H,
            "floor": L * W, "ceiling": L * W}.get(wall)
    Uvec = inlet.get("U", [0, 0, 0])
    Umag = float(np.linalg.norm(Uvec)) if Uvec else 0.0
    if area and Umag > 0:
        Q = Umag * area              # m3/s
        out["supply_area"] = area
        out["supply_U"] = Umag
        out["supply_cmh"] = Q * 3600.0
        out["ach"] = (Q * 3600.0) / (L * W * H)
        out["supply_full_wall"] = (wall in ("x0", "xL", "y0", "yW", "floor", "ceiling"))
    # v2 급배기구 모드: 설계 풍량은 패치 정의에서 정확히(스냅 실면적 반영)
    patches = meta.get("patches")
    if patches:
        sup_cmh = sum((p.get("cmh") or 0) for p in patches if p["role"] == "supply")
        if sup_cmh:
            out["supply_cmh"] = sup_cmh
            out["ach"] = sup_cmh / (L * W * H)
            out["supply_full_wall"] = False
        out["n_supply"] = len({p["name"].split("_q")[0] for p in patches
                               if p["role"] == "supply"})
        out["n_exhaust"] = sum(1 for p in patches if p["role"] == "exhaust")
    # 발열 kW 케이스: 에너지 폐합 검증(신뢰 지표)
    ec = energy_closure(case_dir, meta)
    if ec:
        out["heat_kw"] = ec["power_w"] / 1000.0
        out["closure_pct"] = ec["closure_pct"]
        out["closure_osc"] = ec.get("closure_osc")
        out["outlet_dT"] = ec["outlet_dT"]
        out["mass_err_pct"] = ec.get("mass_err_pct")
    return out


def _cell_grid(field_arr, meta):
    """평탄 셀배열 → (nz, ny, nx) 재배열 + 셀중심 좌표축. blockMesh 단일 hex 가정."""
    import numpy as np
    nx, ny, nz = meta["mesh"]["nx"], meta["mesh"]["ny"], meta["mesh"]["nz"]
    room = meta["config"]["room"]
    L, W, H = room["L"], room["W"], room["H"]
    g = np.asarray(field_arr[:nx * ny * nz]).reshape(nz, ny, nx)  # [k,j,i]
    xc = (np.arange(nx) + 0.5) * L / nx
    yc = (np.arange(ny) + 0.5) * W / ny
    zc = (np.arange(nz) + 0.5) * H / nz
    return g, xc, yc, zc


def plot_sections(case_dir, meta, out_png, z_target=1.5):
    """수평(z=z_target)·수직(길이방향 중앙) 온도 단면 2매 + 수직면 기류벡터."""
    import numpy as np
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    tdir = find_latest_time(case_dir)
    if not tdir:
        return None
    n = meta["mesh"]["cells"]
    T = _as_array(read_field(os.path.join(tdir, "T")), n)
    U = _as_array(read_field(os.path.join(tdir, "U")), n)
    if T is None:
        return None
    Tg, xc, yc, zc = _cell_grid(T - 273.15, meta)
    nz, ny, nx = Tg.shape
    room = meta["config"]["room"]
    L, W, H = room["L"], room["W"], room["H"]
    kz = int(np.clip(round(z_target / H * nz - 0.5), 0, nz - 1))
    jy = ny // 2

    vmin, vmax = float(Tg.min()), float(Tg.max())
    if vmax - vmin < 0.5:
        vmax = vmin + 0.5
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13, 5.2))

    # (1) 수평면 z=z_target : 평면 온도분포
    c1 = ax1.contourf(xc, yc, Tg[kz], levels=24, cmap="turbo", vmin=vmin, vmax=vmax)
    ax1.set_title(f"Horizontal plane  z = {zc[kz]:.2f} m  (temperature)")
    ax1.set_xlabel("x [m]"); ax1.set_ylabel("y [m]"); ax1.set_aspect("equal")
    fig.colorbar(c1, ax=ax1, label="T [C]", shrink=0.85)

    # (2) 수직면 y=mid : 성층 + 기류(inlet->outlet, 부력순환)
    Tv = Tg[:, jy, :]                      # (nz, nx)
    c2 = ax2.contourf(xc, zc, Tv, levels=24, cmap="turbo", vmin=vmin, vmax=vmax)
    if U is not None and U.ndim == 2:
        Ug = U[:nx * ny * nz].reshape(nz, ny, nx, 3)
        Ux = Ug[:, jy, :, 0]; Uz = Ug[:, jy, :, 2]
        s = max(1, nx // 20)
        ax2.quiver(xc[::s], zc[::max(1, nz // 12)],
                   Ux[::max(1, nz // 12), ::s], Uz[::max(1, nz // 12), ::s],
                   color="white", scale=None, width=0.003, alpha=0.8)
    ax2.set_title(f"Vertical plane  y = {yc[jy]:.2f} m  (temp + airflow)")
    ax2.set_xlabel("x [m]"); ax2.set_ylabel("height z [m]"); ax2.set_aspect("equal")
    fig.colorbar(c2, ax=ax2, label="T [C]", shrink=0.85)

    fig.tight_layout()
    fig.savefig(out_png, dpi=120)
    plt.close(fig)
    return out_png


def find_log(path):
    """case 디렉토리면 log.*Foam 최신 파일 탐색, 파일이면 그대로."""
    if os.path.isfile(path):
        return path
    if os.path.isdir(path):
        import glob
        cands = glob.glob(os.path.join(path, "log.*Foam")) + \
            glob.glob(os.path.join(path, "log.*Simple*")) + \
            glob.glob(os.path.join(path, "log.*Pimple*"))
        cands = [c for c in cands if "checkMesh" not in c and "blockMesh" not in c]
        if cands:
            return max(cands, key=os.path.getmtime)
    return None


def convergence_badge(parsed, metrics):
    """수렴 판정 → (배지문구, 색). 리포트·대시보드(cfd_studio)가 같은 판정 공유.
    발열 kW 케이스는 '에너지 폐합율'이 잔차보다 신뢰성 높은 게이트(부력지배 약유동은
    잔차가 떨어져도 에너지가 안 닫힘 → 미수렴을 잔차가 오판, 실측 확인)."""
    m = metrics or {}
    cont_ok = parsed["continuity_global"] and abs(parsed["continuity_global"][-1][1]) < 1e-3
    clo = m.get("closure_pct")
    if parsed.get("crashed"):
        return ("발산/크래시", "#c0392b")
    if clo is not None:
        osc = m.get("closure_osc") or 0
        tag = f"{clo:.0f}%" + (f"±{osc:.0f}" if osc >= 5 else "")
        if 90 <= clo <= 110:
            return (f"수렴(에너지폐합 {tag})", "#1e8449")
        return (f"미수렴(에너지폐합 {tag})", "#b9770e")
    if cont_ok:
        return ("수렴(양호)", "#1e8449")
    return ("부분수렴/확인필요", "#b9770e")


def case_summary(case_dir):
    """케이스 폴더 → 대시보드 행 dict. 실행 전(meta만)·실행 후·리포트 유무 모두 처리.
    meta 없으면 None(케이스 아님)."""
    import glob as _glob
    import math
    meta = _load_meta(case_dir)
    if not meta:
        return None
    cfg = meta.get("config", {})
    room = cfg.get("room", {})
    heat = meta.get("heat", {})
    Uvec = cfg.get("inlet", {}).get("U", [0, 0, 0])
    supply_u = math.sqrt(sum(float(v) ** 2 for v in Uvec)) if Uvec else 0.0
    if heat.get("mode") == "volume":
        heat_label = f"{heat.get('power_w', 0) / 1000.0:g} kW"
    elif heat.get("mode") == "surface":
        heat_label = f"바닥 {heat.get('floor_T', '?')}K"
    else:
        heat_label = "—"
    out = {
        "dir": os.path.basename(os.path.abspath(case_dir)),
        "name": cfg.get("name") or os.path.basename(case_dir),
        "room": f"{room.get('L','?')}×{room.get('W','?')}×{room.get('H','?')}",
        "cells": meta.get("mesh", {}).get("cells"),
        "heat_label": heat_label,
        "heat_kw": (heat.get("power_w", 0) / 1000.0) if heat.get("mode") == "volume" else None,
        "supply_u": round(supply_u, 3),
        "endTime": cfg.get("endTime"),
        "mtime": os.path.getmtime(os.path.join(case_dir, "cfd_case_meta.json")),
        "status": "created",
        "badge": "미실행", "badge_color": "#7f8c8d",
        "T_avg_C": None, "T_max_C": None, "dT_rise": None,
        "closure_pct": None, "outlet_dT": None, "n_iters": None,
        "gci": meta.get("gci"),
        "report": None,
        "from_geometry": bool(meta.get("from_geometry")),
    }
    logp = find_log(case_dir)
    if logp:
        out["status"] = "ran"
        with open(logp, encoding="utf-8", errors="replace") as f:
            parsed = parse_log(f.read())
        metrics = None
        try:
            metrics = field_metrics(case_dir, meta)
        except Exception:
            pass
        out["badge"], out["badge_color"] = convergence_badge(parsed, metrics)
        out["n_iters"] = parsed["n_iters"]
        if metrics:
            for k in ("T_avg_C", "T_max_C", "dT_rise", "closure_pct", "outlet_dT",
                      "supply_cmh", "ach", "U_max", "mass_err_pct", "n_supply", "n_exhaust"):
                out[k] = metrics.get(k)
    reps = _glob.glob(os.path.join(case_dir, "cfd_report_*.html"))
    if reps:
        out["report"] = os.path.basename(max(reps, key=os.path.getmtime))
        if out["status"] == "ran":
            out["status"] = "reported"
    return out


def _b64(png_path):
    import base64
    with open(png_path, "rb") as f:
        return "data:image/png;base64," + base64.b64encode(f.read()).decode()


def _fmt(v, unit="", nd=1):
    if v is None:
        return "—"
    return f"{v:,.{nd}f}{unit}"


def build_html_report(case_dir, meta, parsed, resid_png, sect_png, metrics, out_html):
    """자립 HTML 해석 리포트(보고서 첨부 품질). preview.py 계열 스타일."""
    import datetime
    cfg = meta.get("config", {})
    name = cfg.get("name", os.path.basename(case_dir))
    room = cfg.get("room", {})
    diag = diagnose(parsed)
    m = metrics or {}
    badge, bcol = convergence_badge(parsed, metrics)
    # 해석조건(입력 가정) 행
    assum = []
    fg = meta.get("from_geometry")
    if fg:
        src = cfg.get("_note", "").split("·")[1].strip() if "·" in cfg.get("_note", "") else fg.get("source", "")
        prov = f"도면 자동추출 ({fg.get('source','?')})"
        if fg.get("equipment"):
            prov += f" · 장비 {fg['equipment']}개 감지(바닥발열로 단순화)"
        if fg.get("openings_by_wall"):
            prov += f" · 경계 개구부 {fg['openings_by_wall']}"
        assum.append(("치수 출처", prov))
    assum.append(("실 치수", f"{room.get('L','?')} × {room.get('W','?')} × {room.get('H','?')} m (L×W×H)"))
    assum.append(("체적", _fmt(m.get("room_volume"), " m³", 1)))
    assum.append(("격자", f"{meta['mesh']['nx']}×{meta['mesh']['ny']}×{meta['mesh']['nz']} = {meta['mesh']['cells']:,} cells (셀 {cfg.get('mesh',{}).get('cell','?')} m)"))
    assum.append(("솔버", "buoyantBoussinesqSimpleFoam (정상상태·부력·비압축 Boussinesq)"))
    patches = meta.get("patches")
    if patches:
        # v2 급배기구 목록 (스냅된 실면적·실풍량)
        seen = {}
        for p in patches:
            base = p["name"].split("_q")[0]
            e = seen.setdefault(base, {"role": p["role"], "type": p.get("type"),
                                       "wall": p["wall"], "area": 0.0, "cmh": 0.0})
            e["area"] += p.get("area") or 0
            e["cmh"] += p.get("cmh") or 0
        rows_txt = " · ".join(
            (f"{k}[{v['type']},{v['wall']}] {v['area']:.2f}㎡ {v['cmh']:.0f}CMH" if v["role"] == "supply"
             else f"{k}[배기,{v['wall']}] {v['area']:.2f}㎡")
            for k, v in seen.items())
        assum.append(("급배기구", rows_txt))
        assum.append(("총 급기(설계)", f"{_fmt(m.get('supply_cmh'),' CMH',0)} · {_fmt(m.get('ach'),' ACH',1)}"))
    elif m.get("supply_cmh"):
        fw = " ※최소모델: 벽면 전체를 급기로 단순화 → 풍량·ACH 비현실적, 급배기구(openings) 모드 권장" if m.get("supply_full_wall") else ""
        assum.append(("급기(가정)", f"{_fmt(m.get('supply_U'),' m/s',3)} × {_fmt(m.get('supply_area'),' m²',1)} = {_fmt(m.get('supply_cmh'),' CMH',0)} · {_fmt(m.get('ach'),' ACH',1)}{fw}"))
    assum.append(("급기온도(가정)", _fmt(m.get("T_supply_C"), " °C", 1)))
    heat = cfg.get("heat", {})
    if m.get("heat_kw") is not None:
        assum.append(("발열(입력)", f"{_fmt(m.get('heat_kw'),' kW',1)} — 바닥층 체적 발열원(계산서 총발열 직결). 실발열량 반영 시 값 지정."))
    elif heat.get("floor_T") is not None:
        assum.append(("발열(가정)", f"바닥 {heat.get('floor_T','?')}K 고정온도 = 장비 총발열 단순화 (실발열량 아님)"))

    # 결과 지표 행
    res = []
    res.append(("평균 온도", _fmt(m.get("T_avg_C"), " °C", 1)))
    res.append(("최고 온도(핫스팟)", _fmt(m.get("T_max_C"), " °C", 1)))
    res.append(("최저 온도", _fmt(m.get("T_min_C"), " °C", 1)))
    res.append(("급기 대비 상승 ΔT", _fmt(m.get("dT_rise"), " K", 1)))
    res.append(("최대 유속", _fmt(m.get("U_max"), " m/s", 3)))
    if m.get("closure_pct") is not None:
        cv = m["closure_pct"]
        osc = m.get("closure_osc") or 0
        mark = "✓ 신뢰" if 90 <= cv <= 110 else "✗ 미수렴 — 반복↑ 또는 급기유속 현실화 필요"
        oscnote = f" ±{osc:.0f} (진동 유동 — 최근 스냅샷 평균)" if osc >= 5 else ""
        res.append(("에너지 폐합율 (주입열=배기열)", f"{cv:.0f}%{oscnote} &nbsp;<b>{mark}</b>"))
        res.append(("배기 온도상승(유량가중)", _fmt(m.get("outlet_dT"), " K", 2)))
    if m.get("mass_err_pct") is not None:
        mv = m["mass_err_pct"]
        res.append(("질량수지 (배기−급기)/급기", f"{mv:+.1f}%"
                    + (" &nbsp;<b>✓</b>" if abs(mv) < 2 else " &nbsp;<b>✗ 확인 필요</b>")))
    res.append(("반복(iteration)", f"{parsed['n_iters']}"))
    if parsed["continuity_global"]:
        res.append(("최종 연속방정식 오차(global)", f"{parsed['continuity_global'][-1][1]:.2e}"))

    def rows(pairs):
        return "\n".join(
            f'<tr><th>{k}</th><td>{v}</td></tr>' for k, v in pairs)

    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    diag_html = "<br>".join(x.replace("★", "⚠") for x in diag)
    sect_img = f'<img src="{_b64(sect_png)}" alt="단면">' if sect_png and os.path.exists(sect_png) else "<p>(단면 없음 — 결과 필드 미기록)</p>"
    resid_img = f'<img src="{_b64(resid_png)}" alt="수렴">' if resid_png and os.path.exists(resid_png) else ""

    html = f"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>CFD 해석 리포트 — {name}</title>
<style>
 :root{{--fg:#1a1a1a;--muted:#666;--line:#e2e2e2;--bg:#fff;--card:#fafafa;--accent:#2c5f8a}}
 *{{box-sizing:border-box}} body{{font-family:'Malgun Gothic','Segoe UI',sans-serif;margin:0;color:var(--fg);background:#f0f2f5}}
 .page{{max-width:960px;margin:24px auto;background:var(--bg);padding:36px 44px;box-shadow:0 1px 8px rgba(0,0,0,.08);border-radius:10px}}
 h1{{font-size:24px;margin:0 0 4px}} .sub{{color:var(--muted);font-size:13px;margin-bottom:18px}}
 .badge{{display:inline-block;color:#fff;background:{bcol};padding:3px 12px;border-radius:14px;font-size:13px;font-weight:600;vertical-align:middle;margin-left:8px}}
 h2{{font-size:16px;margin:26px 0 10px;padding-bottom:6px;border-bottom:2px solid var(--accent);color:var(--accent)}}
 table{{width:100%;border-collapse:collapse;font-size:14px}}
 th,td{{text-align:left;padding:7px 10px;border-bottom:1px solid var(--line);vertical-align:top}}
 th{{width:34%;background:var(--card);font-weight:600;color:#333}}
 .warn{{background:#fff8e1;border:1px solid #f0d060;border-radius:8px;padding:10px 14px;font-size:13px;color:#7a5c00;margin:10px 0}}
 .diag{{background:var(--card);border-left:4px solid var(--accent);padding:12px 16px;font-size:13.5px;line-height:1.7;border-radius:4px}}
 img{{max-width:100%;height:auto;border:1px solid var(--line);border-radius:6px;margin-top:8px}}
 .foot{{color:var(--muted);font-size:12px;margin-top:28px;border-top:1px solid var(--line);padding-top:12px}}
 code{{background:#eee;padding:1px 6px;border-radius:4px;font-size:12px}}
</style></head><body><div class="page">
 <h1>CFD 해석 리포트 — {name} <span class="badge">{badge}</span></h1>
 <div class="sub">전기실 발열·환기 정상상태 해석 · 생성 {now} · case <code>{os.path.basename(case_dir)}</code></div>

 <div class="warn">⚠ <b>본 리포트의 풍량·발열·온도는 설계 <u>가정값</u>이며 확정 설계값이 아닙니다.</b>
  실디퓨저 면적·장비별 실발열량·급기조건을 반영하면 수치가 달라집니다. 방법론·경향 검토용입니다.</div>

 <h2>1. 수렴성 판정</h2>
 <div class="diag">{diag_html}</div>
 {resid_img}

 <h2>2. 해석 조건 (입력 가정)</h2>
 <table>{rows(assum)}</table>

 <h2>3. 결과 지표</h2>
 <table>{rows(res)}</table>

 <h2>4. 온도·기류 단면</h2>
 {sect_img}
 <div class="sub">좌: 수평면(작업/장비 높이) 온도분포 — 핫스팟 위치. 우: 길이방향 수직면 — 바닥 발열에 의한
  온도 성층과 급기→배기 기류(흰 화살표). 색 범례 단위 °C.</div>

 <div class="foot">생성 도구: cfd_report.py (도면→CFD 파이프라인) · OpenFOAM {meta.get('_of','v1912')} ·
  재현: <code>python cfd_export.py &lt;config&gt; -o &lt;case&gt; &amp;&amp; python cfd_run.py &lt;case&gt; &amp;&amp; python cfd_report.py &lt;case&gt;</code></div>
</div></body></html>"""
    with open(out_html, "w", encoding="utf-8") as f:
        f.write(html)
    return out_html


def _load_meta(case_dir):
    import json
    p = os.path.join(case_dir, "cfd_case_meta.json")
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    return None


def generate_report(case_dir, out_html=None, quiet=True):
    """케이스 디렉토리 → HTML 리포트 생성(그래프·지표·단면 포함).
    CLI(main)와 스튜디오(cfd_studio)가 공용. 반환: (out_html, metrics) 또는 로그 없으면 예외."""
    logpath = find_log(case_dir)
    if not logpath or not os.path.exists(logpath):
        raise FileNotFoundError(f"솔버 로그 없음: {case_dir}")
    with open(logpath, encoding="utf-8", errors="replace") as f:
        parsed = parse_log(f.read())
    meta = _load_meta(case_dir)
    if not meta:
        raise FileNotFoundError(f"cfd_case_meta.json 없음: {case_dir}")

    def note(msg):
        if not quiet:
            print(msg, file=sys.stderr)

    resid_png = os.path.join(case_dir, "_residuals.png")
    try:
        plot_residuals(parsed, resid_png)
    except Exception as e:
        note(f"수렴 그래프 스킵: {e}")
        resid_png = None
    metrics = None
    try:
        metrics = field_metrics(case_dir, meta)
    except Exception as e:
        note(f"지표 계산 스킵: {e}")
    sect_png = os.path.join(case_dir, "_sections.png")
    try:
        if not plot_sections(case_dir, meta, sect_png):
            sect_png = None
    except Exception as e:
        note(f"단면 스킵: {e}")
        sect_png = None
    out_html = out_html or os.path.join(
        case_dir, f"cfd_report_{meta.get('config', {}).get('name', 'case')}.html")
    build_html_report(case_dir, meta, parsed, resid_png, sect_png, metrics, out_html)
    return out_html, metrics


def main():
    ap = argparse.ArgumentParser(description="OpenFOAM 결과 → 수렴 그래프·지표·단면 HTML 리포트")
    ap.add_argument("input", help="솔버 로그 파일 또는 case 디렉토리")
    ap.add_argument("-o", "--out", default=None, help="출력(로그모드=PNG, 케이스모드=HTML) 경로")
    args = ap.parse_args()

    logpath = find_log(args.input)
    if not logpath or not os.path.exists(logpath):
        print(f"로그를 찾을 수 없음: {args.input}", file=sys.stderr)
        sys.exit(1)
    with open(logpath, encoding="utf-8", errors="replace") as f:
        parsed = parse_log(f.read())

    print(f"로그: {logpath}")
    for line in diagnose(parsed):
        print(line)

    # 케이스 디렉토리 + meta 있으면 전체 HTML 리포트
    case_dir = args.input if os.path.isdir(args.input) else os.path.dirname(logpath)
    if _load_meta(case_dir):
        out_html, metrics = generate_report(case_dir, out_html=args.out, quiet=False)
        if metrics:
            print(f"  평균 {_fmt(metrics.get('T_avg_C'),'°C')} · 최고 {_fmt(metrics.get('T_max_C'),'°C')} · "
                  f"ΔT {_fmt(metrics.get('dT_rise'),'K')} · 최대유속 {_fmt(metrics.get('U_max'),'m/s',3)}")
        print(f"HTML 리포트 -> {out_html}")
    else:
        resid_png = os.path.join(case_dir, "_residuals.png")
        try:
            plot_residuals(parsed, resid_png)
        except Exception as e:
            print(f"수렴 그래프 스킵: {e}", file=sys.stderr)
            resid_png = None
        out = args.out or (os.path.splitext(logpath)[0] + "_residuals.png")
        if resid_png and resid_png != out and os.path.exists(resid_png):
            import shutil
            shutil.copy(resid_png, out)
        print(f"수렴 그래프 -> {out}  (meta 없음 → 로그 전용 모드)")


if __name__ == "__main__":
    main()
