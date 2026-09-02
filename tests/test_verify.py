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
    rep = V.verify_build(d, st, ifc)
    assert "V101" in ids(rep), "IFC 에서 보 135개가 통째로 빠진 것을 못 잡았다"
    assert rep.failed
    os.remove(ifc)


def test_V101_silent_when_counts_match():
    import tempfile
    d = clean()
    ifc = os.path.join(tempfile.gettempdir(), "_vtest2.ifc")
    with open(ifc, "w", encoding="utf-8") as f:
        f.write("ISO-10303-21;\nDATA;\n#1=IFCWALL('x');\n#2=IFCCOLUMN('y');\nENDSEC;\n")
    rep = V.verify_build(d, {"intent": {"wall": 1, "column": 1}}, ifc)
    assert "V101" not in ids(rep)
    os.remove(ifc)


def test_V102_fires_on_orphan_objects():
    rep = V.verify_build(clean(), {"floor_orphans": 135, "floor_dups": 0})
    assert "V102" in ids(rep) and rep.failed


def test_V103_fires_on_invalid_shapes():
    rep = V.verify_build(clean(), {"invalid_shapes": 3})
    assert "V103" in ids(rep) and rep.failed


def test_V104_fires_on_bbox_blowup():
    rep = V.verify_build(clean(), {"bbox": [0, 0, 0, 27250174, 5000, 3000]})
    assert "V104" in ids(rep), "폭주 솔리드로 bbox 가 터진 것을 못 잡았다"


def test_V102_V103_silent_when_clean():
    rep = V.verify_build(clean(), {"floor_orphans": 0, "floor_dups": 0,
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
