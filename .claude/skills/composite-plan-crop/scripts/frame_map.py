"""종합평면도(층마다 도곽 한 장이 X 로 나란히)의 도곽 지도 — 도곽마다 어느 층 레이어 그룹과 어느 블록 삽입이 들었는지.

    python frame_map.py <도면.dxf> [--frame-block REGEX] [-o frames.json]
    python frame_map.py --selftest

- 도곽 블록은 지정하지 않으면 '용지 모양(긴 변 20 m 이상, √2 ±2%)이고 여러 번 삽입됐고 서로 겹치지 않는 가장 큰 INSERT'
  로 고른다(후보를 전부 출력한다). 그런 게 없으면 종합평면도가 아니라고 멈춘다.
- 층 레이어 그룹 = xref bind 레이어 이름에서 '$0$' 앞부분('<층 xref>$0$A-WALL' → '<층 xref>').
- 소속은 부재 하나의 기준점(WCS 삽입점·시작점·중심)으로 본다 — bbox 는 한글 지시선 프록시에서 예외가 난다.
crop_floor.py 가 이 파일의 함수를 그대로 쓴다.
"""
import argparse
import collections
import json
import re
import sys
import time
from pathlib import Path

import ezdxf
from ezdxf import bbox

# 도곽 = 용지 모양 — drawing-set-intake 인벤토리와 같은 규칙(같은 상수). sheet_inventory 가 저장소를 sys.path 에 올린다.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'drawing-set-intake' / 'scripts'))
from sheet_inventory import FRAME_MIN_M, SHEET_RATIO, SHEET_TOL  # noqa: E402
from drawing_units import scale_to_mm  # noqa: E402

BIND = '$0$'
BIG = 50        # 이 이상 부재를 담은 블록만 출력한다(실측: 코어 70~93, 세대 470~740, 문·창 기호는 대개 수십 이하)


def load(path):
    t0 = time.time()
    doc = ezdxf.readfile(path)
    print(f'load {time.time() - t0:.1f}s  {path}', flush=True)
    return doc


class Extents:
    """bbox.extents 를 예외 안전하게 — 한글(cp949) 문자열을 담은 MULTILEADER 프록시 그래픽은 ezdxf 가 못 푼다."""

    def __init__(self):
        self.cache = bbox.Cache()
        self.failed = 0

    def __call__(self, e):
        try:
            b = bbox.extents([e], cache=self.cache, fast=True)
            return b if b.has_data else None
        except Exception:
            self.failed += 1
            return None


def anchor(e, ext):
    """부재 하나의 기준점(WCS). 블록은 삽입점 — 세대 블록 bbox 는 안의 지시선 때문에 실패한다(실측: 세대 블록 7개가 전부 빠졌다).
    원·호·글자·블록·경량 폴리선은 좌표를 OCS 로 저장한다 — 대칭(xscale<0) 삽입을 풀면 돌출 방향이 −Z 가 되어 x 가
    뒤집힌 값이 들어 있다. 그대로 쓰면 대칭 세대의 벽·문 호·실명이 도곽 밖(−x)으로 판정돼 조용히 지워진다."""
    t = e.dxftype()
    try:
        if t == 'LINE':
            return e.dxf.start
        if t == 'ELLIPSE':                      # LINE·ELLIPSE·MTEXT 는 WCS 로 저장한다
            return e.dxf.center
        if t == 'MTEXT':
            return e.dxf.insert
        if t in ('CIRCLE', 'ARC'):
            return e.ocs().to_wcs(e.dxf.center)
        if t in ('INSERT', 'TEXT'):
            return e.ocs().to_wcs(e.dxf.insert)
        if t == 'LWPOLYLINE':
            return next(iter(e.vertices_in_wcs()))
        if t == 'MULTILEADER':                  # bbox 는 cp949 프록시에서 실패한다(실측: 39개 — 창·문 부호가 흔히 이것)
            return e.context.base_point
    except Exception:
        pass
    b = ext(e)
    return b.center if b else None


def _overlap(a, b):
    # 이웃 도곽은 변을 공유한다 — bbox 부동소수 오차로 '맞닿음'이 겹침이 되지 않게 1% 여유(실측: 도곽 25장이 x 로 맞닿아 있다)
    iw = min(a[2], b[2]) - max(a[0], b[0])
    ih = min(a[3], b[3]) - max(a[1], b[1])
    return iw > 0.01 * min(a[2] - a[0], b[2] - b[0]) and ih > 0.01 * min(a[3] - a[1], b[3] - b[1])


