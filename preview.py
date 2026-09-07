"""
preview.py — FreeCAD 없는 즉석 3D 미리보기 + 클릭 수정 루프

목표(프로그램 지향점 직결): "쉽고 빠르게 DXF → 3D".
  - geometry.json(또는 DXF) → 자립 preview.html 생성 → 브라우저에서 즉시 3D.
  - FreeCAD 불필요. three.js(CDN)로 벽/기둥/슬래브/개구부/MEP 압출 렌더.
  - 검출 결과를 카테고리·신뢰도 색으로 오버레이(single_offset=경고색) → '눈으로' 검증.
  - 요소 클릭 → 카테고리 재분류 / 치수 override / 삭제 → edits.json 다운로드.
    edits.json 은 EID 기반(element_id.apply_edits) → 재파싱 후에도 수정 보존(라운드트립).

사용:
  python preview.py geometry.json                       # 파싱된 json 미리보기
  python preview.py plan.dxf -m layer_map.csv -b block_map.csv   # DXF 즉시 미리보기
  python preview.py geometry.json -o preview.html --no-open

수정 반영(라운드트립):
  python dxf_parser.py plan.dxf -m layer_map.csv -o geometry.json --edits edits.json
"""
import argparse
import base64
import json
import os
import sys
import webbrowser

import geom_contract as _GC   # z 기준면 규약의 단일 출처


def _vendor_dir():
    """three.js 동봉 폴더. PyInstaller onefile 이면 sys._MEIPASS, 아니면 소스 dir."""
    base = getattr(sys, "_MEIPASS", None) or os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, "vendor")


# CDN 폴백(vendor 없을 때). 오프라인 현장이면 vendor/ 동봉으로 무인터넷 동작.
_CDN_IMPORTMAP = """<script type="importmap">
{ "imports": {
  "three": "https://unpkg.com/three@0.160.0/build/three.module.js",
  "three/addons/": "https://unpkg.com/three@0.160.0/examples/jsm/"
}}
</script>"""


