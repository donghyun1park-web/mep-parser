# -*- coding: utf-8 -*-
"""verify.py 검사별 단위테스트.

원칙: 검사 하나당 테스트 둘 —
  (1) 고의로 망가뜨린 데이터에서 **발동하는가**
  (2) 정상 데이터에서 **오탐하지 않는가**
발동 안 하는 검사는 없느니만 못하다. 이 저장소에서 가치가 가장 높은 테스트 셋.
"""
import copy
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import geom_contract as GC
import verify as V


def catalog_build(data, stats, ifc_path=None):
    """Isolated catalog tests intentionally use partial stats and STEP fragments."""
    return V.verify_build(data, stats, ifc_path, stage="catalog")


def clean():
    """정상 최소 모델: 벽·기둥 0~3000, 슬래브 2800~3000 (연속).

    floors 는 실제 사용법대로 선언한다 — _at_floor 는 datum 과 무관하게 z_base 로
    매칭하므로, 슬래브(z_base=상단 3000)를 담을 층 선언이 z=3000 에 있어야 한다.
    """
    return {
        "contract": GC.contract_block(),
        "params": {"wall": {"width": 200.0, "height": 2800.0},
                   "column": {"height": 3000.0}, "slab": {"thickness": 200.0}},
        "floors": [{"z": 0.0, "label": "L1"}, {"z": 3000.0, "label": "L1_슬래브"}],
        "elements": {
            "wall": [{"kind": "polyline", "closed": False,
                      "points": [[0, 0], [5000, 0]], "centerline": [[0, 0], [5000, 0]],
                      "z_base": 0.0, "overrides": {"height": 3000.0}}],
            "column": [{"kind": "polyline", "closed": True,
                        "points": [[0, 0], [400, 0], [400, 400], [0, 400]],
                        "z_base": 0.0, "overrides": {"height": 3000.0}}],
            "slab": [{"kind": "polyline", "closed": True,
                      "points": [[0, 0], [5000, 0], [5000, 5000], [0, 5000]],
                      "z_base": 3000.0, "overrides": {"thickness": 200.0}}],
        },
        "warnings": [],
    }


def ids(rep):
    return {f.id for f in rep.findings}


# ── 기준선: 정상 데이터는 조용해야 한다 ────────────────────────────────────
def test_clean_is_silent():
    rep = V.verify_geometry(clean())
    assert not rep.failed, f"정상 데이터에서 ERROR 발생: {rep.text()}"
    assert ids(rep) == set(), f"정상 데이터에서 오탐: {ids(rep)}"


# ── V001 층 미매칭 ─────────────────────────────────────────────────────────
def test_V001_fires_on_orphan():
    d = clean()
    d["elements"]["slab"].append({"kind": "polyline", "closed": True,
                                  "points": [[0, 0], [100, 0], [100, 100], [0, 100]],
                                  "z_base": 99999.0, "overrides": {"thickness": 200.0}})
    rep = V.verify_geometry(d)
    assert "V001" in ids(rep), "층에 안 붙는 요소를 못 잡았다(IFC 누락의 직접 원인)"
    assert rep.failed


def test_V001_silent_when_all_matched():
    assert "V001" not in ids(V.verify_geometry(clean()))


# ── V002 층 중복 매칭 ──────────────────────────────────────────────────────
def test_V002_fires_on_duplicate_floor():
    d = clean()
    d["floors"] = [{"z": 0.0, "label": "A"}, {"z": 50.0, "label": "B"}]   # 100mm tol 안에 둘
    rep = V.verify_geometry(d)
    assert "V002" in ids(rep), "두 층에 동시 매칭되는 요소를 못 잡았다"


def test_V002_silent_on_separated_floors():
    d = clean()
    d["floors"] = [{"z": 0.0, "label": "A"}, {"z": 3000.0, "label": "B"}]
    assert "V002" not in ids(V.verify_geometry(d))


