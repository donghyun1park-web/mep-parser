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
    }
    data_json = json.dumps(payload, ensure_ascii=False)
    # JS 안전: </script> 분리
    data_json = data_json.replace("</", "<\\/")
    html = _TEMPLATE.replace("/*__DATA__*/null", data_json)
    html = html.replace("<!--__IMPORTMAP__-->", importmap_section())
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
      <div class="row"><label>카테고리</label>
        <select id="e_cat"></select></div>
      <div class="row"><label>폭/지름 (mm)</label><input id="e_w" type="number"/></div>
      <div class="row"><label>높이/두께 (mm)</label><input id="e_h" type="number"/></div>
      <div class="row"><label><input type="checkbox" id="e_del" style="width:auto"/> 삭제</label></div>
      <button id="apply">수정 적용</button>
    </div>
    <h3>창호 배치 (반자동)</h3>
    <div id="schedwrap">
      <div class="row"><label>창호일람 선택</label>
        <select id="sched"></select></div>
      <div class="muted" id="placehint">‘창호 배치’ 켠 뒤 벽을 클릭하면 그 위치에 배치됩니다.</div>
    </div>
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

const S = 0.001;                  // mm -> m
const CX = DATA.center[0], CY = DATA.center[1];
const P = DATA.params || {};
const wallH = (P.wall && P.wall.height) || 2800;
const colH  = (P.column && P.column.height) || 3000;
const slabT = (P.slab && P.slab.thickness) || 200;

const CAT_COLOR = {
  wall:0x6b8fb5, column:0xc77dff, slab:0x8d99ae, zone:0x495057,
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
function buildWall(rec){
  const cl = rec.centerline || rec.points; if(!cl||cl.length<2) return;
  const w = (rec.overrides?.width ?? rec.width_detected ?? (P.wall?.width) ?? 200)*S;
  const h = (rec.overrides?.height ?? wallH)*S;
  const z0 = (rec.z_base||0)*S;
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
  const z0=(rec.z_base||0)*S, h=(rec.overrides?.height ?? colH)*S;
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
  const t=(rec.overrides?.thickness ?? slabT)*S; const z0=(rec.z_base||0)*S;
  const geo=new THREE.ExtrudeGeometry(shapeFrom(pts), {depth:t, bevelEnabled:false});
  addMesh(geo,'slab',rec,z0);
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
const edits = {};   // eid -> {category?, overrides?, deleted?}
const CATS=['wall','column','slab','zone','opening','pipe','duct','tray','equipment'];
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
  const eb=document.getElementById('editbox'), ns=document.getElementById('noSel');
  if(!m){ eb.style.display='none'; ns.style.display='block'; return; }
  m.material.emissive?.setHex(0x333300);
  eb.style.display='block'; ns.style.display='none';
  const rec=m.userData.rec, cat=m.userData.cat, eid=rec.eid||'(eid 없음)';
  document.getElementById('e_eid').textContent=eid;
  document.getElementById('e_layer').textContent=rec.layer||'-';
  document.getElementById('e_conf').textContent=(rec.confidence!=null?rec.confidence:'-')+(rec.pairing?' / '+rec.pairing:'');
  sel.value=(edits[eid]?.category)||cat;
  document.getElementById('e_w').value=edits[eid]?.overrides?.width ?? rec.overrides?.width ?? rec.width_detected ?? rec.width ?? '';
  document.getElementById('e_h').value=edits[eid]?.overrides?.height ?? rec.overrides?.height ?? rec.overrides?.thickness ?? '';
  document.getElementById('e_del').checked=!!edits[eid]?.deleted;
}
document.getElementById('apply').addEventListener('click', ()=>{
  if(!selected) return; const rec=selected.userData.rec, eid=rec.eid; if(!eid){alert('이 요소는 EID가 없어 수정 저장 불가');return;}
  const e=edits[eid]={}; const cat=sel.value;
  if(cat!==selected.userData.cat) e.category=cat;
  const w=parseFloat(document.getElementById('e_w').value), h=parseFloat(document.getElementById('e_h').value);
  const ov={}; if(!isNaN(w)) ov.width=w; if(!isNaN(h)){ (cat==='slab')?ov.thickness=h:ov.height=h; }
  if(Object.keys(ov).length) e.overrides=ov;
  if(document.getElementById('e_del').checked) e.deleted=true;
  if(!Object.keys(e).length) delete edits[eid];
  refreshEdits(); applyEditVisuals();
});
function refreshEdits(){
  const n=Object.keys(edits).length; document.getElementById('nedits').textContent=n;
  const el=document.getElementById('editlist');
  el.innerHTML = n? Object.entries(edits).map(([k,v])=>{
    if(v.added){ const r=v.record||{};
      return `<div class="kv"><span>➕ ${r.mark||'창호'} ${Math.round(r.width||0)}×${Math.round(r.height||0)}</span>`
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
  refreshEdits(); applyEditVisuals();
}
document.getElementById('editlist').addEventListener('click', ev=>{
  const a=ev.target.closest('a.rm'); if(a){ ev.preventDefault(); removeAdded(a.dataset.eid); }
});
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
  const ww = (rec.width_detected ?? rec.overrides?.width ?? (P.wall?.width) ?? 200);
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
