# -*- coding: utf-8 -*-
"""구조·하중 개략 검토 화면 생성기.

입력: geometry.json (mep-parser 산출물) + 구조개요서 설계기준(상수)
출력: 자립형 HTML 대시보드 (three.js 3D 기둥 축력 히트맵 + 하중 집계 패널)

※ 본 산출물은 BIM 물량과 구조개요서 기준으로 한 '개략 검토'이며
   구조기술사의 구조계산서를 대체하지 않는다.

사용: python struct_review.py <geometry.json> [-o out.html]
"""
import argparse
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from preview import importmap_section  # three.js importmap 재사용

# ══════════ 구조개요서 [APPENDIX 12] 별첨#4 설계기준 ══════════
CRITERIA = {
    "project": "NHN 광주 EDC 데이터센터",
    "doc": "[APPENDIX 12] 별첨#4 구조 개요 (O-002026)",
    "code": "KDS 41 00 00 / ACI 318-19 / ASCE 7-16",
    "structure": "철골조 (하부 철근콘크리트)",
    "foundation": "지내력기초 (지반조사 후 변경 가능)",
    "gwl": "GL 0.00 m (가정치)",
    "program": "MIDAS Design+ / BeST Pro",
    "concrete": [("기초", "24 MPa"), ("기초 외 (수직·수평부재)", "27 MPa")],
    "rebar": [("HD13 이하", "SD500  fy=500 MPa"), ("HD16 이상", "SD600  fy=600 MPa")],
    "steel": [("SS275", "Fy=275 (t≤16) / 265 MPa"), ("SM355", "Fy=355 (t≤16) / 345 MPa"),
              ("SHN355", "Fy=355 MPa (t≤75)"), ("접합볼트", "F10T H.S.B")],
}
# 2.1 하중표 (kN/m²)
LOAD_TABLE = [
    ("데이터홀", 7.30, 20.00),
    ("기계실·발전기실 등", 7.30, 35.00),
    ("전기실·복도·소화가스실·UPS실·배터리실 등", 7.30, 25.00),
    ("사무동·검사실·하역장·쓰레기처리실·업무공간 등", 7.30, 20.00),
    ("옥상층", 7.30, 10.00),
]
SNOW = dict(Sg=0.5, Cb=0.7, Ce=1.1, Ct=1.2, Is=1.1, Sf=0.51, rain=0.25, total=0.76)
WIND = dict(V0=28.0, expo="B", Iw=1.00, region="광주")
SEIS = dict(Z=0.11, S="S4", Fa=1.448, Fv=2.048, SDS=0.425, SD1=0.240,
            R=3.0, Omega=3.0, Cd=3.0, IE=1.20, Ta=0.274, cat="D", vs=320.7)
COMBOS = [
    ("1.4(D+F)", "고정하중·유체"),
    ("1.2(D+F+T)+1.6L+0.5(Lr or S or R)", "고정·유체·온도·활하중"),
    ("1.2D+1.6(Lr or S or R)+(1.0L or 0.5W)", "지붕활하중·설하중·강우"),
    ("1.2D+1.0W+1.0L+0.5(Lr or S or R)", "풍하중 조합"),
    ("1.2D+1.0E+1.0L+0.2S", "지진 조합"),
    ("0.9D+1.0W", "풍하중 (전도)"),
    ("0.9D+1.0E", "지진 (전도)"),
]
# 층별 적용 (층, 슬래브 레이어, 용도, DL, LL, 지지요소 z_base, 추가벽 z_base)
# TODO: 슬래브 레이어명과 z 값이 NHN 장성 DC 전용 하드코딩이다.
#       stack.json 이 생기면 levels 선언에서 읽어 프로젝트 독립으로 만들 것.
LEVELS = [
    ("PIT",    "generated_from_exterior_walls_bbox", "기계실·발전기실 등", 7.30, 35.00, None, None),
    ("1F",     "generated_1F_slab_bbox",            "데이터홀",           7.30, 20.00, 2500.0, None),
    ("중층 2F", "중층_deck_generated",                "전기실·UPS실 등",     7.30, 25.00, 6050.0, None),
    ("옥상 RF1", "옥상_deck_generated",               "옥상층",             7.30, 10.00, 6050.0, 10550.0),
    ("옥상 RF2", "PH_deck_generated",                 "옥상층",             7.30, 10.00, 14750.0, None),
]
FCK, FY, RHO = 27.0, 500.0, 0.01
CELL = 1000.0