# ── V003 계약 ──────────────────────────────────────────────────────────────
def test_V003_fires_on_unknown_bucket():
    d = clean()
    d["elements"]["bogus"] = [{"kind": "polyline", "points": [[0, 0], [1, 1]]}]
    assert "V003" in ids(V.verify_geometry(d))


def test_V003_fires_on_ignore_bucket():
    d = clean()
    d["elements"]["ignore"] = [{"kind": "polyline", "points": [[0, 0], [1, 1]]}]
    assert "V003" in ids(V.verify_geometry(d)), "ignore 버킷이 JSON 에 실린 것을 못 잡았다"


def test_V003_fires_on_version_mismatch():
    d = clean()
    d["contract"] = {"version": 999, "z_datum": {}}
    assert "V003" in ids(V.verify_geometry(d))


# ── V004 층간 연속성 ───────────────────────────────────────────────────────
def test_V004_fires_on_gap():
    """PIT 벽 상단과 1F 슬래브 하단 사이 1,000mm 공백 — 실제로 납품된 사고."""
    d = clean()
    d["floors"].append({"z": 9000.0, "label": "L2"})
    d["elements"]["slab"].append({"kind": "polyline", "closed": True,
                                  "points": [[0, 0], [5000, 0], [5000, 5000], [0, 5000]],
                                  "z_base": 9000.0, "overrides": {"thickness": 200.0}})
    rep = V.verify_geometry(d)
    assert "V004" in ids(rep), "층간 공백을 못 잡았다"
    g = [f for f in rep.findings if f.id == "V004"][0].payload["gaps"][0]
    assert abs(g["gap_mm"] - 5800.0) < 1.0, f"공백 크기 오산: {g}"


def test_V004_silent_when_contiguous():
    assert "V004" not in ids(V.verify_geometry(clean()))


# ── V005 퇴화 형상 ─────────────────────────────────────────────────────────
def test_V005_fires_on_runaway_coordinate():
    """좌표 -27,250,174mm 짜리 벽이 isValid()=True 로 통과한 사고의 방어선."""
    d = clean()
    d["elements"]["wall"].append({"kind": "polyline", "closed": False,
                                  "points": [[0, 0], [0, -27250174]],
                                  "centerline": [[0, 0], [0, -27250174]],
                                  "z_base": 0.0, "overrides": {"height": 3000.0}})
    assert "V005" in ids(V.verify_geometry(d)), "좌표 이상치를 못 잡았다"


def test_V005_fires_on_zero_length():
    d = clean()
    d["elements"]["wall"].append({"kind": "polyline", "closed": False,
                                  "points": [[10, 10], [10, 10]],
                                  "centerline": [[10, 10], [10, 10]],
                                  "z_base": 0.0, "overrides": {"height": 3000.0}})
    assert "V005" in ids(V.verify_geometry(d))


def test_V005_silent_on_clean():
    assert "V005" not in ids(V.verify_geometry(clean()))


# ── V006 되꺾임 ────────────────────────────────────────────────────────────
def test_V006_fires_on_foldback():
    d = clean()
    d["elements"]["wall"].append({"kind": "polyline", "closed": False,
                                  "points": [[0, 0], [0, 7700], [0, 1600]],
                                  "centerline": [[0, 0], [0, 7700], [0, 1600]],
                                  "z_base": 0.0, "overrides": {"height": 3000.0}})
    assert "V006" in ids(V.verify_geometry(d)), "180° 되꺾인 baseline 을 못 잡았다"


def test_V006_silent_on_corner():
    """직각 코너는 되꺾임이 아니다 — 오탐하면 실무 도면 전체가 걸린다."""
    d = clean()
    d["elements"]["wall"].append({"kind": "polyline", "closed": False,
                                  "points": [[0, 0], [0, 5000], [5000, 5000]],
                                  "centerline": [[0, 0], [0, 5000], [5000, 5000]],
                                  "z_base": 0.0, "overrides": {"height": 3000.0}})
    assert "V006" not in ids(V.verify_geometry(d))


