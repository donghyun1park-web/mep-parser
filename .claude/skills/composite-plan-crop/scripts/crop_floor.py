"""종합평면도에서 한 층의 도곽만 잘라 평평한 DXF 로 만든다(+ 푼 담는 블록의 변환 사이드카).

    python crop_floor.py --src <종합.dxf> --dst <층.dxf> --floor-group "<층 xref>" --explode "^<세대 xref 접두어>" --explode "^<코어 xref 접두어>"
    python crop_floor.py --src ... --dst ... --frame-index 7 --explode ...
    python crop_floor.py --selftest

1. 도곽: frame_map.py 와 같은 규칙(자동 또는 --frame-block). 층은 그 층 레이어 그룹의 부재가 가장 많이 든 도곽.
2. 자르기: 부재 하나의 기준점(WCS)이 도곽 안이면 남긴다 — 레이어로 거르면 세대·코어(블록 삽입)가 빠진다.
3. 풀기: --explode 에 맞는 '담는 블록'(세대·코어 xref)만 부재마다 복사·변환한다. 문·창 기호는 INSERT 로 남긴다.
   '$0$' 이름(bind 된 xref 안의 중첩 블록)은 --explode 가 마지막 '$0$' 뒤 제 이름에 맞을 때만 푼다 — '^<세대 xref>' 가
   '<세대 xref>$0$<창 블록>' 까지 풀지 않게. --keep-block 은 무엇보다 먼저다. 중첩된 담는 블록은 다음 회차에 푼다.
4. 다시 자르기: 코어 xref 하나에 여러 층 코어가 옆으로 들어 있어 풀면 이웃 도곽까지 나간다.
5. 저장: <dst> + <dst 이름>.inserts.json(푼 삽입의 이름·삽입점·회전·배율·4x4 행렬·XCLIP 여부, 도곽 bbox, sheet_offset).
XCLIP(ACAD_FILTER)은 적용하지 않는다 — 세고 경고한다(경계 좌표 규약을 실측으로 확인하지 못했다).
"""
import argparse
import collections
import json
import re
import sys
import time
from pathlib import Path

from ezdxf.entities import factory

from frame_map import BIND, Extents, anchor, demo_doc, find_frames, frame_of, group_of, inside, load


def pick_frame(msp, ext, frames, group):
    per, seen = collections.Counter(), collections.Counter()
    for e in msp:
        g = group_of(e.dxf.layer)
        if g is None:
            continue
        seen[g] += 1
        if g == group:
            p = anchor(e, ext)
            i = frame_of(frames, p) if p is not None else None
            if i is not None:
                per[i] += 1
    if not per:
        near = [g for g in seen if group in g] or list(seen)[:20]
        sys.exit(f'층 그룹 {group!r} 의 부재가 어느 도곽에도 없다. 그룹 이름은 frame_map.py 출력 그대로: {near}'
                 + ('' if seen else " — '$0$' 층 레이어가 아예 없다. --frame-index 로 고른다"))
    ranked = per.most_common()
    if len(ranked) > 1:
        print(f'[!] 층 그룹이 도곽 여러 장에 걸친다 {ranked} — 가장 많은 도곽 [{ranked[0][0]}] 을 쓴다', flush=True)
    return ranked[0][0]


def is_container(n, explode, keep):
    """--keep-block 이 먼저. '$0$' 이름은 마지막 '$0$' 뒤 제 이름이 --explode 에 맞아야 담는 블록이다 — 세대 접두어
    정규식이 그 세대 xref 안의 창 블록('<세대 xref>$0$<창>')에도 맞기 때문. '마지막': xref 안의 xref 는
    '<세대 xref>$0$<세대 xref>$0$<기호>' 처럼 접두어가 두 번 붙는다(실측: 2개 — 첫 '$0$' 로 자르면 풀렸다).
    층 xref 안의 복도처럼 '$0$' 이름인데 벽을 담은 블록(실측)은 제 이름으로 --explode 에 준다."""
    if any(re.search(k, n) for k in keep):
        return False
    own = n.rsplit(BIND, 1)[-1]
    return any(re.search(r, own) for r in explode)


def _xclip(e):
    try:
        return e.has_extension_dict and 'ACAD_FILTER' in e.get_extension_dict()
    except Exception:
        return False


