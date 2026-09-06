# -*- coding: utf-8 -*-
"""공정표(CSV) → IFC 공정(IfcWorkSchedule/IfcTask) + 층별 부재 연결.

4D 시공 애니메이션은 **Bonsai(Blender)가 이미 한다.** 우리가 만들 것은 하나뿐이다:
"이 공정이 이 층의 부재들이다" 라는 연결. Bonsai 도 ifcopenshell 도 그건 모른다.

    python ifc_4d.py out_model.ifc schedule.csv [-o out_4d.ifc]

schedule.csv (헤더 필수):
    task,level,start,finish
    지하층 기둥·벽체 골조,지하1층,2026-11-01,2026-11-20
    1층 바닥 골조,1층,2026-11-21,2026-12-10

  · level : IFC 층 이름(IfcBuildingStorey.Name). 비우면 건물 전체.
            우리 빌더가 stack.json 의 label 로 층을 만들므로 그 이름을 쓰면 된다.
  · 날짜  : YYYY-MM-DD.

이후 Blender > Bonsai > File > Import > IFC → Sequence 패널에서
"Visualise Work Schedule Date Range" 로 4D 가 돈다.

주의: 이 스크립트는 형상을 만들지 않는다. 이미 만들어진 .ifc 에 공정만 얹는다.
"""
import argparse
import csv
import datetime
import os
import sys
from collections import OrderedDict

try:
    import ifcopenshell
    import ifcopenshell.api.sequence as seq
except ImportError:                       # 이 저장소의 필수 의존성이 아니다
    raise SystemExit("ifcopenshell 이 필요하다: pip install ifcopenshell\n"
                     "  (Blender/Bonsai 안에는 이미 들어 있다 — 거기서 실행해도 된다)")


def read_schedule(path):
    """CSV → [(task, level, start, finish)]. 순서가 곧 공정 순서다."""
    rows = []
    with open(path, encoding="utf-8-sig") as f:
        for i, r in enumerate(csv.DictReader(f), start=2):
            name = (r.get("task") or "").strip()
            if not name:
                continue
            try:
                s = datetime.date.fromisoformat((r.get("start") or "").strip())
                e = datetime.date.fromisoformat((r.get("finish") or "").strip())
            except ValueError:
                raise SystemExit(
                    f"{os.path.basename(path)} {i}행 '{name}': 날짜가 YYYY-MM-DD 가 아니다 "
                    f"({r.get('start')!r} ~ {r.get('finish')!r}). "
                    "엑셀 공정표에서 #REF! 로 깨진 칸이 흔하다 — 먼저 고칠 것")
            if e < s:
                raise SystemExit(f"{name}: 종료가 시작보다 빠르다 ({s} ~ {e})")
            rows.append((name, (r.get("level") or "").strip(), s, e))
    if not rows:
        raise SystemExit(f"{path}: 공정 행이 없다")
    return rows


def products_by_storey(f):
    """{층이름: [부재…]}. IFC 표준 공간 포함관계를 쓴다(우리 Level 속성보다 확실)."""
    out = OrderedDict()
    for st in f.by_type("IfcBuildingStorey"):
        items = []
        for rel in getattr(st, "ContainsElements", []) or []:
            items.extend(rel.RelatedElements)
        out[st.Name or f"Storey_{st.id()}"] = items
    return out


def build(ifc_path, csv_path, out_path, schedule_name="시공 공정"):
    f = ifcopenshell.open(ifc_path)
    rows = read_schedule(csv_path)
    by_storey = products_by_storey(f)
    if not by_storey:
        print("[warn] IfcBuildingStorey 가 없다 — 층 연결 없이 공정만 만든다")

    ws = seq.add_work_schedule(f, name=schedule_name, predefined_type="PLANNED")
    n_task = n_link = 0
    unknown = []
    for name, level, s, e in rows:
        t = seq.add_task(f, work_schedule=ws, name=name,
                         identification=str(n_task + 1), predefined_type="CONSTRUCTION")
        tt = seq.add_task_time(f, task=t)
        seq.edit_task_time(f, task_time=tt, attributes={
            "ScheduleStart": s, "ScheduleFinish": e,
            # 달력일. Bonsai 가 기간 막대를 그릴 때 쓴다.
            "ScheduleDuration": f"P{(e - s).days + 1}D",
        })
        n_task += 1

        if not level:
            targets = [p for items in by_storey.values() for p in items]
        elif level in by_storey:
            targets = by_storey[level]
        else:
            unknown.append(level)
            targets = []
        for p in targets:
            seq.assign_product(f, relating_product=p, related_object=t)
        n_link += len(targets)
        print(f"  {name:28s} {s}~{e}  층={level or '(전체)':8s} 부재 {len(targets)}개")

    if unknown:
        print(f"  [!] IFC 에 없는 층 이름 {sorted(set(unknown))} — "
              f"실제 층: {list(by_storey)}")
    f.write(out_path)
    print(f"\n공정 {n_task}개, 부재 연결 {n_link}건 → {out_path}")
    print("  Blender > Bonsai > Import IFC → Sequence 패널 > "
          "Visualise Work Schedule Date Range")
    return {"tasks": n_task, "links": n_link, "storeys": list(by_storey),
            "unknown_levels": sorted(set(unknown))}


def main():
    ap = argparse.ArgumentParser(description="IFC 에 공정(IfcTask)을 얹어 4D 준비")
    ap.add_argument("ifc")
    ap.add_argument("csv")
    ap.add_argument("-o", "--out", default=None)
    ap.add_argument("--name", default="시공 공정")
    a = ap.parse_args()
    out = a.out or os.path.splitext(a.ifc)[0] + "_4d.ifc"
    build(a.ifc, a.csv, out, a.name)


if __name__ == "__main__":
    main()