# ── V007 / V008 ────────────────────────────────────────────────────────────
def test_V007_fires_on_needs_review():
    d = clean()
    d["elements"]["wall"][0]["needs_review"] = True
    assert "V007" in ids(V.verify_geometry(d))


def test_V008_fires_on_low_coverage():
    d = clean()
    d["qa"] = {"face_coverage_pct": 42.0}
    assert "V008" in ids(V.verify_geometry(d))


def test_V008_silent_on_good_coverage():
    d = clean()
    d["qa"] = {"face_coverage_pct": 99.0}
    assert "V008" not in ids(V.verify_geometry(d))


# ── severity 정책 ──────────────────────────────────────────────────────────
def test_policy_can_downgrade():
    """severity 조정은 가능해야 하지만, 로그가 아니라 설정에 남아야 한다."""
    d = clean()
    d["elements"]["slab"].append({"kind": "polyline", "closed": True,
                                  "points": [[0, 0], [100, 0], [100, 100], [0, 100]],
                                  "z_base": 99999.0, "overrides": {"thickness": 200.0}})
    assert V.verify_geometry(d).failed
    d["verify"] = {"severity": {"V001": "warn", "V004": "warn"}}
    rep = V.verify_geometry(d)
    assert not rep.failed and "V001" in ids(rep), "정책 하향이 반영되지 않았다"


# ── 빌드 후 검사 ───────────────────────────────────────────────────────────
def test_V101_fires_when_ifc_missing_category(tmp_path=None):
    import tempfile
    d = clean()
    ifc = os.path.join(tempfile.gettempdir(), "_vtest.ifc")
    with open(ifc, "w", encoding="utf-8") as f:
        f.write("ISO-10303-21;\nDATA;\n#1=IFCWALL('x');\n#2=IFCCOLUMN('y');\nENDSEC;\n")
    st = {"intent": {"wall": 1, "column": 1, "beam": 135}}
    rep = catalog_build(d, st, ifc)
    assert "V101" in ids(rep), "IFC 에서 보 135개가 통째로 빠진 것을 못 잡았다"
    assert rep.failed
    os.remove(ifc)


def test_V101_silent_when_counts_match():
    import tempfile
    d = clean()
    ifc = os.path.join(tempfile.gettempdir(), "_vtest2.ifc")
    with open(ifc, "w", encoding="utf-8") as f:
        f.write("ISO-10303-21;\nDATA;\n#1=IFCWALL('x');\n#2=IFCCOLUMN('y');\nENDSEC;\n")
    rep = catalog_build(d, {"intent": {"wall": 1, "column": 1}}, ifc)
    assert "V101" not in ids(rep)
    os.remove(ifc)


def test_V102_fires_on_orphan_objects():
    rep = catalog_build(clean(), {"floor_orphans": 135, "floor_dups": 0})
    assert "V102" in ids(rep) and rep.failed


def test_V103_fires_on_invalid_shapes():
    rep = catalog_build(clean(), {"invalid_shapes": 3})
    assert "V103" in ids(rep) and rep.failed


def test_V104_fires_on_bbox_blowup():
    rep = catalog_build(clean(), {"bbox": [0, 0, 0, 27250174, 5000, 3000]})
    assert "V104" in ids(rep), "폭주 솔리드로 bbox 가 터진 것을 못 잡았다"


def test_V102_V103_silent_when_clean():
    rep = catalog_build(clean(), {"floor_orphans": 0, "floor_dups": 0,
                                   "invalid_shapes": 0, "bbox": [0, 0, 0, 5000, 5000, 3000]})
    assert not rep.failed and ids(rep) == set()


# ── 카탈로그 무결성 ────────────────────────────────────────────────────────
def test_every_check_has_catalog_entry():
    """새 검사를 추가하고 카탈로그 등록을 잊는 것을 막는다(스킬 문서가 여기서 생성된다)."""
    import re
    src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "verify.py"), encoding="utf-8").read()
    used = set(re.findall(r'Finding\("(V\d+)"', src))
    assert used <= set(V.CHECKS), f"카탈로그에 없는 검사 id: {used - set(V.CHECKS)}"


