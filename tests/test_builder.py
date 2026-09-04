# -*- coding: utf-8 -*-
"""freecad_builder 커버리지.

이 파일이 생기기 전까지 빌더(1,100여 줄, **산출물을 실제로 만드는 코드**)는
자동 검사가 하나도 없었다. 거기서 나온 버그 셋 — 죽은 apply_opening_voids,
마커만 찍고 파일은 안 옮기던 것, elements["beam"] 을 아예 안 읽던 것 — 전부
사람이 읽어서 찾았다. 다음 사람은 못 찾는다.

두 층으로 덮는다:
  · 순수 함수(체이닝/되꺾임 분할) — FreeCAD 를 stub 으로 세우고 바로 부른다.
  · 엔드투엔드 — freecadcmd 로 실제 빌드하고 <out>.build.json 을 검사한다.
    freecadcmd 가 없으면 [skip] 을 찍고 건너뛴다 — 통과처럼 보이는 미실행 금지.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest.mock as _mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)


# ── 순수 함수: FreeCAD 를 stub 으로 세우고 임포트 ──────────────────────────
def _load_builder():
    """freecad_builder 는 FreeCAD 를 모듈 레벨에서 임포트한다. 기하 계산부만
    보려면 stub 으로 충분하다 — stub 으로 안 되는 게 생기면 그건 순수 함수가
    아니라는 신호이므로, 여기서 실패하는 것이 옳다."""
    saved = {k: sys.modules.get(k) for k in ("FreeCAD", "Part", "Draft", "Arch")}
    for k in saved:
        sys.modules[k] = _mock.MagicMock()
    try:
        import importlib
        return importlib.import_module("freecad_builder")
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


FB = _load_builder()


def _w(pts, **kw):
    d = {"kind": "polyline", "closed": False, "centerline": pts, "points": pts}
    d.update(kw)
    return d


def test_connected_walls_chain_into_one():
    """잘게 쪼개진 세그먼트가 BIM 객체로도 쪼개지면 코너 miter 가 안 된다."""
    chains = FB._chain_wall_segments([_w([[0, 0], [1000, 0]]),
                                      _w([[1000, 0], [2000, 0]]),
                                      _w([[2000, 0], [2000, 1000]])])
    assert len(chains) == 1, chains
    assert chains[0][1][0] == [0, 0], chains[0][1]
    assert chains[0][1][-1] == [2000, 1000], chains[0][1]


def test_disconnected_walls_stay_separate():
    chains = FB._chain_wall_segments([_w([[0, 0], [1000, 0]]),
                                      _w([[9000, 0], [10000, 0]])])
    assert len(chains) == 2


def test_closed_walls_are_not_chained():
    """닫힌 폴리선은 solid extrusion 경로다 — 체이닝에 섞이면 안 된다."""
    chains = FB._chain_wall_segments([_w([[0, 0], [1000, 0]], pairing="closed"),
                                      _w([[0, 0], [1000, 0], [1000, 1000]],
                                         closed=True)])
    assert chains == [], chains


def test_fold_back_chain_is_split():
    """★ 27km 좌표 사고. 겹친 동일선상 벽이 A→B→A 로 체이닝되면
    Arch.makeWall(align="Center") 오프셋이 발산하는데 isValid() 는 True 다."""
    out = FB._split_folded_chain([[0, 0], [5000, 0], [1000, 0]])
    assert len(out) == 2, out
    assert out[0] == [[0, 0], [5000, 0]] and out[1] == [[5000, 0], [1000, 0]]


def test_straight_and_corner_chains_are_not_split():
    assert len(FB._split_folded_chain([[0, 0], [1000, 0], [2000, 0]])) == 1
    assert len(FB._split_folded_chain([[0, 0], [1000, 0], [1000, 1000]])) == 1


def test_zero_length_segments_are_dropped_not_split_on():
    """중복점은 방향이 없다 — 되꺾임으로 오해해 자르면 벽이 잘게 부서진다."""
    assert FB._split_folded_chain([[0, 0], [0, 0], [1000, 0]]) == [[[0, 0], [1000, 0]]]
    assert FB._split_folded_chain([[0, 0], [1000, 0]]) == [[[0, 0], [1000, 0]]]


# ── 엔드투엔드: 실제 freecadcmd 빌드 ──────────────────────────────────────
_FREECAD = (shutil.which("freecadcmd")
            or r"C:\Program Files\FreeCAD 1.1\bin\freecadcmd.exe")


def _skip_if_no_freecad():
    """건너뛰는 것을 조용히 넘기지 않는다 — 통과처럼 보이는 미실행이 제일 나쁘다."""
    if os.path.exists(_FREECAD):
        return True
    print(f"  [skip] freecadcmd 없음 — 엔드투엔드 빌드 검사를 건너뜀 ({_FREECAD})")
    return False


def _build(geom_path, out_base):
    env = dict(os.environ, MEP_GEOMETRY=geom_path, MEP_OUT=out_base)
    r = subprocess.run([_FREECAD, os.path.join(ROOT, "freecad_builder.py")],
                       cwd=ROOT, env=env, capture_output=True, timeout=300)
    out = (r.stdout or b"").decode("utf-8", "ignore")
    with open(out_base + ".build.json", encoding="utf-8") as f:
        return out, json.load(f)


def test_end_to_end_build_of_the_sample_plan():
    """빌더가 실제로 무엇을 만들었는지 <out>.build.json 으로 확인한다.

    'walls=3' 같은 출력 문자열이 아니라 산출물의 자기기술을 본다 — 종전에
    '빌드 완료' 를 찍고도 IFC 에 보가 0개인 채로 나간 적이 있다."""
    if not _skip_if_no_freecad():
        return
    geom = os.path.join(ROOT, "_t_sample.json")
    import dxf_parser as dp
    import contextlib
    import io
    rules = dp.load_layer_map(os.path.join(ROOT, "layer_map.csv"))
    blocks = dp.load_layer_map(os.path.join(ROOT, "block_map.csv"))
    with contextlib.redirect_stdout(io.StringIO()):
        data = dp.parse(os.path.join(ROOT, "sample_plan.dxf"), rules, blocks)
    with open(geom, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    base = os.path.join(tempfile.mkdtemp(prefix="mepbuild_"), "out")
    try:
        log, st = _build(geom, base)
    finally:
        os.remove(geom)

    assert st["verify"]["status"] == "ok", st["verify"]
    assert st["built"]["walls"] == 3 and st["built"]["columns"] == 4
    assert st["built"]["slabs"] == 1 and st["built"]["floors"] == 1
    assert st["invalid_shapes"] == 0, st["invalid_shapes"]
    # 층 고아 = IFC 에서 조용히 누락되는 객체. 0 이어야 한다(V001/V102 의 근원).
    assert st["floor_orphans"] == 0 and st["floor_dups"] == 0, st
    assert st["openings_void"] == 1, st["openings_void"]
    # 마커를 찍었으면 그 경로에 파일이 실제로 있어야 한다.
    assert "FCSTD_DST:" in log and "IFC_DST:" in log, log[-400:]
    assert os.path.exists(base + ".FCStd") and os.path.exists(base + ".ifc")
    # QA 속성이 IFC 로 나가는지. 형상만 맞고 이게 빠지면 뷰어(Bonsai) 검수가
    # 통째로 무의미해지는데, 형상 검사는 전부 통과하므로 따로 봐야 한다.
    import verify as V
    n_pset = V.count_ifc_psets(base + ".ifc")
    assert n_pset == st["built"]["walls"] + st["built"]["columns"] + st["built"]["slabs"],         f"Pset_MEPParser {n_pset}개 (객체 수와 불일치)"
    with open(base + ".ifc", encoding="utf-8", errors="ignore") as f:
        ifc = f.read()
    assert "'EID'" in ifc and "'Layer'" in ifc, "EID/Layer 가 IFC 에 없다"


def test_gate_withholds_output_when_a_record_has_no_floor():
    """게이트는 마커를 '출력하지 않는 것' 으로 fail-closed 를 만든다.
    소비자(GUI/MCP)는 마커가 있어야만 파일을 옮기므로 이것만으로 충분하다."""
    if not _skip_if_no_freecad():
        return
    import dxf_parser as dp
    import contextlib
    import io
    rules = dp.load_layer_map(os.path.join(ROOT, "layer_map.csv"))
    with contextlib.redirect_stdout(io.StringIO()):
        data = dp.parse(os.path.join(ROOT, "sample_plan.dxf"), rules, [])
    data["elements"]["wall"][0]["z_base"] = 99999.0      # 어느 층에도 안 맞는 z
    geom = os.path.join(ROOT, "_t_bad.json")
    with open(geom, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    base = os.path.join(tempfile.mkdtemp(prefix="mepgate_"), "out")
    try:
        log, st = _build(geom, base)
    finally:
        os.remove(geom)

    assert "BUILD_FAILED:" in log, log[-400:]
    assert "FCSTD_DST:" not in log, "게이트가 실패했는데 마커가 나갔다"
    assert not os.path.exists(base + ".FCStd"), "산출물이 나갔다"
    assert st["verify"]["status"] == "failed", st["verify"]
    assert any(f["id"] == "V102" for f in st["verify"]["findings"]), st["verify"]


def test_mep_gets_real_ifc_types_not_proxies():
    """MEP 를 Part::Feature 로만 만들면 IFC 에서 전부 IfcBuildingElementProxy 가 된다.

    형상은 맞지만 뷰어가 배관인지 덕트인지 모르니 시스템 필터도, 카테고리별 물량도,
    의미 있는 간섭 리포트도 안 나온다. MEP 가 이 프로젝트의 차별점인데 정작 IFC 에서
    정체불명이었다(실측: 지하3층 MEP 6개 전부 Proxy)."""
    if not _skip_if_no_freecad():
        return
    import contextlib
    import io
    import dxf_parser as dp
    rules = dp.load_layer_map(os.path.join(ROOT, "layer_map.csv"))
    blocks = dp.load_layer_map(os.path.join(ROOT, "block_map.csv"))
    with contextlib.redirect_stdout(io.StringIO()):
        data = dp.parse(os.path.join(ROOT, "sample_mep.dxf"), rules, blocks)
    geom = os.path.join(ROOT, "_t_mep.json")
    with open(geom, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    base = os.path.join(tempfile.mkdtemp(prefix="mepifc_"), "out")
    try:
        _log, st = _build(geom, base)
    finally:
        os.remove(geom)
    assert st["built"]["mep"] == 6, st["built"]
    with open(base + ".ifc", encoding="utf-8", errors="ignore") as f:
        ifc = f.read()
    for want in ("IFCPIPESEGMENT", "IFCDUCTSEGMENT", "IFCCABLECARRIERSEGMENT",
                 "IFCDISTRIBUTIONELEMENT"):
        assert want in ifc, f"{want} 가 IFC 에 없다"
    assert "IFCBUILDINGELEMENTPROXY" not in ifc, "MEP 가 아직 Proxy 로 나간다"