def _clip(msp, ext, box, stats, tag):
    drop = []
    for e in msp:
        p = anchor(e, ext)
        if p is None:
            stats[f'{tag}:no_anchor:{e.dxftype()}'] += 1
            drop.append(e)
        elif not inside(box, p):
            stats[f'{tag}:outside_frame' + (f':{e.dxftype()}' if tag == 'reclip' else '')] += 1
            drop.append(e)
    for e in drop:
        msp.delete_entity(e)


def _copy_into(msp, doc, ent, m, stats):
    """블록 정의 부재 하나를 모델공간으로. ezdxf explode() 는 블록 안 부재 하나에서 통째로 실패해 부재마다 한다."""
    if ent.dxftype() == 'INSERT' and _xclip(ent):
        stats['xclip:nested'] += 1
    try:
        c = ent.copy()
        c.transform(m)
        factory.bind(c, doc)
        msp.add_entity(c)
        stats['explode:copied'] += 1
        return
    except Exception:
        pass
    if ent.dxftype() == 'INSERT':
        # 확장 사전(동적 블록 표현·XCLIP)을 단 INSERT 는 copy() 가 실패한다(실측: 확장 사전의 내용물 복제 실패 429 +
        # SPATIAL_FILTER 복제 불가 2 — 속성과는 무관). 같은 블록을 같은 속성(레이어·색·선종류·돌출·배율·회전)으로 새로
        # 꽂고 ATTRIB 을 옮긴 뒤 변환한다. 확장 사전은 옮기지 않는다 — 동적 블록 표현과 XCLIP 이 여기서 사라진다.
        try:
            c = msp.add_blockref(ent.dxf.name, ent.dxf.insert, dxfattribs=ent.dxfattribs(drop={'handle', 'owner'}))
            for a in ent.attribs:
                c.add_attrib(a.dxf.tag, a.dxf.text, a.dxf.insert, dxfattribs=a.dxfattribs(drop={'handle', 'owner'}))
            c.transform(m)
            stats['explode:blockref_fallback'] += 1
            return
        except Exception:
            pass
    stats[f'explode:failed:{ent.dxftype()}'] += 1


def crop(doc, floor_group=None, frame_index=None, frame_re=None, explode=(), keep=()):
    msp = doc.modelspace()
    ext = Extents()
    names, frames, _ = find_frames(msp, ext, frame_re)
    if frame_index is None:
        frame_index = pick_frame(msp, ext, frames, floor_group)
    elif not 0 <= frame_index < len(frames):
        sys.exit(f'--frame-index {frame_index}: 도곽은 0..{len(frames) - 1}')
    box = frames[frame_index]
    print(f'frame [{frame_index}] of {len(frames)} ({names})  x[{box[0]:.0f},{box[2]:.0f}] y[{box[1]:.0f},{box[3]:.0f}]'
          f'  size {box[2] - box[0]:.0f} x {box[3] - box[1]:.0f}', flush=True)

    stats = collections.Counter()
    _clip(msp, ext, box, stats, 'crop')
    print(f'crop: kept {len(msp)}  {dict(stats)}', flush=True)

    exploded, spared = [], collections.Counter()
    for rnd in range(1, 10):
        todo = []
        for e in msp.query('INSERT'):
            n = e.dxf.name
            if is_container(n, explode, keep):
                todo.append(e)
            elif BIND in n and any(re.search(r, n) for r in explode):
                spared[n] += 1             # 접두어로만 맞은 '$0$' 기호 — 풀지 않았다
        if not todo:
            break
        for e in todo:
            d, m = e.dxf, e.matrix44()
            clip = _xclip(e)
            if clip:
                stats['xclip:container'] += 1
            exploded.append({'round': rnd, 'name': d.name, 'layer': d.layer, 'insert': list(d.insert),
                             'rotation': d.get('rotation', 0), 'xscale': d.get('xscale', 1),
                             'yscale': d.get('yscale', 1), 'zscale': d.get('zscale', 1),
                             'extrusion': list(d.get('extrusion', (0, 0, 1))), 'xclip': clip,
                             'matrix': [list(r) for r in m.rows()]})
            for ent in doc.blocks.get(d.name):
                if ent.dxftype() != 'ATTDEF':
                    _copy_into(msp, doc, ent, m, stats)
            msp.delete_entity(e)
        print(f'round {rnd}: exploded {len(todo)} container inserts  {dict(stats)}', flush=True)
    else:
        print('[!] 9회차 뒤에도 담는 블록이 남았다 — --explode 정규식이 자기 자신을 담는 블록에 맞는지 확인', flush=True)
    if spared:
        print(f"kept as symbol ('$0$' 이름, --explode 가 접두어로만 맞음): {len(spared)} names, "
              f"e.g. {sorted(spared)[:5]} — 벽을 담은 블록이면 마지막 '$0$' 뒤 이름으로 --explode 에 준다", flush=True)
    if stats['xclip:container'] or stats['xclip:nested']:
        print(f"[!] XCLIP 무시: 담는 블록 {stats['xclip:container']} + 중첩 INSERT {stats['xclip:nested']} — 시트에서 가린 "
              '부분까지 들어왔다. 원본 시트와 눈으로 대조한다(사이드카 containers[].xclip)', flush=True)

    _clip(msp, ext, box, stats, 'reclip')
    rc = {k: v for k, v in stats.items() if k.startswith('reclip')}
    print(f'reclip: dropped {sum(rc.values())} outside the frame  {rc}', flush=True)
    return {'frame_block': names, 'frame_index': frame_index, 'floor_group': floor_group,
            'frame_bbox': list(box), 'sheet_offset': [box[0], box[1]],
            'explode': list(explode), 'keep_block': list(keep), 'spared_bind_symbols': sorted(spared),
            'counts': dict(stats), 'entities_by_type': dict(collections.Counter(e.dxftype() for e in msp).most_common()),
            'containers': exploded}