# ── V105: IFC 에 QA 속성이 실렸는가 ────────────────────────────────────────
def _ifc_with(psets, path=None):
    import tempfile
    p = path or os.path.join(tempfile.gettempdir(), "_v105.ifc")
    body = "\n".join(
        f"#{100+i}=IFCPROPERTYSET('0abc{i}',#5,'Pset_MEPParser',$,(#9));"
        for i in range(psets))
    with open(p, "w", encoding="utf-8") as f:
        f.write("ISO-10303-21;\nDATA;\n" + body + "\nENDSEC;\nEND-ISO-10303-21;\n")
    return p


def test_v105_fires_when_no_qa_properties_reached_the_ifc():
    """형상만 맞고 속성이 빠지면 뷰어 검수가 통째로 무의미해진다.
    실제로 오래 그 상태였는데 형상 검사가 전부 통과해서 아무도 몰랐다."""
    rep = catalog_build({"elements": {}},
                              {"built": {"walls": 3, "columns": 4, "slabs": 1}},
                              _ifc_with(0))
    assert any(f.id == "V105" for f in rep.findings), rep.text()


def test_v105_silent_when_properties_are_present():
    rep = catalog_build({"elements": {}},
                              {"built": {"walls": 3, "columns": 4, "slabs": 1}},
                              _ifc_with(8))
    assert not any(f.id == "V105" for f in rep.findings), rep.text()


def test_count_ifc_psets_counts_only_our_pset():
    p = _ifc_with(3)
    assert V.count_ifc_psets(p) == 3
    assert V.count_ifc_psets(p, "Pset_Other") == 0


# ── V106 개구부 세 갈래 ────────────────────────────────────────────────────
# 종전에는 "호스트가 없거나 실패하면 error" 한 줄이었다. 그래서 붙일 벽을 못 찾은
# 개구부 하나만 있어도 건물 전체가 납품 불가가 됐다(실측 지하3층: 15개).
def _pre_export(opening_results):
    from artifact_validation import input_hash
    data = clean()
    st = {"intent": {}, "built": {"walls": 1}, "floor_orphans": 0, "floor_dups": 0,
          "invalid_shapes": 0, "bbox": [0, 0, 0, 5000, 5000, 3000],
          "unbuilt": {}, "opening_results": opening_results,
          "provenance": {"run_id": "t", "input_sha256": input_hash(data)}}
    return V.verify_build(data, st, None, stage="pre_export")


def _sev(rep, check):
    return sorted(f.severity for f in rep.findings if f.id == check)


def test_V106_opening_without_a_host_warns_but_does_not_block():
    rep = _pre_export([{"eid": "o:1", "requested_hosts": [], "cut_host_eids": []}])
    assert not rep.failed, rep.text()
    assert _sev(rep, "V106") == ["warn"], rep.text()


def test_V106_opening_that_cut_nothing_is_an_error():
    rep = _pre_export([{"eid": "o:1", "requested_hosts": [3], "cut_host_eids": []}])
    assert _sev(rep, "V106") == ["error"], rep.text()


def test_V106_partial_miss_warns():
    rep = _pre_export([{"eid": "o:1", "requested_hosts": [3, 4], "cut_host_eids": ["w:a"],
                        "failed_hosts": [{"wall_index": 4}]}])
    assert not rep.failed, rep.text()
    assert _sev(rep, "V106") == ["warn"], rep.text()


def test_V106_already_void_host_counts_as_cut():
    """겹쳐 그린 개구부 — 먼저 온 커터가 그 자리를 비웠으면 void 는 존재한다."""
    rep = _pre_export([{"eid": "o:1", "requested_hosts": [3], "cut_host_eids": ["w:a"],
                        "already_void": [{"host_name": "Wall_1", "host_eids": ["w:a"]}]}])
    assert not rep.failed and _sev(rep, "V106") == [], rep.text()