def _any_overlap(boxes):
    return any(_overlap(boxes[i], boxes[j]) for i in range(len(boxes)) for j in range(i + 1, len(boxes)))


def _box(b):
    # 1e-3 로 반올림 — bbox 는 도곽 모서리를 -1.7e-08 처럼 준다(실측). 경계 판정은 inside() 의 eps 가 맡는다
    return tuple(round(v, 3) for v in (b.extmin.x, b.extmin.y, b.extmax.x, b.extmax.y))


def _paper(box, mm):
    """긴 변 FRAME_MIN_M 이상 + A 계열 용지 비율. 단층 도면의 반복 기호(실측: 실명 기호 4 m × 1.5 m)가 '도곽' 이 되지 않게."""
    w, h = sorted(((box[2] - box[0]) * mm / 1000, (box[3] - box[1]) * mm / 1000))
    return h >= FRAME_MIN_M and w > 0 and abs(h / w / SHEET_RATIO - 1) <= SHEET_TOL


def find_frames(msp, ext, frame_re=None):
    """(도곽 블록 이름 목록, [x0,y0,x1,y1] 을 x 순으로, 후보 목록). frame_re 가 없으면 자동 선택."""
    mm = scale_to_mm(msp.doc) or 1.0
    by_name = collections.defaultdict(list)
    for e in msp.query('INSERT'):
        by_name[e.dxf.name].append(e)
    if frame_re:
        names = [n for n in by_name if re.search(frame_re, n)]
        if not names:
            sys.exit(f'--frame-block {frame_re!r} 에 맞는 INSERT 가 없다')
        boxes = sorted(_box(b) for n in names for b in map(ext, by_name[n]) if b)
        if _any_overlap(boxes):
            print(f'[!] --frame-block {frame_re!r} 의 도곽끼리 겹친다 {sorted(names)} — 이름 하나로 좁혀라', flush=True)
        if not all(_paper(b, mm) for b in boxes):
            print(f'[!] --frame-block {frame_re!r} 가 용지 모양(긴 변 {FRAME_MIN_M:.0f} m 이상, √2 ±{SHEET_TOL:.0%})이 아니다', flush=True)
        return sorted(names), boxes, []
    # 자동: 2번 이상 삽입된 이름을 한 개 크기로 줄 세우고, 용지 모양이고 삽입끼리 겹치지 않는 첫 이름.
    # 코어 xref 는 여러 층 코어를 옆으로 담아 도곽보다 넓을 수 있지만 도곽마다 꽂혀 서로 겹친다 → 탈락.
    # 크기는 유효숫자 4자리로 — 같은 크기의 도곽 변형(실측: 25장 대 12장)이 부동소수 0.03 mm² 차로 이기지 않고 삽입 수로 가른다.
    cands = []
    for n, es in by_name.items():
        if len(es) < 2:
            continue
        b = ext(es[0])
        if b:
            cands.append((float(f'{b.size.x * b.size.y:.4g}'), len(es), n))
    cands.sort(reverse=True)
    report = []
    for _, cnt, n in cands[:8]:
        boxes = sorted(_box(b) for b in map(ext, by_name[n]) if b)
        paper = bool(boxes) and _paper(boxes[0], mm)
        disjoint = len(boxes) >= 2 and not _any_overlap(boxes)
        report.append({'name': n, 'count': cnt, 'size': [round(boxes[0][2] - boxes[0][0]), round(boxes[0][3] - boxes[0][1])]
                       if boxes else None, 'paper': paper, 'disjoint': disjoint, 'boxes': boxes})
    pick = next((r for r in report if r['paper'] and r['disjoint']), None)
    boxes = pick['boxes'] if pick else None
    for r in report:
        del r['boxes']
    if pick is None:
        sys.exit('도곽을 못 찾았다 — 종합평면도가 아니다(용지 모양 긴 변 '
                 f'{FRAME_MIN_M:.0f} m 이상·√2 ±{SHEET_TOL:.0%} 로 겹치지 않게 2번 이상 꽂힌 INSERT 없음). 한 층 도면이면 '
                 '이 스킬 없이 drawing-set-intake 로 파싱한다. 도곽이 맞는데 못 찾았으면 --frame-block. 후보: '
                 + json.dumps(report, ensure_ascii=False))
    return [pick['name']], boxes, report