def sidecar_path(dst):
    return Path(dst).with_suffix('.inserts.json')


def run(src, dst, **kw):
    t0 = time.time()
    doc = load(src)
    info = crop(doc, **kw)
    doc.saveas(dst)
    info['src'], info['dst'] = str(src), str(dst)
    sidecar_path(dst).write_text(json.dumps(info, ensure_ascii=False, indent=1), encoding='utf-8')
    print(f'saved {dst}  entities={sum(info["entities_by_type"].values())}  {info["entities_by_type"]}')
    print(f'sidecar {sidecar_path(dst)}  containers={len(info["containers"])}')
    print(f'sheet_offset {info["sheet_offset"]} — 좌표는 아직 도곽(시트) 좌표다. 설비 겹치기 전에 통심선으로 맞춘다  ({time.time() - t0:.1f}s)')
    return info


def selftest():
    import tempfile
    import ezdxf
    with tempfile.TemporaryDirectory() as d:
        src, dst = demo_doc(str(Path(d) / 'demo.dxf')), str(Path(d) / 'fb.dxf')
        # '^U' 는 UNIT·UMIR 과 기호 UX$0$WIN 둘 다에 맞는다 — '$0$' 규칙이 기호를 지켜야 한다. 사용자 --keep-block 을
        # 줘도 그 규칙이 꺼지지 않는다. 'HALL' 은 '$0$' 뒤 제 이름에 맞으므로 '$0$' 이름이어도 풀린다.
        explode = ['^U', '^CORE$', '^HALL$']
        info = run(src, dst, floor_group='FB', explode=explode, keep=['^NOPE$'])
        side = json.loads(sidecar_path(dst).read_text(encoding='utf-8'))
        msp = ezdxf.readfile(dst).modelspace()
        assert info['frame_index'] == 1 and side['sheet_offset'] == [1200, 0], side['sheet_offset']
        assert not [e for e in msp if group_of(e.dxf.layer) == 'FA'], 'FA 층이 남았다'
        ins = [(e.dxf.name, round(e.dxf.insert.x), round(e.dxf.insert.y), round(e.dxf.rotation)) for e in msp.query('INSERT')]
        assert not [i for i in ins if i[0] in ('UNIT', 'UMIR', 'CORE', 'FB$0$HALL')], ins     # 담는 블록은 풀렸다
        assert ('UX$0$WIN', 1300, 150, 90) in ins and ('UX$0$WIN', 1400, 600, 0) in ins, ins   # 기호는 INSERT
        assert ('UX$0$UY$0$SYM', 1300, 180, 90) in ins, ins       # 마지막 '$0$' 뒤 이름('SYM')으로 본다
        assert side['spared_bind_symbols'] == ['UX$0$UY$0$SYM', 'UX$0$WIN'], side['spared_bind_symbols']
        tag = [e for e in msp.query('INSERT') if e.dxf.name == 'TAG']
        assert len(tag) == 1 and tag[0].get_attrib_text('MARK') == 'W1', '속성 값이 사라졌다'
        dyn = [e for e in msp.query('INSERT') if e.dxf.name == 'DYN']     # 확장 사전 INSERT: blockref 대체 경로
        assert len(dyn) == 1 and dyn[0].dxf.color == 1 and dyn[0].dxf.layer == 'UX$0$A-DOOR', dyn
        assert (round(dyn[0].dxf.insert.x), round(dyn[0].dxf.insert.y)) == (1240, 160), dyn[0].dxf.insert
        assert info['counts'].get('explode:blockref_fallback') == 1, info['counts']
        assert info['counts'].get('xclip:nested') == 1 and info['counts'].get('xclip:container') == 1, info['counts']
        lines = sorted((round(e.dxf.start.x), round(e.dxf.start.y), round(e.dxf.end.x), round(e.dxf.end.y))
                       for e in msp.query('LINE') if e.dxf.layer == 'UX$0$A-WALL')
        assert lines == [(1300, 100, 1300, 200)], lines   # 밖으로 나간 선은 잘렸다
        core = [(round(e.dxf.start.x), round(e.dxf.start.y)) for e in msp.query('LINE') if e.dxf.layer == 'CX$0$S-CONC']
        assert core == [(1700, 300)], core      # 도곽 원점에 꽂힌 담는 블록(y=-1e-9)도 이 도곽 것이다
        hall = [(round(e.dxf.start.x), round(e.dxf.start.y)) for e in msp.query('LINE') if e.dxf.layer == 'FB$0$A-WALL-DRY']
        assert hall == [(1600, 100)], hall      # '$0$' 이름의 담는 블록도 제 이름으로 풀린다
        # 대칭(xscale=-1) 세대: 풀면 경량 폴리선·호가 OCS −Z 로 저장된다 — WCS 기준점이어야 남는다
        mir = {e.dxftype(): e for e in msp if e.dxf.layer.startswith('UM$0$')}
        assert sorted(mir) == ['ARC', 'LWPOLYLINE'], sorted(mir)
        assert [round(v) for v in next(iter(mir['LWPOLYLINE'].vertices_in_wcs()))][:2] == [1900, 400]
        assert [round(v) for v in mir['ARC'].ocs().to_wcs(mir['ARC'].dxf.center)][:2] == [1850, 450]
        col = [e for e in msp.query('CIRCLE') if e.dxf.layer == 'FB$0$A-COL']    # 원래 돌출 −Z 인 모델공간 원
        assert len(col) == 1, '돌출 −Z 원이 첫 자르기에서 빠졌다'
        assert info['counts'].get('reclip:outside_frame:LINE') == 2, info['counts']
        assert sorted(c['name'] for c in side['containers']) == ['CORE', 'FB$0$HALL', 'UMIR', 'UNIT'], side['containers']
        c, = [c for c in side['containers'] if c['name'] == 'UNIT']
        assert c['insert'][:2] == [1300, 100] and c['rotation'] == 90 and not c['xclip']
        assert [round(v) for v in c['matrix'][3][:2]] == [1300, 100], c['matrix']
        assert [c['xclip'] for c in side['containers'] if c['name'] == 'CORE'] == [True]
        assert run(src, dst, frame_index=1, explode=explode)['frame_bbox'] == info['frame_bbox']
    print('selftest ok')


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--src')
    ap.add_argument('--dst')
    g = ap.add_mutually_exclusive_group()
    g.add_argument('--floor-group', help="층 레이어 그룹(레이어 이름의 '$0$' 앞부분, frame_map.py 출력 그대로)")
    g.add_argument('--frame-index', type=int, help='frame_map.py 의 도곽 번호(x 순)')
    ap.add_argument('--frame-block', help='도곽 블록 이름 정규식(기본: 자동)')
    ap.add_argument('--explode', action='append', default=[],
                    help="풀 담는 블록 이름 정규식(반복 가능). '$0$' 이름은 마지막 '$0$' 뒤 제 이름에 맞춘다")
    ap.add_argument('--keep-block', action='append', default=[], help='풀지 않을 블록 정규식(반복 가능, --explode 보다 먼저)')
    ap.add_argument('--selftest', action='store_true')
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if not (a.src and a.dst) or (a.floor_group is None and a.frame_index is None):
        ap.error('--src, --dst 와 --floor-group 또는 --frame-index 가 필요하다')
    if not a.explode:
        print('[!] --explode 없음 — 세대·코어 블록이 INSERT 로 남아 파서가 그 안의 벽을 못 본다', flush=True)
    run(a.src, a.dst, floor_group=a.floor_group, frame_index=a.frame_index, frame_re=a.frame_block,
        explode=a.explode, keep=a.keep_block)


if __name__ == '__main__':
    main()
