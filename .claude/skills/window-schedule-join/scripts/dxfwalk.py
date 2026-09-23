"""스크립트 공용 — 저장소 찾기 · DXF 글자/치수 수집(중첩 블록은 WCS 로) · 합성 도면용 작은 도우미.

저장소는 이 파일에서 네 단계 위(skills/<스킬>/scripts/ → 저장소)다. 환경변수 MEP_PARSER_REPO 가 이긴다.
"""
import collections
import math
import os
import re
import sys
from pathlib import Path

REPO = Path(os.environ.get("MEP_PARSER_REPO") or Path(__file__).resolve().parents[4])
if not (REPO / "dxf_parser.py").exists():
    sys.exit(f"저장소를 못 찾았다: {REPO} — MEP_PARSER_REPO 로 지정할 것")
sys.path.insert(0, str(REPO))

import ezdxf  # noqa: E402
from ezdxf import bbox  # noqa: E402
from ezdxf.math import Vec3  # noqa: E402

tail = lambda s: (s or "").split("$0$")[-1]        # xref bind 이름 '<xref>$0$<원래 이름>' → 원래 이름

# 엔티티 하나가 읽기에 실패하면 건너뛰되 **센다** — 빠진 F.L 글자·치수는 조용히 창대 None 이 된다.
DROPS = collections.Counter()


def drops():
    return f"읽기 실패 {dict(DROPS)}" if DROPS else "읽기 실패 0"


def out_path(p):
    """출력은 저장소 밖에만 — 블록·xref 이름이 곧 현장 식별자다(저장소는 공개)."""
    q = Path(p).resolve()
    if q == REPO or REPO in q.parents:
        sys.exit(f"저장소 안에 쓰지 않는다: {q} — <작업 폴더>/decl/ 처럼 저장소 밖 경로를 준다")
    return str(q)


def text_of(e):
    t = e.dxftype()
    if t == "MTEXT":
        return e.plain_text()
    if t in ("TEXT", "ATTRIB"):
        return e.dxf.text
    return None


def anchor(e):
    """글자의 진짜 자리. TEXT 는 정렬점(중앙정렬이면 insert 는 글자 왼쪽 끝이다), MTEXT 는 부착점과 폭으로 칸 가운데."""
    t = e.dxftype()
    if t in ("TEXT", "ATTRIB"):
        if (e.dxf.get("halign", 0) or e.dxf.get("valign", 0)) and e.dxf.hasattr("align_point"):
            p = e.dxf.align_point
        else:
            p = e.dxf.insert
        return p.x, p.y
    p = e.dxf.insert
    w = e.dxf.get("width", 0) or 0
    col = (e.dxf.get("attachment_point", 1) - 1) % 3          # 0 왼쪽 · 1 가운데 · 2 오른쪽
    return p.x + (w / 2 if col == 0 else -w / 2 if col == 2 else 0), p.y


def collect(ents, max_depth=3):
    """모델공간(또는 엔티티 목록) + 중첩 블록의 TEXT·MTEXT·ATTRIB·DIMENSION·INSERT 를 WCS 로 한 목록에.
    중첩은 블록 **정의**를 행렬로 걷는다 — virtual_entities() 는 어떤 블록에서 중간에 예외로 멈추고
    (실측: 실명 라벨 블록 · 통심선 버블이 든 층 블록) 동적 블록의 복사 불가 글자를 건너뛴다.
    한 엔티티가 실패해도(프록시 그래픽 MULTILEADER 의 cp949 글자 등) 나머지는 계속 읽는다."""
    out = []

    def P(M, v):
        p = M.transform(Vec3(v)) if M is not None else Vec3(v)
        return p.x, p.y

    def walk(items, M, depth, src):
        for e in items:
            tp = e.dxftype()
            try:
                if tp in ("TEXT", "MTEXT"):
                    x, y = P(M, anchor(e))
                    out.append({"k": tp, "t": (text_of(e) or "").strip(), "x": x, "y": y, "l": e.dxf.layer,
                                "h": e.dxf.get("height" if tp == "TEXT" else "char_height"), "d": depth, "src": src})
                elif tp == "DIMENSION":
                    p2, p3 = P(M, e.dxf.defpoint2), P(M, e.dxf.defpoint3)
                    m = e.get_measurement()
                    if M is not None and isinstance(m, (int, float)):
                        m *= M.transform_direction((1, 0, 0)).magnitude
                    out.append({"k": "DIM", "t": e.dxf.get("text", ""), "m": m, "l": e.dxf.layer,
                                "p2": p2, "p3": p3, "d": depth, "src": src, "x": p2[0], "y": p2[1]})
                elif tp == "INSERT":
                    x, y = P(M, e.dxf.insert)
                    out.append({"k": "INSERT", "t": e.dxf.name, "x": x, "y": y, "l": e.dxf.layer, "d": depth,
                                "src": src, "attribs": {a.dxf.tag: (a.dxf.text or "").strip() for a in e.attribs}})
                    for a in e.attribs:
                        ax, ay = P(M, anchor(a))
                        out.append({"k": "ATTRIB", "t": (a.dxf.text or "").strip(), "x": ax, "y": ay, "l": a.dxf.layer,
                                    "tag": a.dxf.tag, "blk": e.dxf.name, "d": depth, "src": src})
            except Exception as ex:
                DROPS[(tp, type(ex).__name__)] += 1
            if tp == "INSERT" and depth < max_depth:     # 부모에서 무엇이 실패해도 안쪽 블록은 읽는다
                try:
                    blk = e.doc.blocks.get(e.dxf.name)
                    MM = e.matrix44() @ M if M is not None else e.matrix44()
                except Exception as ex:
                    DROPS[("INSERT-matrix", type(ex).__name__)] += 1
                    continue
                if blk is not None:
                    walk(blk, MM, depth + 1, e.dxf.name)

    walk(ents, None, 0, "msp")
    return out