def inside(box, p):
    """도곽 원점 쪽으로 eps 만큼 민 반열린 구간. xref(세대·코어)는 흔히 도곽 원점(왼쪽 아래 모서리)에 꽂힌다 —
    실측: 코어 xref 4개가 모두 도곽 원점에 y=-3e-10 오차로 꽂혀 있어 eps 없이는 도곽 bbox 의 반올림 오차에 따라
    들어왔다 빠졌다 했다(빠지면 코어 벽 S-CONC 111개 등 330개가 사라진다). 오른쪽 이웃 도곽의 원점은 이웃 것이다."""
    eps = 1e-6 * max(box[2] - box[0], box[3] - box[1])
    return box[0] - eps <= p[0] < box[2] - eps and box[1] - eps <= p[1] < box[3] - eps


def frame_of(frames, p):
    return next((i for i, box in enumerate(frames) if inside(box, p)), None)


def group_of(layer):
    return layer.split(BIND)[0] if BIND in layer else None


def role_of(layer):
    return layer.rsplit(BIND, 1)[-1]


def frame_map(doc, frame_re=None):
    msp = doc.modelspace()
    ext = Extents()
    names, frames, cands = find_frames(msp, ext, frame_re)
    groups = collections.defaultdict(collections.Counter)
    inserts = collections.defaultdict(collections.Counter)
    for e in msp:
        p = anchor(e, ext)
        i = frame_of(frames, p) if p is not None else None
        if i is None:
            continue
        g = group_of(e.dxf.layer)
        if g:
            groups[i][g] += 1
        if e.dxftype() == 'INSERT' and e.dxf.name not in names:
            inserts[i][e.dxf.name] += 1
    # 블록 정의 부재 수·역할 — 세대·코어 같은 '담는 블록'은 수백, 문·창 기호는 수십이다. 역할(레이어의 마지막 '$0$' 뒤)은
    # '$0$' 이름인데 벽·문·창을 담은 블록(실측: 층 xref 안의 복도 블록 — 레이어 0 에 꽂혀 A-WALL-DRY 80~86·A-DOOR 최대 22)을 드러낸다.
    info = {}
    for n in {n for c in inserts.values() for n in c}:
        blk = doc.blocks.get(n)
        if blk is not None:
            info[n] = (len(blk), collections.Counter(role_of(e.dxf.layer) for e in blk).most_common(4))
    return {'frame_block': names, 'frame_candidates': cands, 'extents_failed': ext.failed,
            'frames': [{'index': i, 'bbox': [round(v, 1) for v in f],
                        'groups': dict(groups[i].most_common()),
                        'inserts': {n: {'count': c, 'block_entities': info.get(n, (None,))[0],
                                        'roles': dict(info.get(n, (None, []))[1])}
                                    for n, c in inserts[i].most_common()}}
                       for i, f in enumerate(frames)]}


def print_map(m):
    print(f"frame block: {m['frame_block']}  frames={len(m['frames'])}  (bbox failed {m['extents_failed']})")
    for c in m['frame_candidates']:
        print(f"  candidate {c['name']!r} x{c['count']} size={c['size']} paper={c['paper']} disjoint={c['disjoint']}")
    big_roles = {}
    for f in m['frames']:
        x0, y0, x1, y1 = f['bbox']
        top = list(f['groups'].items())[:3]
        big = sorted(((n, v) for n, v in f['inserts'].items() if (v['block_entities'] or 0) >= BIG),
                     key=lambda kv: -kv[1]['block_entities'])
        print(f"[{f['index']:2d}] x[{x0:.0f},{x1:.0f}] y[{y0:.0f},{y1:.0f}]  groups={top}")
        if big:
            print('      big inserts: ' + '  '.join(f"{n}×{v['count']}({v['block_entities']})" for n, v in big))
        big_roles.update((n, v['roles']) for n, v in big)
    if not any(f['groups'] for f in m['frames']):
        print(f"[!] '{BIND}' 층 레이어 그룹이 없다 — xref 를 'insert' 방식으로 bind 했거나 직접 그린 종합평면도다. "
              "층은 도곽 제목 글자로 확인하고 crop_floor.py --frame-index 로 고른다", flush=True)
    if big_roles:
        print(f'block roles (정의 안 부재 {BIG}개 이상, 역할 상위 4):')
        for n in sorted(big_roles):
            print(f"  {n}: " + ', '.join(f'{r} {k}' for r, k in big_roles[n].items()))