# ══════════ 계산 ══════════


def poly_area(pts):
    a = 0.0
    for (x1, y1), (x2, y2) in zip(pts, pts[1:] + pts[:1]):
        a += x1 * y2 - x2 * y1
    return abs(a) / 2.0 / 1e6


def supports_at(d, zb):
    cols, segs = [], []
    for c in d["elements"]["column"]:
        if abs(c.get("z_base", 0) - zb) < 1:
            xs = [p[0] for p in c["points"]]; ys = [p[1] for p in c["points"]]
            cols.append(((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2))
    for w in d["elements"]["wall"]:
        if abs(w.get("z_base", 0) - zb) >= 1:
            continue
        cl = w.get("centerline") or w.get("points") or []
        for a, b in zip(cl, cl[1:]):
            segs.append((a[0], a[1], b[0], b[1]))
    return np.array(cols, float).reshape(-1, 2), np.array(segs, float).reshape(-1, 4)


def dist_segs(px, py, segs):
    if len(segs) == 0:
        return np.full(px.shape, 1e12)
    best = np.full(px.shape, 1e12)
    for ax, ay, bx, by in segs:
        dx, dy = bx - ax, by - ay
        L2 = max(dx * dx + dy * dy, 1e-9)
        t = np.clip(((px - ax) * dx + (py - ay) * dy) / L2, 0.0, 1.0)
        np.minimum(best, np.hypot(px - (ax + t * dx), py - (ay + t * dy)), out=best)
    return best


def distribute(d, plate, q, sup_zb, extra_zb):
    cols, segs = supports_at(d, sup_zb)
    if extra_zb is not None:
        _, s2 = supports_at(d, extra_zb)
        segs = np.vstack([segs, s2]) if len(segs) else s2
    xs = [p[0] for p in plate]; ys = [p[1] for p in plate]
    PX, PY = np.meshgrid(np.arange(min(xs) + CELL / 2, max(xs), CELL),
                         np.arange(min(ys) + CELL / 2, max(ys), CELL))
    PX, PY = PX.ravel(), PY.ravel()
    dw = dist_segs(PX, PY, segs)
    if len(cols):
        dc = np.hypot(PX[:, None] - cols[None, :, 0], PY[:, None] - cols[None, :, 1])
        ci = dc.argmin(axis=1); dcm = dc[np.arange(len(PX)), ci]
    else:
        ci = np.zeros(len(PX), int); dcm = np.full(len(PX), 1e12)
    cell = q * (CELL / 1000.0) ** 2
    to_col = dcm <= dw
    ckN = np.zeros(len(cols))
    np.add.at(ckN, ci[to_col], cell)
    return cols, ckN, cell * int((~to_col).sum()), cell * len(PX)


def compute(d):
    out = {"levels": [], "criteria": CRITERIA}
    plates = {}
    for name, lay, use, dl, ll, sup, ex in LEVELS:
        recs = [s for s in d["elements"]["slab"] if s.get("layer") == lay]
        if not recs:
            continue
        pl = recs[0]["points"]
        plates[name] = pl
        A = poly_area(pl)
        q = 1.2 * dl + 1.6 * ll
        row = dict(name=name, use=use, dl=dl, ll=ll, A=A, q=q,
                   svc=(dl + ll) * A, fac=q * A, col=None, wall=None)
        if sup is not None:
            cols, ckN, wkN, tot = distribute(d, pl, q, sup, ex)
            row["col"], row["wall"] = float(ckN.sum()), float(wkN)
            out.setdefault("_dist", {})[name] = (cols, ckN)
        out["levels"].append(row)

    # 기둥 축력 누적 (PIT 기둥 = 1F바닥 분담 + 상부 1F기둥 축력)
    pit_cols, pit_kN = out["_dist"]["1F"]
    f1_cols, f1_kN = out["_dist"]["옥상 RF1"]
    mz_cols, mz_kN = out["_dist"]["중층 2F"]
    f1_tot = f1_kN.copy()
    for i, (x, y) in enumerate(f1_cols):
        dd = np.hypot(mz_cols[:, 0] - x, mz_cols[:, 1] - y)
        if len(dd) and dd.min() < 500:
            f1_tot[i] += mz_kN[dd.argmin()]
    Pu = pit_kN.copy()
    for i, (x, y) in enumerate(pit_cols):
        dd = np.hypot(f1_cols[:, 0] - x, f1_cols[:, 1] - y)
        if len(dd) and dd.min() < 500:
            Pu[i] += f1_tot[dd.argmin()]

    Ag = 800.0 * 800.0
    Ast = Ag * RHO
    phiPn = 0.65 * 0.80 * (0.85 * FCK * (Ag - Ast) + FY * Ast) / 1000.0
    out["columns"] = [dict(x=float(x), y=float(y), Pu=float(p), r=float(p / phiPn))
                      for (x, y), p in zip(pit_cols, Pu)]
    out["phiPn"] = phiPn

    # 자중 & 밑면전단력 (PIT 바닥 이상 = 지상 구조체)
    RC_D, ST_D = 24.0, 78.5
    conc = steel = 0.0
    for s in d["elements"]["slab"]:
        lay = s.get("layer", "")
        if lay == "generated_from_exterior_walls_bbox":
            continue                              # 지반 지지 → 지진질량 제외
        t = float(s.get("overrides", {}).get("thickness", 200)) / 1000.0
        v = poly_area(s["points"]) * t
        if lay.startswith(("ROOF_", "PH_PH", "PH_SB")) or "GIRDER_generated" in lay:
            steel += v * 0.35
        else:
            conc += v
    for c in d["elements"]["column"]:
        if abs(c.get("z_base", 0) - 1200) < 1:
            continue
        conc += poly_area(c["points"]) * float(c.get("overrides", {}).get("height", 3000)) / 1000.0
    for w in d["elements"]["wall"]:
        cl = w.get("centerline") or w.get("points") or []
        ww = float(w.get("overrides", {}).get("width") or w.get("width_detected") or 200) / 1000.0
        h = float(w.get("overrides", {}).get("height", 2800)) / 1000.0
        L = sum(((b[0] - a[0]) ** 2 + (b[1] - a[1]) ** 2) ** 0.5 for a, b in zip(cl, cl[1:])) / 1000.0
        conc += L * ww * h
    W_self = conc * RC_D + steel * ST_D
    W_dl = sum(r["dl"] * r["A"] for r in out["levels"] if r["name"] != "PIT")
    W = W_self + W_dl
    Cs = min(SEIS["SD1"] / (SEIS["Ta"] * SEIS["R"] / SEIS["IE"]),
             SEIS["SDS"] / (SEIS["R"] / SEIS["IE"]))
    Cs = max(Cs, 0.044 * SEIS["SDS"] * SEIS["IE"], 0.01)
    out["mass"] = dict(conc=conc, steel=steel, W_self=W_self, W_dl=W_dl, W=W, Cs=Cs, V=Cs * W)
    out["plates"] = {k: v for k, v in plates.items()}
    out["walls"] = [dict(p=(w.get("centerline") or w["points"]),
                         z=w.get("z_base", 0),
                         h=w.get("overrides", {}).get("height", 2800))
                    for w in d["elements"]["wall"]]
    del out["_dist"]
    return out


# ══════════ HTML ══════════
HTML = r"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<title>구조·하중 개략 검토 — __PROJ__</title>
__IMPORTMAP__
<style>
*{box-sizing:border-box} html,body{margin:0;height:100%;font-family:"Malgun Gothic",system-ui,sans-serif}
body{display:flex;background:#0f1216;color:#e8ecf2;overflow:hidden}
#view{flex:1;position:relative;min-width:0}
#hud{position:absolute;left:16px;top:14px;pointer-events:none}
#hud h1{margin:0;font-size:19px;letter-spacing:-.3px}
#hud .s{margin-top:3px;font-size:12px;color:#8e9bab}
#legend{position:absolute;left:16px;bottom:16px;background:#161b22cc;border:1px solid #2a323d;
  border-radius:8px;padding:10px 12px;font-size:11px}
#legend .bar{width:190px;height:11px;border-radius:3px;margin:6px 0 4px;
  background:linear-gradient(90deg,#2d7ff9,#37b24d,#f59f00,#e03131)}
#legend .lb{display:flex;justify-content:space-between;color:#93a1b0}
aside{width:396px;background:#12161c;border-left:1px solid #232b35;overflow-y:auto;padding:14px 16px 40px}
h2{font-size:12px;letter-spacing:.08em;color:#6ea8fe;margin:20px 0 8px;text-transform:uppercase}
h2:first-child{margin-top:4px}
table{width:100%;border-collapse:collapse;font-size:11.5px}
th,td{padding:4px 5px;border-bottom:1px solid #222a34;text-align:right;white-space:nowrap}
th{color:#8e9bab;font-weight:600;text-align:right;border-bottom:1px solid #2f3a47}
td.l,th.l{text-align:left;white-space:normal}
.kv{display:flex;justify-content:space-between;font-size:11.5px;padding:3px 0;border-bottom:1px solid #1c232c}
.kv span:first-child{color:#8e9bab}
.big{display:flex;gap:8px;margin:8px 0}
.big div{flex:1;background:#171d25;border:1px solid #263040;border-radius:8px;padding:9px 10px}
.big b{display:block;font-size:19px;margin-top:3px;letter-spacing:-.5px}
.big small{color:#8e9bab;font-size:10.5px}
.ok{color:#37b24d}.ng{color:#ff6b6b}.wr{color:#f59f00}
.note{margin-top:22px;padding:10px 11px;background:#1b1710;border:1px solid #4a3a1a;
  border-radius:8px;font-size:11px;color:#d9c89a;line-height:1.6}
code{background:#1b222c;padding:1px 5px;border-radius:4px;font-size:11px;color:#a5d8ff}
</style></head><body>
<div id="view">
  <div id="hud"><h1>구조 · 하중 개략 검토</h1>
    <div class="s">__PROJ__ &nbsp;·&nbsp; 기둥 축력비 Pu/φPn 히트맵 &nbsp;·&nbsp; 마우스 드래그 회전 / 휠 확대</div></div>
  <div id="legend"><b style="font-size:11.5px">기둥 축력비 Pu/φPn</b>
    <div class="bar"></div><div class="lb"><span>0.0</span><span>0.5</span><span>0.9</span><span>1.0+</span></div>
  </div>
</div>
<aside id="side"></aside>
<script type="module">
import * as THREE from 'three';
import {OrbitControls} from 'three/addons/controls/OrbitControls.js';
const D = __DATA__;
const S = 0.001;
const el = document.getElementById('view');
const scene = new THREE.Scene(); scene.background = new THREE.Color(0x0f1216);
const cam = new THREE.PerspectiveCamera(42, el.clientWidth/el.clientHeight, 0.5, 6000);
const ren = new THREE.WebGLRenderer({antialias:true}); ren.setPixelRatio(devicePixelRatio);
ren.setSize(el.clientWidth, el.clientHeight); el.appendChild(ren.domElement);
scene.add(new THREE.HemisphereLight(0xdfe9ff,0x20262e,1.15));
const dl=new THREE.DirectionalLight(0xffffff,1.0); dl.position.set(1,-1.4,2.2); scene.add(dl);
const ctr=new OrbitControls(cam,ren.domElement); ctr.enableDamping=true;

// 중심
let xs=[],ys=[];
for(const k in D.plates) for(const p of D.plates[k]){xs.push(p[0]);ys.push(p[1]);}
const cx=(Math.min(...xs)+Math.max(...xs))/2, cy=(Math.min(...ys)+Math.max(...ys))/2;
const T=p=>[(p[0]-cx)*S,(p[1]-cy)*S];

function ramp(r){ // 0→파랑, 0.5→초록, 0.9→주황, 1+→빨강
  const c=new THREE.Color();
  if(r<0.5) c.lerpColors(new THREE.Color(0x2d7ff9),new THREE.Color(0x37b24d),r/0.5);
  else if(r<0.9) c.lerpColors(new THREE.Color(0x37b24d),new THREE.Color(0xf59f00),(r-0.5)/0.4);
  else c.lerpColors(new THREE.Color(0xf59f00),new THREE.Color(0xe03131),Math.min(1,(r-0.9)/0.3));
  return c;
}
// 슬래브(반투명)
for(const k in D.plates){
  const pts=D.plates[k], sh=new THREE.Shape();
  pts.forEach((p,i)=>{const q=T(p); i?sh.lineTo(q[0],q[1]):sh.moveTo(q[0],q[1]);});
  const g=new THREE.ExtrudeGeometry(sh,{depth:0.12,bevelEnabled:false});
  const m=new THREE.Mesh(g,new THREE.MeshLambertMaterial({color:0x54606f,transparent:true,opacity:0.18}));
  const z={'PIT':2.5,'1F':6.05,'중층 2F':10.55,'옥상 RF1':14.75,'옥상 RF2':18.75}[k]||0;
  m.position.z=z-0.12; scene.add(m);
  const eg=new THREE.LineSegments(new THREE.EdgesGeometry(g),
    new THREE.LineBasicMaterial({color:0x7c8ba1,transparent:true,opacity:0.5}));
  eg.position.z=z-0.12; scene.add(eg);
}
// 벽
const wg=new THREE.MeshLambertMaterial({color:0x3f4b5b,transparent:true,opacity:0.55});
for(const w of D.walls){ const h=w.h*S;
  for(let i=0;i<w.p.length-1;i++){
    const a=T(w.p[i]), b=T(w.p[i+1]);
    const dx=b[0]-a[0],dy=b[1]-a[1],L=Math.hypot(dx,dy); if(L<0.02)continue;
    const m=new THREE.Mesh(new THREE.BoxGeometry(L,0.2,h),wg);
    m.position.set((a[0]+b[0])/2,(a[1]+b[1])/2,w.z*S+h/2); m.rotation.z=Math.atan2(dy,dx);
    scene.add(m);
  }}
// 기둥 (축력비 색) — PIT~옥상 전 높이로 표시(축력비는 최하단 기준)
const Z0=2.5, Z1=14.55, H=Z1-Z0;
for(const c of D.columns){
  const q=T([c.x,c.y]);
  const m=new THREE.Mesh(new THREE.BoxGeometry(0.8,0.8,H),
    new THREE.MeshLambertMaterial({color:ramp(c.r)}));
  m.position.set(q[0],q[1],Z0+H/2); scene.add(m);
  if(c.r>0.9){
    const e=new THREE.LineSegments(new THREE.EdgesGeometry(m.geometry),
      new THREE.LineBasicMaterial({color:c.r>1?0xff8787:0xffd43b}));
    e.position.copy(m.position); scene.add(e);
  }
}
const box=new THREE.Box3().setFromObject(scene), sp=box.getSize(new THREE.Vector3()).length();
const ct=box.getCenter(new THREE.Vector3());
cam.position.set(ct.x+sp*0.62, ct.y-sp*0.86, ct.z+sp*0.55); cam.up.set(0,0,1);
ctr.target.copy(ct); ctr.update();
addEventListener('resize',()=>{cam.aspect=el.clientWidth/el.clientHeight;cam.updateProjectionMatrix();
  ren.setSize(el.clientWidth,el.clientHeight);});
(function loop(){requestAnimationFrame(loop);ctr.update();ren.render(scene,cam);})();

// ── 패널 ──
const C=D.criteria, M=D.mass, n=(v,f=0)=>v.toLocaleString('ko-KR',{minimumFractionDigits:f,maximumFractionDigits:f});
const cols=D.columns, ng=cols.filter(c=>c.r>1).length, wr=cols.filter(c=>c.r>0.9&&c.r<=1).length;
const mx=Math.max(...cols.map(c=>c.r));
document.getElementById('side').innerHTML=`
<h2>검토 결과 요약</h2>
<div class="big">
  <div><small>기둥 검토 (RC 800×800)</small><b>${cols.length-ng-wr}<span style="font-size:12px;color:#8e9bab"> / ${cols.length} OK</span></b></div>
  <div><small>축력비 최대</small><b class="${mx>1?'ng':'ok'}">${mx.toFixed(2)}</b></div>
</div>
<div class="big">
  <div><small>밑면전단력 V</small><b>${n(M.V)} <span style="font-size:11px">kN</span></b></div>
  <div><small>유효중량 W</small><b>${n(M.W/9.807)} <span style="font-size:11px">ton</span></b></div>
</div>
<div class="kv"><span>재검토 필요 (Pu/φPn &gt; 1.0)</span><b class="ng">${ng} 개소</b></div>
<div class="kv"><span>주의 (0.9 ~ 1.0)</span><b class="wr">${wr} 개소</b></div>
<div class="kv"><span>φPn (fck 27 · fy 500 · ρ 1%)</span><b>${n(D.phiPn)} kN</b></div>

<h2>층별 하중 집계</h2>
<table><tr><th class="l">층 / 용도</th><th>면적<br>m²</th><th>DL+LL<br>kN/m²</th><th>계수하중<br>kN</th></tr>
${D.levels.map(l=>`<tr><td class="l"><b>${l.name}</b><br><span style="color:#8e9bab;font-size:10.5px">${l.use}</span></td>
<td>${n(l.A)}</td><td>${(l.dl+l.ll).toFixed(1)}<br><span style="color:#8e9bab;font-size:10px">${l.q.toFixed(1)}</span></td>
<td>${n(l.fac)}</td></tr>`).join('')}
<tr><td class="l"><b>합계</b></td><td>${n(D.levels.reduce((s,l)=>s+l.A,0))}</td><td>—</td>
<td><b>${n(D.levels.reduce((s,l)=>s+l.fac,0))}</b></td></tr></table>

<h2>연직하중 분담 (기둥 : 벽)</h2>
<table><tr><th class="l">바닥</th><th>기둥 kN</th><th>벽 kN</th><th>기둥%</th></tr>
${D.levels.filter(l=>l.col!=null).map(l=>`<tr><td class="l">${l.name}</td><td>${n(l.col)}</td>
<td>${n(l.wall)}</td><td>${(l.col/(l.col+l.wall)*100).toFixed(0)}%</td></tr>`).join('')}</table>

<h2>축력 상위 기둥</h2>
<table><tr><th class="l">위치 (X, Y)</th><th>Pu kN</th><th>Pu/φPn</th><th>판정</th></tr>
${[...cols].sort((a,b)=>b.r-a.r).slice(0,8).map(c=>`<tr><td class="l">${n(c.x/1000,1)}, ${n(c.y/1000,1)}</td>
<td>${n(c.Pu)}</td><td>${c.r.toFixed(2)}</td>
<td class="${c.r>1?'ng':(c.r>0.9?'wr':'ok')}">${c.r>1?'재검토':(c.r>0.9?'주의':'OK')}</td></tr>`).join('')}</table>

<h2>설계하중 기준 (구조개요서 2.1)</h2>
<table><tr><th class="l">실 명</th><th>DL</th><th>LL</th><th>1.2D+1.6L</th></tr>
${D.loadTable.map(r=>`<tr><td class="l">${r[0]}</td><td>${r[1].toFixed(2)}</td><td>${r[2].toFixed(2)}</td>
<td>${(1.2*r[1]+1.6*r[2]).toFixed(2)}</td></tr>`).join('')}</table>

<h2>지진하중 (2.4 · 등가정적)</h2>
<div class="kv"><span>SDS / SD1</span><b>${D.seis.SDS} / ${D.seis.SD1}</b></div>
<div class="kv"><span>Fa / Fv · 지반 ${D.seis.S}</span><b>${D.seis.Fa} / ${D.seis.Fv}</b></div>
<div class="kv"><span>R / Ω₀ / Cd</span><b>${D.seis.R} / ${D.seis.Omega} / ${D.seis.Cd}</b></div>
<div class="kv"><span>중요도계수 IE (내진등급 Ⅰ)</span><b>${D.seis.IE}</b></div>
<div class="kv"><span>기본진동주기 Ta</span><b>${D.seis.Ta} sec</b></div>
<div class="kv"><span>내진설계범주</span><b>${D.seis.cat}</b></div>
<div class="kv"><span>지진응답계수 Cs</span><b>${M.Cs.toFixed(4)}</b></div>
<div class="kv"><span>밑면전단력 V = Cs·W</span><b>${n(M.V)} kN</b></div>

<h2>적설 · 풍하중</h2>
<div class="kv"><span>지상적설 Sg / 평지붕 Sf</span><b>${D.snow.Sg} / ${D.snow.Sf} kN/m²</b></div>
<div class="kv"><span>적설하중 (눈·비 혼합 포함)</span><b>${D.snow.total} kN/m²</b></div>
<div class="kv"><span>설계기본풍속 V₀ · 조도 ${D.wind.expo}</span><b>${D.wind.V0} m/s</b></div>

<h2>재료강도</h2>
${C.concrete.map(r=>`<div class="kv"><span>콘크리트 · ${r[0]}</span><b>${r[1]}</b></div>`).join('')}
${C.rebar.map(r=>`<div class="kv"><span>철근 · ${r[0]}</span><b>${r[1]}</b></div>`).join('')}
${C.steel.map(r=>`<div class="kv"><span>철골 · ${r[0]}</span><b>${r[1]}</b></div>`).join('')}

<h2>구조체 물량 (BIM 실물량)</h2>
<div class="kv"><span>철근콘크리트</span><b>${n(M.conc)} m³ · ${n(M.conc*24/9.807)} ton</b></div>
<div class="kv"><span>철골 (환산)</span><b>${n(M.steel)} m³ · ${n(M.steel*78.5/9.807)} ton</b></div>
<div class="kv"><span>구조체 자중 계</span><b>${n(M.W_self)} kN</b></div>

<h2>하중조합 (KDS 41)</h2>
<table>${D.combos.map(c=>`<tr><td class="l"><code>${c[0]}</code><br>
<span style="color:#8e9bab;font-size:10.5px">${c[1]}</span></td></tr>`).join('')}</table>

<div class="note"><b>⚠ 본 화면은 개략 검토입니다.</b><br>
BIM 물량과 구조개요서 기준으로 산정한 예비 검토 결과이며, 구조기술사의 구조계산서를 대체하지 않습니다.<br><br>
· 기둥 축력은 최근접 연직지지요소(기둥·벽) 기준 지배면적 분배(격자 1.0m)로 산정 — 실제 골조 강성 분배와 다릅니다.<br>
· 축력만 검토했으며 휨·전단·좌굴·접합부는 미포함입니다.<br>
· 지상부 기둥은 구조개요서상 <b>철골조</b>이나 본 검토는 RC 800×800 단면으로 일괄 가정했습니다.<br>
· 근거: ${C.doc}</div>`;
</script></body></html>"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("geometry")
    ap.add_argument("-o", "--out", default=None)
    a = ap.parse_args()
    d = json.load(open(a.geometry, encoding="utf-8"))
    r = compute(d)
    r.update(loadTable=LOAD_TABLE, snow=SNOW, wind=WIND, seis=SEIS, combos=COMBOS)
    html = (HTML.replace("__IMPORTMAP__", importmap_section())
                .replace("__PROJ__", CRITERIA["project"])
                .replace("__DATA__", json.dumps(r, ensure_ascii=False)))
    out = a.out or os.path.splitext(a.geometry)[0] + "_구조검토.html"
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    m = r["mass"]
    ng = sum(1 for c in r["columns"] if c["r"] > 1)
    print(f"기둥 {len(r['columns'])}개 · 재검토 {ng}개 · 최대 축력비 {max(c['r'] for c in r['columns']):.2f}")
    print(f"W = {m['W']:,.0f} kN, Cs = {m['Cs']:.4f}, V = {m['V']:,.0f} kN")
    print(f"→ {out}")


if __name__ == "__main__":
    main()