def block_contents(doc, insert, max_depth=4, pts=True):
    """INSERT 의 블록 **정의**를 직접 걷는다(중첩 포함) → (WCS 점 목록, 글자 목록 [{t,l,x,y}]).
    virtual_entities() 는 동적 블록의 복사 불가 글자(문 폭 '0.7')를 건너뛰고, 어떤 블록에서는 중간에 죽는다
    (실측: 실명 라벨 블록에서 예외로 멈춰 라벨 일부만 나왔다) — 그래서 정의를 읽는다."""
    want_pts = pts
    pts, texts = [], []

    def put(e, M):
        x, y = anchor(e)
        p = M.transform(Vec3(x, y, 0)) if M is not None else Vec3(x, y, 0)
        texts.append({"t": (text_of(e) or "").strip(), "l": e.dxf.layer, "x": p.x, "y": p.y,
                      "h": e.dxf.get("char_height" if e.dxftype() == "MTEXT" else "height")})

    def walk(name, M, depth):
        blk = doc.blocks.get(name)
        if blk is None:
            return
        for e in blk:
            t = e.dxftype()
            try:
                if t == "INSERT":
                    for a in e.attribs:
                        put(a, M)
                    if depth < max_depth:
                        walk(e.dxf.name, e.matrix44() @ M, depth + 1)
                    continue
                if t in ("TEXT", "MTEXT"):
                    put(e, M)
                    continue
                if t == "ATTDEF" or not want_pts:  # 속성 틀은 값이 아니다 · 글자만 필요할 때
                    continue
                b = bbox.extents([e], fast=True)
                if b.has_data:
                    for cx, cy in ((b.extmin.x, b.extmin.y), (b.extmax.x, b.extmax.y),
                                   (b.extmin.x, b.extmax.y), (b.extmax.x, b.extmin.y)):
                        pts.append(M.transform(Vec3(cx, cy, 0)))
            except Exception as ex:
                DROPS[(t, type(ex).__name__)] += 1
                continue

    walk(insert.dxf.name, insert.matrix44(), 0)
    for a in insert.attribs:
        put(a, None)
    return pts, texts


def local_axes(insert):
    """INSERT 의 로컬 X·Y 방향(WCS 단위벡터). 미러·회전 블록도 같은 식으로 잰다."""
    m = insert.matrix44()
    out = []
    for d in ((1, 0, 0), (0, 1, 0)):
        v = m.transform_direction(d)
        n = math.hypot(v.x, v.y) or 1.0
        out.append((v.x / n, v.y / n))
    return out


def norm_room(s):
    """'침실-2'→'침실2', '침실1(안방)'→'침실1', 줄바꿈·공백 제거. 실명 비교 전용."""
    return re.sub(r"[\s\-]|\(.*?\)", "", s or "")


def new_doc():
    """합성 도면(selftest) — 저장소 규약대로 mm."""
    doc = ezdxf.new("R2010")
    doc.header["$INSUNITS"] = 4
    return doc