def demo_doc(path):
    """합성 종합평면도(단위 m — ezdxf.new 기본): 도곽 2장, 층 그룹 FA/FB, 도곽 밖으로 뻗는 선을 담은 세대 블록,
    그 안의 기호·속성·확장 사전(XCLIP 흉내) 블록, 대칭 삽입 세대, 돌출 −Z 원, '$0$' 이름의 담는 블록."""
    doc = ezdxf.new()
    fr = doc.blocks.new('FRAME')
    fr.add_lwpolyline([(0, 0), (1000, 0), (1000, 700), (0, 700)], close=True)
    win = doc.blocks.new('UX$0$WIN')                   # xref bind 로 이름에 $0$ 가 붙은 기호 블록
    win.add_line((0, 0), (30, 0))
    tag = doc.blocks.new('TAG')
    tag.add_circle((0, 0), 5)
    tag.add_attdef('MARK', (0, 0))
    doc.blocks.new('DYN').add_line((0, 0), (10, 0))
    unit = doc.blocks.new('UNIT')                       # 담는 블록(세대 평면)
    unit.add_line((0, 0), (100, 0), dxfattribs={'layer': 'UX$0$A-WALL'})
    # 도곽 밖으로 나가는 선 — 90° 회전 삽입 뒤 x+2300(어느 도곽에도 없다). 이 선 때문에 UNIT 삽입이 도곽보다 크고 서로 겹친다
    unit.add_line((0, -2300), (1400, -2300), dxfattribs={'layer': 'UX$0$A-WALL'})
    unit.add_blockref('UX$0$WIN', (50, 0), dxfattribs={'layer': 'UX$0$A-WIN'})
    doc.blocks.new('UX$0$UY$0$SYM').add_line((0, 0), (5, 0))     # xref 안의 xref 기호 — 접두어가 두 번 붙는다
    unit.add_blockref('UX$0$UY$0$SYM', (80, 0), dxfattribs={'layer': 'UX$0$A-WIN'})
    unit.add_blockref('TAG', (20, 20)).add_auto_attribs({'MARK': 'W1'})
    # 확장 사전(동적 블록 표현·XCLIP)을 단 중첩 INSERT — copy() 가 실패해 blockref 대체 경로로 간다
    dyn = unit.add_blockref('DYN', (60, 60), dxfattribs={'layer': 'UX$0$A-DOOR', 'color': 1})
    xf = dyn.new_extension_dict().add_dictionary('ACAD_FILTER')
    xf['SPATIAL'] = doc.objects.new_entity('SPATIAL_FILTER', dxfattribs={'owner': xf.dxf.handle})
    mir = doc.blocks.new('UMIR')                        # 대칭(xscale=-1)으로 꽂는 세대 — 풀면 OCS 가 −Z 가 된다
    mir.add_lwpolyline([(0, 0), (100, 0)], dxfattribs={'layer': 'UM$0$A-WALL'})
    mir.add_arc((50, 50), 10, 0, 90, dxfattribs={'layer': 'UM$0$A-DOOR'})
    hall = doc.blocks.new('FB$0$HALL')                  # '$0$' 이름이지만 벽을 담은 블록(층 xref 안의 복도)
    hall.add_line((0, 0), (0, 100), dxfattribs={'layer': 'FB$0$A-WALL-DRY'})
    core = doc.blocks.new('CORE')                       # 여러 층 코어를 옆으로 담은 xref — FB 도곽 원점에 오차를 달고 꽂힌다
    core.add_line((500, 300), (600, 300), dxfattribs={'layer': 'CX$0$S-CONC'})     # → (1700,300): FB 도곽 안
    core.add_line((1500, 300), (1600, 300), dxfattribs={'layer': 'CX$0$S-CONC'})   # → (2700,300): 밖
    msp = doc.modelspace()
    ci = msp.add_blockref('CORE', (1200, -1e-9))
    xc = ci.new_extension_dict().add_dictionary('ACAD_FILTER')                     # XCLIP 된 담는 블록
    xc['SPATIAL'] = doc.objects.new_entity('SPATIAL_FILTER', dxfattribs={'owner': xc.dxf.handle})
    for x0, g in ((0, 'FA'), (1200, 'FB')):
        msp.add_blockref('FRAME', (x0, 0))
        msp.add_line((x0 + 100, 600), (x0 + 400, 600), dxfattribs={'layer': f'{g}$0$A-WALL'})
        msp.add_line((x0 + 100, 650), (x0 + 400, 650), dxfattribs={'layer': f'{g}$0$A-WALL'})
        msp.add_blockref('UX$0$WIN', (x0 + 200, 600), dxfattribs={'layer': f'{g}$0$A-WIN'})
        msp.add_blockref('UNIT', (x0 + 100, 100), dxfattribs={'rotation': 90})
    msp.add_blockref('UMIR', (1900, 400), dxfattribs={'xscale': -1})              # WCS (1800..1900, 400)
    msp.add_blockref('FB$0$HALL', (1600, 100))
    msp.add_circle((-1500, 300), 20, dxfattribs={'layer': 'FB$0$A-COL', 'extrusion': (0, 0, -1)})  # WCS (1500,300)
    doc.saveas(path)
    return path