def _data_url(path):
    with open(path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("ascii")
    return "data:text/javascript;base64," + b64


def importmap_section():
    """vendor/three.module.js + OrbitControls.js 있으면 base64 data-URL importmap
    (완전 오프라인 단일 HTML). 없으면 CDN importmap 폴백."""
    vd = _vendor_dir()
    three = os.path.join(vd, "three.module.js")
    orbit = os.path.join(vd, "OrbitControls.js")
    if os.path.exists(three) and os.path.exists(orbit):
        imports = {
            "three": _data_url(three),
            "three/addons/controls/OrbitControls.js": _data_url(orbit),
        }
        return ('<script type="importmap">\n'
                + json.dumps({"imports": imports})
                + "\n</script>")
    return _CDN_IMPORTMAP

if sys.stdout is not None and getattr(sys.stdout, "encoding", None) \
        and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


def _collect_xy(elements):
    """모든 요소 좌표 → bbox 중심 산출(카메라 프레이밍·재중심용)."""
    xs, ys = [], []
    for recs in elements.values():
        for r in recs:
            for key in ("centerline", "points"):
                for p in r.get(key, []) or []:
                    if isinstance(p, (list, tuple)) and len(p) >= 2:
                        xs.append(float(p[0])); ys.append(float(p[1]))
            c = r.get("center")
            if isinstance(c, (list, tuple)) and len(c) >= 2:
                xs.append(float(c[0])); ys.append(float(c[1]))
    if not xs:
        return [0.0, 0.0], [0.0, 0.0, 0.0, 0.0]
    bbox = [min(xs), min(ys), max(xs), max(ys)]
    center = [(bbox[0] + bbox[2]) / 2.0, (bbox[1] + bbox[3]) / 2.0]
    return center, bbox


def build_html(data):
    """geometry.json dict → 자립 HTML 문자열."""
    elements = data.get("elements", {})
    params = data.get("params", {})
    center, bbox = _collect_xy(elements)
    payload = {
        "elements": elements,
        "params": params,
        "floors": data.get("floors", []),
        "center": center,
        "bbox": bbox,
        "source": data.get("source", ""),
        "wall_pairing": data.get("wall_pairing", {}),
        "window_schedule": data.get("window_schedule", []),
        # ★ '왜 이렇게 나왔는지' 를 함께 싣는다. 종전엔 형상만 보냈고, 사용자가
        #   고칠 대상을 보고 있는 유일한 화면에 판단 근거가 하나도 없었다.
        "warnings": data.get("warnings", []),
        "thin_pairs": data.get("thin_pairs", {}),
        "width_conflicts": data.get("width_conflicts", []),
        "qa": data.get("qa", {}),
        "edits_report": data.get("edits_report", {}),
    }
    data_json = json.dumps(payload, ensure_ascii=False)
    # JS 안전: </script> 분리
    data_json = data_json.replace("</", "<\\/")
    html = _TEMPLATE.replace("/*__DATA__*/null", data_json)
    html = html.replace("<!--__IMPORTMAP__-->", importmap_section())
    # z 기준면 규약은 geom_contract 가 단독 정의한다. JS 는 import 가 불가하므로
    # 상수+헬퍼를 주입해서 쓴다 — 이 파일에서 규약을 다시 구현하면 안 된다.
    # (그렇게 재구현했다가 슬래브를 '하단'으로 해석해 보가 한 두께 떠 보인 적이 있다.)
    html = html.replace("/*__CONTRACT__*/", _GC.js_constants())
    return html


def load_data(path, layer_map=None, block_map=None):
    """입력이 .dxf 면 파싱, .json 이면 그대로 로드."""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".dxf":
        import dxf_parser as P
        rules = P.load_layer_map(layer_map) if layer_map else P.DEFAULT_LAYER_RULES
        brules = P.load_layer_map(block_map) if block_map else P.DEFAULT_BLOCK_RULES
        return P.parse(path, rules, brules)
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def main():
    ap = argparse.ArgumentParser(description="FreeCAD 없는 즉석 3D 미리보기")
    ap.add_argument("input", help="geometry.json 또는 .dxf")
    ap.add_argument("-m", "--map", default=None, help="layer_map.csv (DXF 입력 시)")
    ap.add_argument("-b", "--blockmap", default=None, help="block_map.csv (DXF 입력 시)")
    ap.add_argument("-o", "--out", default=None, help="출력 HTML 경로(기본 <입력>_preview.html)")
    ap.add_argument("--no-open", action="store_true", help="브라우저 자동 열기 비활성")
    args = ap.parse_args()

    data = load_data(args.input, args.map, args.blockmap)
    html = build_html(data)
    out = args.out or (os.path.splitext(args.input)[0] + "_preview.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)

    el = data.get("elements", {})
    counts = {k: len(v) for k, v in el.items() if v}
    print(f"미리보기 생성 -> {out}")
    print("  요소:", ", ".join(f"{k}={v}" for k, v in counts.items()) or "(없음)")
    print("  브라우저에서 마우스: 좌드래그=회전, 휠=줌, 우드래그=이동. 요소 클릭=수정.")
    if not args.no_open:
        webbrowser.open("file://" + os.path.abspath(out))


# ── 자립 HTML 템플릿 (three.js CDN) ──────────────────────────────────────────
_TEMPLATE = r"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>MEP Parser — 3D 미리보기</title>
<style>
  html,body{margin:0;height:100%;overflow:hidden;font-family:"Segoe UI",sans-serif;background:#1e2127;color:#e6e6e6}
  #app{display:flex;height:100%}
  #view{flex:1;position:relative}
  #panel{width:300px;background:#272b33;border-left:1px solid #3a3f4a;padding:14px;box-sizing:border-box;overflow-y:auto}
  #toolbar{position:absolute;top:10px;left:10px;z-index:10;display:flex;gap:6px;flex-wrap:wrap}
  button{background:#3a4150;color:#e6e6e6;border:1px solid #4a5364;border-radius:5px;padding:6px 10px;cursor:pointer;font-size:13px}
  button:hover{background:#46506280}
  button.active{background:#2d6cdf;border-color:#2d6cdf}
  #legend{position:absolute;bottom:10px;left:10px;z-index:10;background:#272b33cc;padding:8px 10px;border-radius:6px;font-size:12px;line-height:1.7}
  .sw{display:inline-block;width:11px;height:11px;border-radius:2px;margin-right:6px;vertical-align:middle}
  h2{font-size:15px;margin:0 0 10px}
  h3{font-size:13px;color:#9aa4b2;margin:16px 0 6px;text-transform:uppercase;letter-spacing:.5px}
  .row{margin:7px 0;font-size:13px}
  .row label{display:block;color:#9aa4b2;margin-bottom:3px}
  select,input{width:100%;box-sizing:border-box;background:#1e2127;color:#e6e6e6;border:1px solid #4a5364;border-radius:4px;padding:6px}
  .muted{color:#7a8290;font-size:12px}
  .kv{display:flex;justify-content:space-between;font-size:12px;margin:3px 0}
  .kv span:first-child{color:#9aa4b2}
  #editbox{display:none}
  #dl{width:100%;margin-top:14px;background:#2d8a4e;border-color:#2d8a4e}
  #dl:hover{background:#34a05b}
  .badge{display:inline-block;padding:1px 6px;border-radius:3px;font-size:11px;margin-left:6px}
  #err{position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);text-align:center;color:#ff8080;display:none}
#tabs{position:absolute;left:8px;top:40px;z-index:5;display:flex;gap:4px;align-items:center;
      flex-wrap:wrap;max-width:calc(100% - 16px)}
#tabs button{background:#2a2f38;color:#cfd6e4;border:1px solid #3a4150;border-radius:4px;
      padding:3px 9px;cursor:pointer;font-size:12px}
#tabs button.on{background:#4b7bd5;color:#fff;border-color:#4b7bd5}
#tools2d{display:none;gap:4px;align-items:center}
#tools2d.on{display:flex}
#hint2d{font-size:11px;margin-left:4px}
#view2d{display:none;position:absolute;inset:0;width:100%;height:100%;background:#12151a}
#view2d.on{display:block}
#view2d .w{stroke:#6f9fe0;fill:none;stroke-linecap:butt}
#view2d .w.sel{stroke:#ffd24a}
#view2d .w.rev{stroke:#e07b7b}
#view2d .w.man{stroke:#5fd08a}
#view2d .w.del{stroke:#555;stroke-dasharray:6 6}
#view2d .col{fill:#c060c0;opacity:.75}
#view2d .op{fill:#e08040;opacity:.85}
#view2d .hnd{fill:#ffd24a;stroke:#000;stroke-width:1;cursor:grab}
#view2d .snap{fill:none;stroke:#5fd08a;stroke-width:2}
.why{margin:3px 0;padding:4px 6px;background:#20242b;border-left:2px solid #556;
     border-radius:2px;font-size:12px;line-height:1.45}
#warnbox{max-height:150px;overflow:auto;font-size:12px;line-height:1.5}
#warnbox div{margin:2px 0;padding:3px 5px;background:#20242b;border-radius:2px}
</style>
</head>
<body>
<div id="app">
  <div id="view">
    <div id="toolbar">
      <button id="b3d" class="active">3D</button>
      <button id="btop">평면(Top)</button>
      <button id="bfit">맞춤(Fit)</button>
      <button id="bconf">신뢰도 색</button>
      <button id="bwire">와이어</button>
      <button id="bplace">창호 배치: OFF</button>
    </div>
    <div id="tabs">
      <button id="t3d" class="on">3D 보기</button>
      <button id="t2d">평면 편집</button>
      <span id="tools2d">
        <button data-m="select" class="on">선택/이동</button>
        <button data-m="split">분할</button>
        <button data-m="join">결합</button>
        <button data-m="draw">벽 그리기</button>
        <span id="hint2d" class="muted"></span>
      </span>
    </div>
    <svg id="view2d"><g id="g2d"></g></svg>
    <div id="legend"></div>
    <div id="err">three.js 로드 실패. (CDN 모드면 인터넷 필요 · 오프라인 모드면 vendor/ 동봉 확인)</div>
  </div>
  <div id="panel">
    <h2>3D 미리보기 <span class="muted" id="src"></span></h2>
    <div id="counts"></div>
    <h3>선택한 요소</h3>
    <div id="noSel" class="muted">요소를 클릭하세요.</div>
    <div id="editbox">
      <div class="kv"><span>EID</span><span id="e_eid"></span></div>
      <div class="kv"><span>레이어</span><span id="e_layer"></span></div>
      <div class="kv"><span>신뢰도</span><span id="e_conf"></span></div>
      <div id="e_why"></div>
      <div class="row"><label>카테고리</label>
        <select id="e_cat"></select></div>
      <div class="row"><label>폭/지름 (mm)</label><input id="e_w" type="number"/></div>
      <div class="row"><label>높이/두께 (mm)</label><input id="e_h" type="number"/></div>
      <div class="row"><label><input type="checkbox" id="e_del" style="width:auto"/> 삭제</label></div>
      <button id="apply">수정 적용</button>
      <div id="bulk"></div>
    </div>
    <h3>창호 배치 (반자동)</h3>
    <div id="schedwrap">
      <div class="row"><label>창호일람 선택</label>
        <select id="sched"></select></div>
      <div class="muted" id="placehint">‘창호 배치’ 켠 뒤 벽을 클릭하면 그 위치에 배치됩니다.</div>
    </div>
    <h3>이 도면에서 파서가 말한 것 (<span id="nwarn">0</span>)</h3>
    <div id="warnbox" class="muted">없음</div>
    <h3>수정 목록 (<span id="nedits">0</span>)</h3>
    <div id="editlist" class="muted">없음</div>
    <button id="dl">edits.json 다운로드</button>
    <div class="muted" style="margin-top:8px">재파싱 시:<br>
      <code style="font-size:11px">dxf_parser.py plan.dxf -o geometry.json --edits edits.json</code></div>
  </div>
</div>

<!--__IMPORTMAP__-->
<script type="module">
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';

const DATA = /*__DATA__*/null;
window.addEventListener('error', e => {
  if (String(e.message).includes('three') || String(e.filename).includes('unpkg'))
    document.getElementById('err').style.display='block';
});

/*__CONTRACT__*/

const S = 0.001;                  // mm -> m
const CX = DATA.center[0], CY = DATA.center[1];
const P = DATA.params || {};
const wallH = (P.wall && P.wall.height) || 2800;
const colH  = (P.column && P.column.height) || 3000;
const slabT = (P.slab && P.slab.thickness) || 200;

const CAT_COLOR = {
  wall:0x6b8fb5, column:0xc77dff, slab:0x8d99ae, beam:0x5a9aa8, zone:0x495057,
  opening:0xe85d5d, pipe:0x4cc9f0, duct:0xf4a261, tray:0x90be6d, equipment:0xf9c74f
};
const PAIR_COLOR = { paired:0x4caf50, single:0xff9800, single_offset:0xf44336, closed:0x26a69a };

const scene = new THREE.Scene();
scene.background = new THREE.Color(0x1e2127);
const view = document.getElementById('view');
const W = () => view.clientWidth, H = () => view.clientHeight;
const cam = new THREE.PerspectiveCamera(45, W()/H(), 0.1, 100000);
cam.up.set(0,0,1);                // Z-up (도면 X,Y 평면 + Z 높이)
const renderer = new THREE.WebGLRenderer({antialias:true});
renderer.setSize(W(), H()); renderer.setPixelRatio(devicePixelRatio);
view.appendChild(renderer.domElement);
const controls = new OrbitControls(cam, renderer.domElement);
controls.screenSpacePanning = false;

scene.add(new THREE.HemisphereLight(0xffffff, 0x404050, 1.1));
const dl = new THREE.DirectionalLight(0xffffff, 0.7); dl.position.set(1,1,2); scene.add(dl);
const grid = new THREE.GridHelper(200, 40, 0x3a3f4a, 0x2c3038);
grid.rotation.x = Math.PI/2; scene.add(grid);

const toM = p => [ (p[0]-CX)*S, (p[1]-CY)*S ];
const meshes = [];
let useConf = false, wire = false;

function colorFor(cat, rec){
  if (useConf && cat==='wall') return PAIR_COLOR[rec.pairing] ?? CAT_COLOR.wall;
  return CAT_COLOR[cat] ?? 0xaaaaaa;
}
function addMesh(geo, cat, rec, z, opts={}){
  const mat = new THREE.MeshLambertMaterial({
    color: colorFor(cat, rec),
    transparent: !!opts.trans, opacity: opts.trans ? (opts.op??0.35) : 1.0,
    wireframe: wire
  });
  const m = new THREE.Mesh(geo, mat);
  m.position.z += z||0;
  m.userData = { cat, rec, baseColor: mat.color.getHex() };
  scene.add(m); meshes.push(m); return m;
}

// 벽: centerline a-b, width, height → 배향 박스
// z 범위는 gcZRange(주입된 geom_contract 규약)만 사용한다.
function buildWall(rec){
  const cl = rec.centerline || rec.points; if(!cl||cl.length<2) return;
  const w = gcWidthOf(rec, P, 'wall')*S;   // 규약은 geom_contract 단독
  const zr = gcZRange('wall', rec, P);
  const z0 = zr[0]*S, h = (zr[1]-zr[0])*S;
  for(let i=0;i<cl.length-1;i++){
    const a=toM(cl[i]), b=toM(cl[i+1]);
    const dx=b[0]-a[0], dy=b[1]-a[1]; const len=Math.hypot(dx,dy); if(len<1e-6) continue;
    const geo=new THREE.BoxGeometry(len, Math.max(w,0.02), h);
    const m=addMesh(geo, 'wall', rec, z0+h/2);
    m.position.x=(a[0]+b[0])/2; m.position.y=(a[1]+b[1])/2;
    m.rotation.z=Math.atan2(dy,dx);
  }
}
function shapeFrom(pts){
  const s=new THREE.Shape();
  pts.forEach((p,i)=>{ const q=toM(p); i?s.lineTo(q[0],q[1]):s.moveTo(q[0],q[1]); });
  return s;
}
function buildColumn(rec){
  const zr = gcZRange('column', rec, P);
  const z0 = zr[0]*S, h = (zr[1]-zr[0])*S;
  if(rec.kind==='circle'){
    const r=(rec.radius||200)*S; const geo=new THREE.CylinderGeometry(r,r,h,24);
    geo.rotateX(Math.PI/2); const m=addMesh(geo,'column',rec,z0+h/2);
    const c=toM(rec.center||[0,0]); m.position.x=c[0]; m.position.y=c[1]; return;
  }
  const pts=rec.points||[]; if(pts.length<3) return;
  const geo=new THREE.ExtrudeGeometry(shapeFrom(pts), {depth:h, bevelEnabled:false});
  addMesh(geo,'column',rec,z0);
}
function buildSlab(rec){
  const pts=rec.points||[]; if(pts.length<3) return;
  // 보(ifc_type=Beam)와 슬래브는 둘 다 'top' 기준이지만 기본 치수가 다르다.
  const cat = (rec.overrides?.ifc_type === 'Beam') ? 'beam' : 'slab';
  const zr = gcZRange(cat, rec, P);
  const z0 = zr[0]*S, t = (zr[1]-zr[0])*S;
  const geo=new THREE.ExtrudeGeometry(shapeFrom(pts), {depth:t, bevelEnabled:false});
  addMesh(geo,'slab',rec,z0);   // ExtrudeGeometry 는 0..t 로 +Z 압출 → 아랫면을 z0 에
}
// 보: 축선 + 단면(폭 b × 춤 h). footprint 규칙은 gcBeamRings(주입) 이 단독 소유 —
// 빌더와 같은 식을 쓰지 않으면 D6(규약 재구현)이 그대로 재현된다.
function buildBeam(rec){
  const zr = gcZRange('beam', rec, P);
  const z0 = zr[0]*S, d = (zr[1]-zr[0])*S;
  if(d<=0) return;
  for(const ring of gcBeamRings(rec, P)){
    if(!ring || ring.length<3) continue;
    const geo=new THREE.ExtrudeGeometry(shapeFrom(ring), {depth:d, bevelEnabled:false});
    addMesh(geo,'beam',rec,z0);
  }
}
function buildZone(rec){
  const pts=rec.points||[]; if(pts.length<3) return;
  const geo=new THREE.ExtrudeGeometry(shapeFrom(pts), {depth:0.02, bevelEnabled:false});
  addMesh(geo,'zone',rec,(rec.z_base||0)*S, {trans:true, op:0.18});
}
function buildOpening(rec){
  const c=rec.center; if(!c) return;
  const z0=(rec.z_base||0)*S;
  const isWin = rec.subtype==='window';
  const wmm = rec.width ?? (rec.radius? rec.radius*2 : 900);
  const hmm = rec.height ?? (isWin?1200:2100);
  const sill = (isWin ? (rec.sill??900) : 0) + (rec.z_base||0);
  const depth = (rec.host_width ?? (P.wall?.width) ?? 250);
  const dir = rec.host_dir || [1,0];
  const geo=new THREE.BoxGeometry(wmm*S, depth*S, hmm*S);
  const m=addMesh(geo,'opening',rec,(sill+hmm/2)*S, {trans:true, op:0.5});
  const q=toM(c); m.position.x=q[0]; m.position.y=q[1];
  m.position.z=(sill+hmm/2)*S; m.rotation.z=Math.atan2(dir[1],dir[0]);
}
function buildMepLinear(rec, cat){
  const pts=rec.points||rec.centerline; if(!pts||pts.length<2) return;
  const elev=(rec.elevation||0)*S;
  const dia=(rec.diameter|| (rec.width_mm? Math.max(rec.width_mm,rec.height_mm||rec.width_mm):100));
  for(let i=0;i<pts.length-1;i++){
    const a=toM(pts[i]), b=toM(pts[i+1]);
    const dx=b[0]-a[0], dy=b[1]-a[1]; const len=Math.hypot(dx,dy); if(len<1e-6) continue;
    let geo;
    if(cat==='pipe'){ const r=dia*S/2; geo=new THREE.CylinderGeometry(r,r,len,16); geo.rotateZ(Math.PI/2); }
    else { const wd=(rec.width_mm||300)*S, ht=(rec.height_mm||150)*S; geo=new THREE.BoxGeometry(len,wd,ht); }
    const m=addMesh(geo,cat,rec,elev);
    m.position.x=(a[0]+b[0])/2; m.position.y=(a[1]+b[1])/2; m.position.z=elev;
    m.rotation.z=Math.atan2(dy,dx);
  }
}
function buildEquip(rec){
  const pts=rec.points||[]; const z0=(rec.elevation||rec.z_base||0)*S;
  if(pts.length>=3){ const geo=new THREE.ExtrudeGeometry(shapeFrom(pts),{depth:1.0,bevelEnabled:false}); addMesh(geo,'equipment',rec,z0); }
}

function rebuild(){
  for(const m of meshes){ scene.remove(m); m.geometry.dispose(); m.material.dispose(); }
  meshes.length=0;
  const E=DATA.elements;
  (E.slab||[]).forEach(buildSlab);
  (E.beam||[]).forEach(buildBeam);
  (E.zone||[]).forEach(buildZone);
  (E.wall||[]).forEach(buildWall);
  (E.column||[]).forEach(buildColumn);
  (E.opening||[]).forEach(buildOpening);
  (E.pipe||[]).forEach(r=>buildMepLinear(r,'pipe'));
  (E.duct||[]).forEach(r=>buildMepLinear(r,'duct'));
  (E.tray||[]).forEach(r=>buildMepLinear(r,'tray'));
  (E.equipment||[]).forEach(buildEquip);
  applyEditVisuals();
}

function fit(){
  const box=new THREE.Box3(); meshes.forEach(m=>box.expandByObject(m));
  if(box.isEmpty()){ cam.position.set(20,-20,20); controls.target.set(0,0,0); controls.update(); return; }
  const c=box.getCenter(new THREE.Vector3()), sz=box.getSize(new THREE.Vector3());
  const r=Math.max(sz.x,sz.y,sz.z)*0.7+1;
  cam.position.set(c.x+r, c.y-r, c.z+r); controls.target.copy(c); controls.update();
}
function topView(){
  const box=new THREE.Box3(); meshes.forEach(m=>box.expandByObject(m));
  const c=box.getCenter(new THREE.Vector3()), sz=box.getSize(new THREE.Vector3());
  const r=Math.max(sz.x,sz.y)*0.75+1;
  cam.position.set(c.x, c.y, c.z+r*2); controls.target.copy(c); controls.update();
}

// ── 선택/수정 ──────────────────────────────────────────────
const ray=new THREE.Raycaster(), mouse=new THREE.Vector2();
let selected=null;
// 검토 사유 → 사람이 읽을 수 있는 한 줄 + 조치. 코드 한 단어만 보여 주면
// 사용자는 무엇을 볼지 모른다(실측: 검토 대상 180개 중 154개가 사유 없음이었다).
const REASON = {
  'thin_pair': '두께가 이 레이어 중앙값의 1/3 미만 — 벽면 옆 마감선과 짝지었을 수 있다. '
             + '실제 두께가 맞으면 layer_map 에 opts pair_min 을 주면 통과한다.',
  'single': '반대편 면선을 못 찾아 이 선 하나를 중심선으로 썼다 — 두께는 기본값이다.',
  'single_offset': '반대편 면선이 없어 중심선을 폭의 절반만큼 밀어 추정했다 — '
                 + '위치·두께 둘 다 추정치다.',
  'closed': '닫힌 폴리선을 그대로 압출했다(면선 페어링을 거치지 않음).',
  'axis': '치수선(DIMENSION)에서 뽑은 축선이다 — 단면은 부재일람표에서 온다.',
};

const edits = {};   // eid -> {category?, overrides?, deleted?}
const CATS=['wall','column','slab','beam','zone','opening','pipe','duct','tray','equipment'];
const sel=document.getElementById('e_cat'); CATS.forEach(c=>{const o=document.createElement('option');o.value=o.textContent=c;sel.appendChild(o);});

renderer.domElement.addEventListener('click', ev=>{
  const r=renderer.domElement.getBoundingClientRect();
  mouse.x=((ev.clientX-r.left)/r.width)*2-1; mouse.y=-((ev.clientY-r.top)/r.height)*2+1;
  ray.setFromCamera(mouse,cam);
  const hit=ray.intersectObjects(meshes)[0];
  if(placeMode){
    if(hit && hit.object.userData.cat==='wall'){ placeWindow(hit.object, hit.point); }
    else { document.getElementById('placehint').textContent='⚠ 벽을 클릭하세요(창호는 벽 위에만 배치).'; }
    return;
  }
  select(hit?hit.object:null);
});
function select(m){
  if(selected) selected.material.emissive?.setHex(0x000000);
  selected=m;
  if(!m){ fillPanel(null, null); return; }
  m.material.emissive?.setHex(0x333300);
  fillPanel(m.userData.rec, m.userData.cat);
}

// 3D 와 2D 평면 탭이 **같은 패널을 공유**한다. 뷰마다 인스펙터를 따로 두면
// 필드가 어긋나기 시작하고, 그게 이 저장소가 반복해 밟은 종류의 버그다.
let selRec=null, selCat=null;
function fillPanel(rec, cat){
  const eb=document.getElementById('editbox'), ns=document.getElementById('noSel');
  selRec=rec; selCat=cat;
  if(!rec){ eb.style.display='none'; ns.style.display='block'; return; }
  eb.style.display='block'; ns.style.display='none';
  const eid=rec.eid||'(eid 없음)';
  document.getElementById('e_eid').textContent=eid;
  document.getElementById('e_layer').textContent=rec.layer||'-';
  document.getElementById('e_conf').textContent=(rec.confidence!=null?rec.confidence:'-')+(rec.pairing?' / '+rec.pairing:'');
  sel.value=(edits[eid]?.category)||cat;
  document.getElementById('e_w').value=edits[eid]?.overrides?.width ?? rec.overrides?.width ?? rec.width_detected ?? rec.width ?? '';
  document.getElementById('e_h').value=edits[eid]?.overrides?.height ?? rec.overrides?.height ?? rec.overrides?.thickness ?? '';
  document.getElementById('e_del').checked=!!edits[eid]?.deleted;
  document.getElementById('e_why').innerHTML=whyHtml(rec, cat);
  document.getElementById('bulk').innerHTML=bulkHtml(rec, cat);
}

// 일괄 수정은 **규칙**이다 — 개별 EID 41개가 아니라 layer_map 한 줄이어야 한다.
// 그래야 도면이 바뀌어도 계속 적용되고, diff 에 남고, 고아가 되지 않는다.
// 자동으로 쓰지 않는다: 붙여 넣을 줄을 만들어 줄 뿐이다("사람이 CSV 를 쓴다").
function bulkHtml(rec, cat){
  if(!rec || cat!=='wall' || !rec.layer) return '';
  const w=rec.width_detected;
  if(w==null) return '';
  const same=(DATA.elements.wall||[]).filter(r=>
    r.layer===rec.layer && Math.abs((r.width_detected||-1)-w)<0.5);
  if(same.length<2) return '';
  const esc=t=>String(t).replace(new RegExp('[.*+?^${}()|[\\]]','g'), m=>'\\'+m);
  const row=esc(rec.layer)+',wall,'+Math.round(w)+','
          +Math.round(gcDim('wall','height',rec,P))+',,';
  return '<div class="why">같은 조건(<b>'+rec.layer+'</b> · 실측 '+Math.round(w)
    +'mm)인 벽이 <b>'+same.length+'</b>개입니다. 개별 수정 대신 layer_map 에'
    + ' 이 줄을 넣으면 규칙으로 남습니다:'
    + '<div style="margin-top:4px"><code id="bulkrow">'+row+'</code> '
    + '<button id="bulkcopy" style="width:auto;padding:2px 8px">복사</button></div></div>';
}
document.getElementById('bulk').addEventListener('click', ev=>{
  if(ev.target.id!=='bulkcopy') return;
  const t=document.getElementById('bulkrow').textContent;
  navigator.clipboard?.writeText(t);
  ev.target.textContent='복사됨';
  setTimeout(()=>{ ev.target.textContent='복사'; }, 1200);
});

// 이 부재가 왜 이 모양인지. 파서가 아는 것을 사용자가 보는 자리로 가져온다.
function whyHtml(rec, cat){
  const out=[];
  if(rec.needs_review){
    const r=rec.review_reason||'';
    out.push('<b style="color:#e66">검토 필요</b> '+(REASON[r]||r||
      '사유가 기록되지 않았다'));
  }
  if(rec.review_resolved) out.push('<span style="color:#6a6">검토 완료로 표시됨</span>');
  const wd=rec.width_detected, ov=rec.overrides?.width;
  if(wd!=null&&ov!=null&&Math.abs(wd-ov)>Math.max(20,ov*0.15))
    out.push('두께: 도면 실측 <b>'+Math.round(wd)+'</b>mm ≠ layer_map 선언 <b>'
      +Math.round(ov)+'</b>mm → <b>'+Math.round(gcWidthOf(rec,P,cat))+'</b>mm 로 세워진다');
  else if(wd!=null) out.push('실측 두께 '+Math.round(wd)+'mm');
  if(rec.dims_assumed?.length)
    out.push('<b style="color:#e93">추정치</b> '+rec.dims_assumed.join(', ')
      +' — 평면도에 없는 값이라 기본값을 넣었다');
  if(rec.section?.size) out.push('일람표 단면 '+rec.section.name+' '+rec.section.size);
  if(rec.schedule_match&&rec.schedule_match!=='ok')
    out.push('<b style="color:#e66">일람표 미매칭</b> '+rec.schedule_match);
  return out.length?out.map(x=>'<div class="why">'+x+'</div>').join(''):'';
}
document.getElementById('apply').addEventListener('click', ()=>{
  const rec=selRec; if(!rec) return; const eid=rec.eid; if(!eid){alert('이 요소는 EID가 없어 수정 저장 불가');return;}
  const e=edits[eid]={}; const cat=sel.value;
  if(cat!==selCat) e.category=cat;
  const w=parseFloat(document.getElementById('e_w').value), h=parseFloat(document.getElementById('e_h').value);
  const ov={}; if(!isNaN(w)) ov.width=w; if(!isNaN(h)){ (cat==='slab')?ov.thickness=h:ov.height=h; }
  if(Object.keys(ov).length) e.overrides=ov;
  if(document.getElementById('e_del').checked) e.deleted=true;
  // 사용자가 손을 댔으면 '봤다' 는 뜻이다. 이걸 안 남기면 이미 고친 부재가
  // NeedsReview=True 로 IFC 까지 나가고, 검토 목록이 줄지 않는다.
  if(rec.needs_review && !e.deleted) e.review_resolved=true;
  // 어디를 고쳤는지 좌표를 남긴다 — 나중에 grouping 이 바뀌어 고아가 됐을 때
  // '어느 벽이었나' 를 되찾는 유일한 단서다(해시만으로는 못 찾는다).
  const _cl=rec.centerline||rec.points; if(_cl&&_cl.length) e._at=[_cl[0][0],_cl[0][1]];
  if(!Object.keys(e).filter(k=>k[0]!=='_').length) delete edits[eid];
  refreshEdits(); applyEditVisuals();
});
// 되돌리기 — edits 는 평평한 객체라 스냅샷 스택 하나면 충분하다.
// 시간 디바운스가 아니라 '한 동작 = 한 단계' 다. 무동작(값이 같음)은 쌓지 않는다.
const UNDO=[]; let _lastSnap=JSON.stringify(edits);
function pushUndo(){
  const cur=JSON.stringify(edits);
  if(cur===_lastSnap) return;            // 바뀐 게 없으면 단계를 만들지 않는다
  UNDO.push(_lastSnap); _lastSnap=cur;
  if(UNDO.length>100) UNDO.shift();
}
function undo(){
  if(!UNDO.length) return;
  const prev=UNDO.pop();
  for(const k of Object.keys(edits)) delete edits[k];
  Object.assign(edits, JSON.parse(prev));
  _lastSnap=prev; saveEdits(); refreshEditsOnly(); applyEditVisuals();
}
window.addEventListener('keydown', ev=>{
  if((ev.ctrlKey||ev.metaKey) && ev.key.toLowerCase()==='z'){ ev.preventDefault(); undo(); }
});

// 브라우저 새로고침·크래시로 오후 작업이 날아가지 않게. 도면별로 따로 둔다.
const LSKEY='mepEdits:'+(DATA.source||'');
function saveEdits(){ try{ localStorage.setItem(LSKEY, JSON.stringify(edits)); }catch(e){} }
function loadEdits(){
  try{
    const raw=localStorage.getItem(LSKEY); if(!raw) return 0;
    const o=JSON.parse(raw); let n=0;
    for(const [k,v] of Object.entries(o)){ edits[k]=v; n++; }
    return n;
  }catch(e){ return 0; }
}

function refreshEdits(){
  pushUndo(); saveEdits(); refreshEditsOnly();
}
function refreshEditsOnly(){
  const n=Object.keys(edits).length; document.getElementById('nedits').textContent=n;
  const el=document.getElementById('editlist');
  el.innerHTML = n? Object.entries(edits).map(([k,v])=>{
    if(v.added){ const r=v.record||{};
      // 추가된 것이 창호만은 아니다 — 2D 탭이 벽도 만든다(이동·분할·결합은 전부
      // delete+add 다). 종전엔 전부 '창호 0×0' 으로 찍혀 무엇을 만들었는지 몰랐다.
      let lbl;
      if((v.category||'opening')==='wall'){
        const cl=r.centerline||r.points||[];
        const L=cl.length>1?Math.hypot(cl[cl.length-1][0]-cl[0][0],
                                       cl[cl.length-1][1]-cl[0][1]):0;
        lbl='벽 '+Math.round(L)+'mm · 두께 '+Math.round(r.overrides?.width||0);
      } else {
        lbl=(r.mark||'창호')+' '+Math.round(r.width||0)+'×'+Math.round(r.height||0);
      }
      return `<div class="kv"><span>➕ ${lbl}</span>`
           + `<span><a href="#" class="rm" data-eid="${k}" style="color:#ff8080">✕ 제거</a></span></div>`; }
    const tag=v.deleted?'🗑삭제':(v.category?('→'+v.category):'')+(v.overrides?(' '+JSON.stringify(v.overrides)):'');
    return `<div class="kv"><span>${k}</span><span>${tag}</span></div>`;
  }).join('') : '없음';
}
function removeAdded(eid){
  delete edits[eid];
  // 씬 메시 제거
  for(let i=meshes.length-1;i>=0;i--){ if(meshes[i].userData.rec.eid===eid){
    const m=meshes[i]; scene.remove(m); m.geometry.dispose(); m.material.dispose(); meshes.splice(i,1); } }
  // 데이터 모델에서 제거
  if(DATA.elements.opening) DATA.elements.opening=DATA.elements.opening.filter(o=>o.eid!==eid);
  if(typeof sel2!=='undefined'){ sel2=sel2.filter(x=>x!==eid); if(typeof render2==='function') render2(); }
  refreshEdits(); applyEditVisuals();
}
document.getElementById('editlist').addEventListener('click', ev=>{
  const a=ev.target.closest('a.rm'); if(a){ ev.preventDefault(); removeAdded(a.dataset.eid); }
});
// ── 2D 평면 편집 ───────────────────────────────────────────────────────────
// 기하 편집은 여기서만 한다. 3D 는 보기 전용이다 — 원근 투사에서는 끝점을 정확히
// 집을 수 없고, 건축 편집은 본래 평면이 정확하다(스냅·직각·치수).
//
// 이동·분할·결합은 **새 동사를 만들지 않는다**. 전부 delete + add 로 표현한다:
//   이동 = 원본 삭제 + 옮긴 좌표로 추가
//   분할 = 원본 삭제 + 두 조각 추가
//   결합 = 두 원본 삭제 + 하나 추가
// apply_edits 가 이미 그 둘을 안다. 네 번째 동사를 만들면 파서·빌더·미리보기가
// 전부 그걸 배워야 한다.
const SVG2NS='http://www.w3.org/2000/svg';
const svg2=document.getElementById('view2d'), g2=document.getElementById('g2d');
let mode2='select', sel2=[], vb=null, dragging=null, drawFrom=null;
const SNAP_PX=12;          // 스냅은 화면 기준(집기 편하게)
const JOIN_TOL_MM=600;     // 결합은 실치수 기준(줌과 무관해야 한다)
const HINT2={select:'벽을 클릭 → 끝점을 끌어 옮깁니다. Delete 로 삭제.',
             split:'벽 위의 나눌 지점을 클릭합니다.',
             join:'이어 붙일 벽 2개를 차례로 클릭합니다.',
             draw:'시작점과 끝점을 클릭해 벽을 그립니다.'};

function wallsAll(){
  const out=(DATA.elements.wall||[]).map(r=>r);
  for(const [k,v] of Object.entries(edits))
    if(v.added && (v.category||'wall')==='wall' && v.record) out.push(v.record);
  return out;
}
function clOf(r){ return r.centerline || r.points || []; }
function isDel(r){ return !!(edits[r.eid] && edits[r.eid].deleted); }
function recByEid(eid){ return wallsAll().find(r=>r.eid===eid); }

function fitView(){
  const b=DATA.bbox; if(!b) return;
  const pad=(b[2]-b[0]+b[3]-b[1])*0.03+500;
  vb={x:b[0]-pad, y:b[1]-pad, w:(b[2]-b[0])+pad*2, h:(b[3]-b[1])+pad*2};
  applyVB();
}
function applyVB(){
  // y 를 뒤집는다 — 도면은 위가 +y, SVG 는 아래가 +y.
  svg2.setAttribute('viewBox', vb.x+' '+(-(vb.y+vb.h))+' '+vb.w+' '+vb.h);
  g2.setAttribute('transform','scale(1,-1)');
}
function px2world(){ const r=svg2.getBoundingClientRect(); return vb.w/Math.max(1,r.width); }
function evtWorld(ev){
  const r=svg2.getBoundingClientRect();
  return [vb.x+(ev.clientX-r.left)*(vb.w/Math.max(1,r.width)),
          vb.y+(r.height-(ev.clientY-r.top))*(vb.h/Math.max(1,r.height))];
}
function mk2(t,a){ const e=document.createElementNS(SVG2NS,t);
  for(const k in a) e.setAttribute(k,a[k]); return e; }

function render2(){
  if(!vb) fitView();
  while(g2.firstChild) g2.removeChild(g2.firstChild);
  for(const r of (DATA.elements.slab||[]))
    if((r.points||[]).length>2) g2.appendChild(mk2('polygon',
      {points:r.points.map(p=>p[0]+','+p[1]).join(' '), fill:'#1b2029', stroke:'#2b3240'}));
  for(const r of (DATA.elements.column||[])){
    if(r.kind==='circle') g2.appendChild(mk2('circle',
      {cx:r.center[0],cy:r.center[1],r:r.radius,class:'col'}));
    else if((r.points||[]).length>2) g2.appendChild(mk2('polygon',
      {points:r.points.map(p=>p[0]+','+p[1]).join(' '), class:'col'}));
  }
  for(const r of (DATA.elements.opening||[])){
    const c=r.center;
    if(c) g2.appendChild(mk2('circle',
      {cx:c[0],cy:c[1],r:Math.max(120,r.radius||150),class:'op'}));
  }
  for(const rec of wallsAll()){
    const cl=clOf(rec); if(cl.length<2) continue;
    const w=Math.max(30, gcWidthOf(rec,P,'wall'));
    const cls=['w'];
    if(isDel(rec)) cls.push('del');
    else if(sel2.indexOf(rec.eid)>=0) cls.push('sel');
    else if(rec.pairing==='manual') cls.push('man');
    else if(rec.needs_review) cls.push('rev');
    const e=mk2('polyline',{points:cl.map(p=>p[0]+','+p[1]).join(' '),
                            class:cls.join(' '),'stroke-width':w});
    e.dataset.eid=rec.eid; g2.appendChild(e);
  }
  const k=px2world();
  for(const rec of wallsAll()){
    if(sel2.indexOf(rec.eid)<0 || isDel(rec)) continue;
    const cl=clOf(rec);
    for(const i of [0, cl.length-1]){
      const h=mk2('circle',{cx:cl[i][0],cy:cl[i][1],r:SNAP_PX*k*0.6,class:'hnd'});
      h.dataset.eid=rec.eid; h.dataset.i=i; g2.appendChild(h);
    }
  }
  document.getElementById('hint2d').textContent = HINT2[mode2] || '';
}

function hashId2(str){ let h=0;
  for(let i=0;i<str.length;i++){ h=(h*31+str.charCodeAt(i))|0; }
  return (h>>>0).toString(16).padStart(8,'0'); }

function manualWall(a, b, src){
  const rec={kind:'polyline', closed:false,
    points:[a.slice(),b.slice()], centerline:[a.slice(),b.slice()],
    pairing:'manual', layer:(src&&src.layer)||'(수동)',
    z_base:(src&&src.z_base)||0, confidence:1, needs_review:false,
    source:'manual_preview',
    overrides:Object.assign({}, src&&src.overrides,
      {width:Math.round(src?gcWidthOf(src,P,'wall'):((P.wall&&P.wall.width)||200))})};
  rec.eid='wm:'+hashId2(a.map(Math.round).join('_')+'|'+b.map(Math.round).join('_'));
  return rec;
}
function addWall(rec){ edits[rec.eid]={added:true, category:'wall', record:rec}; }
function delWall(rec){
  if(edits[rec.eid] && edits[rec.eid].added) delete edits[rec.eid];
  else edits[rec.eid]=Object.assign({}, edits[rec.eid], {deleted:true});
}

// 끝점 스냅 — 수동 벽은 파서의 코너 스냅·junction 치유를 건너뛴다(주입이 그 뒤라서).
// 그래서 여기서 사람이 **보면서** 붙인다. 파서가 나중에 몰래 옮기는 것보다 낫다.
function snapPoint(pt, skipEid){
  const tol=SNAP_PX*px2world(); let best=null, bd=tol;
  for(const rec of wallsAll()){
    if(rec.eid===skipEid || isDel(rec)) continue;
    for(const q of clOf(rec)){
      const d=Math.hypot(q[0]-pt[0], q[1]-pt[1]);
      if(d<bd){ bd=d; best=[q[0],q[1]]; }
    }
  }
  return best || pt;
}

function splitWall(rec, pt){
  const cl=clOf(rec); if(cl.length<2) return;
  const a=cl[0], b=cl[cl.length-1];
  const dx=b[0]-a[0], dy=b[1]-a[1], L2=dx*dx+dy*dy; if(L2<1) return;
  let t=((pt[0]-a[0])*dx+(pt[1]-a[1])*dy)/L2;
  t=Math.max(0.02, Math.min(0.98, t));
  const m=[a[0]+dx*t, a[1]+dy*t];
  const w1=manualWall(a,m,rec), w2=manualWall(m,b,rec);
  delWall(rec); addWall(w1); addWall(w2); sel2=[w1.eid,w2.eid];
  refreshEdits(); fillPanel(w1,'wall'); render2();
}

function joinWalls(r1, r2){
  sel2=[];
  if(!r1 || !r2 || r1===r2){ render2(); return; }
  const A=clOf(r1), B=clOf(r2);
  const ends=[[A[0],A[A.length-1]],[B[0],B[B.length-1]]];
  let best=null, bd=Infinity;
  for(let i=0;i<2;i++) for(let j=0;j<2;j++){
    const d=Math.hypot(ends[0][i][0]-ends[1][j][0], ends[0][i][1]-ends[1][j][1]);
    if(d<bd){ bd=d; best=[i,j]; }
  }
  // 멀면 붙이지 않는다 — 조용히 엉뚱한 벽을 만드느니 안 되는 게 낫다.
  // 허용치는 **화면 배율과 무관한 실치수**다. 픽셀 기준으로 두면 같은 조작이
  // 줌에 따라 다르게 동작한다(멀리서 보면 6m 떨어진 벽도 붙었다).
  if(bd > JOIN_TOL_MM){
    document.getElementById('hint2d').textContent='끝점이 '+Math.round(bd)+'mm 떨어져 있어 결합하지 않았습니다(허용 '+JOIN_TOL_MM+'mm).';
    render2(); return;
  }
  const nw=manualWall(ends[0][1-best[0]], ends[1][1-best[1]], r1);
  delWall(r1); delWall(r2); addWall(nw); sel2=[nw.eid];
  refreshEdits(); fillPanel(nw,'wall'); render2();
}

svg2.addEventListener('pointerdown', ev=>{
  const t=ev.target;
  if(t.classList && t.classList.contains('hnd')){
    dragging={eid:t.dataset.eid, i:+t.dataset.i};
    svg2.setPointerCapture(ev.pointerId); ev.preventDefault(); return;
  }
  const eid=(t.dataset && t.dataset.eid) || null;
  const pt=evtWorld(ev);
  if(mode2==='draw'){
    const p=snapPoint(pt,null);
    if(!drawFrom){ drawFrom=p;
      document.getElementById('hint2d').textContent='끝점을 클릭하세요 (Esc 취소).'; }
    else { addWall(manualWall(drawFrom,p,null)); drawFrom=null;
      refreshEdits(); render2(); }
    return;
  }
  if(!eid){ if(mode2!=='join'){ sel2=[]; fillPanel(null,null); render2(); } return; }
  const rec=recByEid(eid); if(!rec) return;
  if(mode2==='split'){ splitWall(rec, pt); return; }
  if(mode2==='join'){
    if(sel2.indexOf(eid)<0) sel2.push(eid);
    if(sel2.length===2) joinWalls(recByEid(sel2[0]), recByEid(sel2[1]));
    render2(); return;
  }
  sel2=[eid]; fillPanel(rec,'wall'); render2();
});

svg2.addEventListener('pointermove', ev=>{
  if(!dragging) return;
  const rec=recByEid(dragging.eid); if(!rec) return;
  const cl=clOf(rec).map(p=>p.slice());
  cl[dragging.i]=snapPoint(evtWorld(ev), rec.eid);
  dragging.preview=cl;
  for(const e of g2.querySelectorAll('polyline.w'))
    if(e.dataset.eid===dragging.eid) e.setAttribute('points', cl.map(p=>p[0]+','+p[1]).join(' '));
});

svg2.addEventListener('pointerup', ()=>{
  if(!dragging) return;
  const rec=recByEid(dragging.eid), cl=dragging.preview;
  dragging=null;
  if(!rec || !cl){ return; }
  const old=clOf(rec), n=cl.length-1, m=old.length-1;
  if(Math.hypot(cl[0][0]-old[0][0], cl[0][1]-old[0][1])<1 &&
     Math.hypot(cl[n][0]-old[m][0], cl[n][1]-old[m][1])<1){ render2(); return; }
  const nw=manualWall(cl[0], cl[n], rec);
  delWall(rec); addWall(nw); sel2=[nw.eid];
  refreshEdits(); fillPanel(nw,'wall'); render2();
});

svg2.addEventListener('wheel', ev=>{
  ev.preventDefault();
  const k=ev.deltaY>0?1.15:0.87, c=evtWorld(ev);
  vb={x:c[0]-(c[0]-vb.x)*k, y:c[1]-(c[1]-vb.y)*k, w:vb.w*k, h:vb.h*k};
  applyVB(); render2();
}, {passive:false});

window.addEventListener('keydown', ev=>{
  if(!svg2.classList.contains('on')) return;
  if(ev.key==='Delete' && sel2.length){
    for(const eid of sel2){ const r=recByEid(eid); if(r) delWall(r); }
    sel2=[]; refreshEdits(); fillPanel(null,null); render2();
  }
  if(ev.key==='Escape'){ drawFrom=null; sel2=[]; fillPanel(null,null); render2(); }
});

for(const b of document.querySelectorAll('#tools2d button'))
  b.addEventListener('click', ()=>{
    mode2=b.dataset.m; drawFrom=null;
    // 결합은 '지금부터 고르는 2개' 다. 이전 선택을 물고 들어가면 사용자가
    // 한 번만 클릭했는데 엉뚱한 벽과 붙는다.
    sel2 = (mode2==='join') ? [] : sel2.slice(0,1);
    for(const x of document.querySelectorAll('#tools2d button'))
      x.classList.toggle('on', x===b);
    render2();
  });

function setTab(two){
  document.getElementById('t2d').classList.toggle('on', two);
  document.getElementById('t3d').classList.toggle('on', !two);
  document.getElementById('tools2d').classList.toggle('on', two);
  svg2.classList.toggle('on', two);
  renderer.domElement.style.display = two ? 'none' : '';
  if(two){ if(!vb) fitView(); render2(); }
}
document.getElementById('t2d').addEventListener('click', ()=>setTab(true));
document.getElementById('t3d').addEventListener('click', ()=>setTab(false));

function applyEditVisuals(){
  for(const m of meshes){
    const eid=m.userData.rec.eid; const e=eid&&edits[eid];
    m.visible = !(e&&e.deleted);
    if(e&&e.category){ m.material.color.setHex(CAT_COLOR[e.category]??0xffffff); }
    else { m.material.color.setHex(colorFor(m.userData.cat,m.userData.rec)); }
  }
}
document.getElementById('dl').addEventListener('click', ()=>{
  const blob=new Blob([JSON.stringify(edits,null,2)],{type:'application/json'});
  const a=document.createElement('a'); a.href=URL.createObjectURL(blob); a.download='edits.json'; a.click();
});

// ── 창호 반자동 배치 ────────────────────────────────────────
const SCHED = DATA.window_schedule || [];
const schedSel = document.getElementById('sched');
if(SCHED.length){
  SCHED.forEach((s,i)=>{ const o=document.createElement('option'); o.value=i;
    o.textContent=`${s.mark} ${Math.round(s.width)}×${Math.round(s.height)} (${s.subtype||'?'})`;
    schedSel.appendChild(o); });
} else {
  const o=document.createElement('option'); o.textContent='(창호일람 없음 — 창호부호 레이어 미검출)'; o.disabled=true; schedSel.appendChild(o);
}
function sceneToDxf(v){ return [v.x/S + CX, v.y/S + CY]; }
function hashId(str){ let h=2166136261>>>0; for(let i=0;i<str.length;i++){ h^=str.charCodeAt(i); h=Math.imul(h,16777619);} return (h>>>0).toString(36); }

let placeMode=false;
const bplace=document.getElementById('bplace');
bplace.onclick=()=>{
  if(!SCHED.length){ document.getElementById('placehint').textContent='⚠ 창호일람이 없어 배치할 수 없습니다.'; return; }
  placeMode=!placeMode;
  bplace.textContent='창호 배치: '+(placeMode?'ON':'OFF');
  bplace.classList.toggle('active', placeMode);
  renderer.domElement.style.cursor = placeMode?'crosshair':'default';
  document.getElementById('placehint').textContent = placeMode
    ? '벽을 클릭하면 선택한 창호를 그 위치에 배치합니다.'
    : '‘창호 배치’ 켠 뒤 벽을 클릭하면 그 위치에 배치됩니다.';
};

function placeWindow(wallMesh, hitVec){
  const s = SCHED[parseInt(schedSel.value)||0]; if(!s) return;
  const rec = wallMesh.userData.rec;
  const cl = rec.centerline || rec.points; if(!cl||cl.length<2) return;
  const a=cl[0], b=cl[cl.length-1];
  const hp = sceneToDxf(hitVec);
  const dx=b[0]-a[0], dy=b[1]-a[1], L2=dx*dx+dy*dy;
  let t = L2? ((hp[0]-a[0])*dx+(hp[1]-a[1])*dy)/L2 : 0;
  t = Math.max(0, Math.min(1, t));
  const cx=a[0]+t*dx, cy=a[1]+t*dy;
  const len=Math.hypot(dx,dy)||1, ux=dx/len, uy=dy/len;
  const ww = gcWidthOf(rec, P, 'wall');    // 빌드와 같은 값이어야 한다
  const half=s.width/2;
  const orec = {kind:'polyline', closed:false,
    points:[[cx-ux*half,cy-uy*half],[cx+ux*half,cy+uy*half]],
    center:[cx,cy], width:s.width, radius:s.width/2,
    height:s.height, sill:s.sill, subtype:s.subtype, mark:s.mark,
    host_dir:[ux,uy], host_width:ww, source:'manual_preview',
    z_base:(rec.z_base||0)};
  const eid='om:'+hashId(Math.round(cx)+'_'+Math.round(cy)+'_'+Math.round(s.width)+'_'+(s.subtype||''));
  orec.eid=eid;
  edits[eid]={added:true, category:'opening', record: JSON.parse(JSON.stringify(orec))};
  (DATA.elements.opening = DATA.elements.opening || []).push(orec);
  buildOpening(orec);
  document.getElementById('placehint').textContent=`✓ ${s.mark} 배치됨 @(${Math.round(cx)},${Math.round(cy)}). 계속 클릭 가능.`;
  refreshEdits(); applyEditVisuals();
}

// ── 툴바 ───────────────────────────────────────────────────
const b3d=document.getElementById('b3d'), btop=document.getElementById('btop');
b3d.onclick=()=>{fit(); b3d.classList.add('active'); btop.classList.remove('active');};
btop.onclick=()=>{topView(); btop.classList.add('active'); b3d.classList.remove('active');};
document.getElementById('bfit').onclick=fit;
document.getElementById('bconf').onclick=e=>{useConf=!useConf; e.target.classList.toggle('active',useConf); applyEditVisuals();};
document.getElementById('bwire').onclick=e=>{wire=!wire; e.target.classList.toggle('active',wire); meshes.forEach(m=>m.material.wireframe=wire);};

// ── 패널 정보 ──────────────────────────────────────────────
document.getElementById('src').textContent = (DATA.source||'').split(/[\\/]/).pop();
const _restored = loadEdits();
if(_restored){
  _lastSnap=JSON.stringify(edits);
  refreshEditsOnly(); applyEditVisuals();
}
renderWarnings();
if(_restored){
  const b=document.getElementById('warnbox');
  b.insertAdjacentHTML('afterbegin',
    '<div style="background:#2a3a2a">이 브라우저에 저장돼 있던 수정 '+_restored
    +'건을 복원했습니다 (Ctrl+Z 로 되돌리기).</div>');
}

// 파서가 낸 경고·통계를 화면에 옮긴다. 종전엔 CLI 로그에만 있었고, 3D 를 보는
// 사람은 무엇이 의심스러운지 알 수 없었다.
function renderWarnings(){
  const w=[...(DATA.warnings||[])];
  for(const c of (DATA.width_conflicts||[]).slice(0,6))
    w.push('두께 불일치: '+c.layer+' 선언 '+Math.round(c.declared)
           +' != 실측 '+Math.round(c.detected)+'mm x '+c.count+'개');
  const q=DATA.qa||{};
  if(q.face_coverage_pct!=null)
    w.push('면선 회수율 '+Math.round(q.face_coverage_pct)+'% '
           +(q.face_coverage_pct<90?'— 낮으면 페어링 실패(opts pair_max 확인)':''));
  const er=DATA.edits_report||{};
  if((er.orphaned||[]).length)
    w.push('이전 수정 중 붙일 곳을 못 찾은 것 '+er.orphaned.length+'건');
  if((er.ambiguous||[]).length)
    w.push('같은 EID 를 여러 부재가 공유해 적용 못 한 수정 '+er.ambiguous.length+'건');
  document.getElementById('nwarn').textContent=w.length;
  document.getElementById('warnbox').innerHTML =
    w.length ? w.map(x=>'<div>'+String(x).replace(/</g,'&lt;')+'</div>').join('') : '없음';
}
const E=DATA.elements; const cnt=Object.entries(E).filter(([,v])=>v.length).map(([k,v])=>`${k} ${v.length}`).join(' · ');
document.getElementById('counts').innerHTML=`<div class="muted">${cnt||'요소 없음'}</div>`;
const wp=DATA.wall_pairing||{};
document.getElementById('legend').innerHTML =
  Object.entries(CAT_COLOR).map(([k,c])=>`<div><span class="sw" style="background:#${c.toString(16).padStart(6,'0')}"></span>${k}</div>`).join('')
  + `<div style="margin-top:6px;border-top:1px solid #3a3f4a;padding-top:6px">신뢰도 색(벽): `
  + Object.entries(PAIR_COLOR).map(([k,c])=>`<span class="sw" style="background:#${c.toString(16).padStart(6,'0')}"></span>${k} `).join('')+`</div>`;

rebuild(); refreshEdits(); fit();
addEventListener('resize', ()=>{ cam.aspect=W()/H(); cam.updateProjectionMatrix(); renderer.setSize(W(),H()); });
(function loop(){ requestAnimationFrame(loop); controls.update(); renderer.render(scene,cam); })();
</script>
</body>
</html>
"""


if __name__ == "__main__":
    main()