def test_V106_splits_host_less_openings_by_reason():
    """'고칠 것 없음'과 '레이어 매핑이 틀림'이 한 줄로 묶이면 같은 말로 보인다."""
    data = clean()
    data["elements"]["opening"] = [
        {"eid": "o:gap", "no_host_reason": "wall_open_at_this_span", "no_host_gap_mm": 110.0},
        {"eid": "o:far", "no_host_reason": "no_wall_on_this_line", "no_host_dist_mm": 2495.0},
    ]
    from artifact_validation import input_hash
    st = {"intent": {}, "built": {"walls": 1}, "floor_orphans": 0, "floor_dups": 0,
          "invalid_shapes": 0, "bbox": [0, 0, 0, 5000, 5000, 3000], "unbuilt": {},
          "opening_results": [{"eid": "o:gap", "requested_hosts": [], "cut_host_eids": []},
                              {"eid": "o:far", "requested_hosts": [], "cut_host_eids": []}],
          "provenance": {"run_id": "t", "input_sha256": input_hash(data)}}
    rep = V.verify_build(data, st, None, stage="pre_export")
    assert not rep.failed, rep.text()
    msgs = {f.message: f.payload["eids"] for f in rep.findings if f.id == "V106"}
    assert len(msgs) == 2, rep.text()
    assert [v for k, v in msgs.items() if "already left the wall open" in k] == [["o:gap"]], msgs
    assert [v for k, v in msgs.items() if "layer mapping" in k] == [["o:far"]], msgs


def test_V106_host_less_openings_are_reported_as_one_line():
    """개구부마다 하나씩 올리면 보고서가 그걸로 덮인다(실측 15건)."""
    rep = _pre_export([{"eid": f"o:{i}", "requested_hosts": [], "cut_host_eids": []}
                       for i in range(15)])
    found = [f for f in rep.findings if f.id == "V106"]
    assert len(found) == 1 and found[0].payload["count"] == 15, rep.text()


def test_V106_catches_degenerate_mep_solids():
    """형상이 **유효한데** 납작한 경우 — isValid()·V103·V107 은 전부 통과한다.

    실측: `App.Vector.normalize()` 가 제자리에서 방향벡터를 바꿔(3000 → 1.0)
    `_rect_solid` 의 extrude 길이가 1mm 가 됐다. 덕트 45개가 단면 150×1 짜리
    종잇장이 되어 부피가 있어야 할 값의 0.05% 였는데, 검사는 전부 통과하고
    뷰어에서만 안 보였다. 부피로 재지 않으면 이 부류는 안 잡힌다.
    """
    rep = _pre_export([])
    assert not rep.failed, rep.text()          # 기준선: MEP 없으면 조용하다

    from artifact_validation import input_hash
    data = clean()
    st = {"intent": {}, "built": {"mep": 45}, "floor_orphans": 0, "floor_dups": 0,
          "invalid_shapes": 0, "bbox": [0, 0, 0, 5000, 5000, 3000], "unbuilt": {},
          "opening_results": [],
          "mep_volume": {"expected_mm3": 2.132e9, "built_mm3": 0.001e9},
          "provenance": {"run_id": "t", "input_sha256": input_hash(data)}}
    bad = V.verify_build(data, st, None, stage="pre_export")
    hit = [f for f in bad.findings if f.id == "V106" and f.severity == "error"]
    assert hit and "degenerate" in hit[0].message, bad.text()
    assert hit[0].payload["ratio"] < 0.01, hit[0].payload

    st["mep_volume"] = {"expected_mm3": 2.132e9, "built_mm3": 2.130e9}   # 마이터 오차
    ok = V.verify_build(data, st, None, stage="pre_export")
    assert not [f for f in ok.findings if f.id == "V106" and f.severity == "error"], ok.text()