def selftest():
    import tempfile
    from ezdxf.math import Vec2
    with tempfile.TemporaryDirectory() as d:
        m = frame_map(load(demo_doc(str(Path(d) / 'demo.dxf'))))
        # 단층 도면: 반복되는 작은 기호만 있다 — 도곽으로 뽑지 않고 멈춘다
        one = ezdxf.new(units=4)
        one.blocks.new('ROOMTAG').add_lwpolyline([(0, 0), (4050, 0), (4050, 1500), (0, 1500)], close=True)
        for x in (0, 10000, 20000, 30000):
            one.modelspace().add_blockref('ROOMTAG', (x, 0))
        try:
            find_frames(one.modelspace(), Extents())
            raise AssertionError('단층 도면에서 도곽을 골랐다')
        except SystemExit as ex:
            assert '종합평면도가 아니다' in str(ex), ex
    print_map(m)
    assert m['frame_block'] == ['FRAME'], m['frame_block']    # UNIT 도 2번 삽입됐고 더 크지만 서로 겹친다
    assert [c['name'] for c in m['frame_candidates']][:2] == ['UNIT', 'FRAME'], m['frame_candidates']
    assert [f['bbox'][0] for f in m['frames']] == [0, 1200]
    assert [list(f['groups']) for f in m['frames']] == [['FA'], ['FB']], [f['groups'] for f in m['frames']]
    assert m['frames'][1]['groups']['FB'] == 4, m['frames'][1]['groups']        # 돌출 −Z 원도 WCS 로 FB 도곽이다
    ins = m['frames'][1]['inserts']
    assert ins['UNIT']['count'] == 1 and ins['UMIR']['count'] == 1, ins
    assert ins['UNIT']['block_entities'] > ins['UX$0$WIN']['block_entities']
    assert ins['FB$0$HALL']['roles'] == {'A-WALL-DRY': 1}, ins['FB$0$HALL']
    ml = ezdxf.new().modelspace().add_multileader_mtext('Standard')
    ml.set_content('W1')
    ml.add_leader_line(ezdxf.render.mleader.ConnectionSide.left, [Vec2(5, 5)])
    ml.build(insert=Vec2(1300, 200))
    p = anchor(ml.multileader, lambda e: None)          # bbox 가 실패해도(cp949 프록시) 기준점이 있다
    assert p is not None and abs(p[0] - 1300) < 10 and abs(p[1] - 200) < 10, p
    print('selftest ok')


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('dxf', nargs='?')
    ap.add_argument('--frame-block', help='도곽 블록 이름 정규식(기본: 자동)')
    ap.add_argument('-o', '--out', help='JSON 으로도 쓴다')
    ap.add_argument('--selftest', action='store_true')
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if not a.dxf:
        ap.error('dxf 경로가 필요하다')
    m = frame_map(load(a.dxf), a.frame_block)
    print_map(m)
    if a.out:
        with open(a.out, 'w', encoding='utf-8') as f:
            json.dump(m, f, ensure_ascii=False, indent=1)
        print('wrote', a.out)


if __name__ == '__main__':
    main()
