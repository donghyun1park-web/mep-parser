# -*- coding: utf-8 -*-
"""ifc_4d 커버리지.

이 파일이 생기기 전까지 ifc_4d.py 는 **한 번도 실행된 적이 없었다.** 산출물이
Blender/Bonsai 에서만 확인되는 종류라 "돌긴 돌았다" 로 넘어가기 쉬운데, 조용히
틀리는 자리가 둘 있다:
  · 층 이름이 IFC 와 안 맞으면 부재 연결이 0건인 채로 성공처럼 끝난다
  · 날짜가 깨진 엑셀 공정표(#REF!)를 그대로 먹고 IfcTaskTime 이 비어 나간다

ifcopenshell 은 이 저장소의 필수 의존성이 아니다(Bonsai 안에 들어 있다) —
없으면 [skip] 을 찍고 건너뛴다.
"""
import datetime
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

try:
    import ifcopenshell
    from ifcopenshell.api import run as _api
except ImportError:
    ifcopenshell = None


def _skip():
    if ifcopenshell is not None:
        return False
    raise unittest.SkipTest('ifcopenshell 없음 — 4D 공정 검사 미실행')


def _mini_ifc(path):
    """층 2개, 각 층에 벽 하나. 형상은 없어도 공정 연결에는 상관없다."""
    f = ifcopenshell.file(schema="IFC4")
    _api("root.create_entity", f, ifc_class="IfcProject", name="T")
    for i, name in enumerate(("1F", "2F"), start=1):
        st = _api("root.create_entity", f, ifc_class="IfcBuildingStorey", name=name)
        w = _api("root.create_entity", f, ifc_class="IfcWall", name=f"W{i}")
        _api("spatial.assign_container", f, products=[w], relating_structure=st)
    f.write(path)
    return path


def _csv(path, body):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("task,level,start,finish\n" + body)
    return path


def _tmp(name):
    return os.path.join(tempfile.mkdtemp(prefix="mep4d_"), name)


def test_tasks_link_only_their_own_storey():
    """층 이름이 맞으면 그 층 부재만, 비우면 전체. 이 둘이 섞이면 공정이 무의미하다."""
    if _skip():
        return
    import ifc_4d
    ifc = _mini_ifc(_tmp("m.ifc"))
    csv = _csv(_tmp("s.csv"), "1층 골조,1F,2026-11-01,2026-11-20\n"
                              "마감,,2026-11-21,2026-11-30\n")
    out = _tmp("o.ifc")
    st = ifc_4d.build(ifc, csv, out)
    assert st == {"tasks": 2, "links": 3, "storeys": ["1F", "2F"],
                  "unknown_levels": []}, st

    f = ifcopenshell.open(out)
    assert len(f.by_type("IfcWorkSchedule")) == 1
    # 공정이 스케줄에 물려 있어야 Bonsai 의 Sequence 패널에 나온다.
    assert len(f.by_type("IfcRelAssignsToControl")) == 1
    by_name = {t.Name: t for t in f.by_type("IfcTask")}
    got = sorted(p.Name for r in f.by_type("IfcRelAssignsToProduct")
                 if by_name["1층 골조"] in r.RelatedObjects
                 for p in [r.RelatingProduct])
    assert got == ["W1"], got
    # 기간은 달력일 포함 계산(11/01~11/20 = 20일). 19일로 나오면 막대가 하루 짧다.
    assert by_name["1층 골조"].TaskTime.ScheduleDuration == "P20D"
    assert by_name["1층 골조"].TaskTime.ScheduleStart.startswith("2026-11-01")


def test_unknown_level_is_reported_not_silently_zero():
    """★ 오타 하나로 연결 0건인데 '공정 1개 생성' 만 찍고 끝나면 아무도 모른다."""
    if _skip():
        return
    import ifc_4d
    ifc = _mini_ifc(_tmp("m.ifc"))
    csv = _csv(_tmp("s.csv"), "지하 골조,B1,2026-11-01,2026-11-20\n")
    st = ifc_4d.build(ifc, csv, _tmp("o.ifc"))
    assert st["links"] == 0 and st["unknown_levels"] == ["B1"], st


def test_broken_dates_stop_the_build():
    """엑셀 공정표는 #REF!/빈칸으로 깨져 오는 게 흔하다 — 먹고 넘어가면 안 된다."""
    if _skip():
        return
    import ifc_4d
    ifc = _mini_ifc(_tmp("m.ifc"))
    for body, why in (("A,1F,#REF!,2026-11-20\n", "깨진 날짜"),
                      ("A,1F,2026-11-20,2026-11-01\n", "종료<시작"),
                      ("", "행 없음")):
        try:
            ifc_4d.build(ifc, _csv(_tmp("s.csv"), body), _tmp("o.ifc"))
        except SystemExit:
            continue
        raise AssertionError(f"{why} 인데 통과했다")


def test_schedule_rows_keep_csv_order():
    """공정 순서 = CSV 행 순서. Identification 이 뒤섞이면 간트가 뒤집힌다."""
    if _skip():
        return
    import ifc_4d
    rows = ifc_4d.read_schedule(_csv(_tmp("s.csv"),
                                     "나중,,2026-12-01,2026-12-02\n"
                                     "\n"                      # 빈 행은 건너뛴다
                                     "먼저,,2026-11-01,2026-11-02\n"))
    assert [r[0] for r in rows] == ["나중", "먼저"], rows
    assert rows[0][2] == datetime.date(2026, 12, 1)
