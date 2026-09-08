import './style.css';
import MepEdit from 'mep-edit';
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { buildReviewEntries, deriveSectionRange, floorKeyOf, isZVisible, reconcileSection, uniqueByEid, fitDistance, recordOnEditFloor, editBackdropState } from './review_logic.js';
import { screenToDrawing, drawingUnitsPerPixel } from './svg_coordinates.js';

const DATA = JSON.parse(document.getElementById('mep-data').textContent);
const RUNTIME = DATA.project_runtime || null;
let EDITS_REPORT=DATA.edits_report || {};
function excludedEditIds(report){ return [...((report||{}).orphaned||[]),...((report||{}).ambiguous||[])]; }
let BASE_ELEMENTS = MepEdit.deriveBaseElements(DATA.elements || {},DATA.project_edits || {},excludedEditIds(DATA.edits_report));
let CANONICAL_PRESENTATION = MepEdit.clone(DATA.elements || {});
let EFFECTIVE_ELEMENTS = MepEdit.materializeElements(BASE_ELEMENTS, DATA.project_edits || {});
window.addEventListener('error', e => {
  if (String(e.message).includes('three') || String(e.filename).includes('unpkg'))
    document.getElementById('err').style.display='block';
});

const {gcDim,gcZRange,gcWidthOf,gcCcw,gcBeamRings} = globalThis.MepContract;

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
const threeHost = document.getElementById('threeHost');
const W = () => Math.max(1,threeHost.clientWidth), H = () => Math.max(1,threeHost.clientHeight);
const cam = new THREE.PerspectiveCamera(45, W()/H(), 0.1, 100000);
cam.up.set(0,0,1);                // Z-up (도면 X,Y 평면 + Z 높이)
const renderer = new THREE.WebGLRenderer({antialias:true});
renderer.setSize(W(), H()); renderer.setPixelRatio(devicePixelRatio);
threeHost.appendChild(renderer.domElement);
const controls = new OrbitControls(cam, renderer.domElement);
controls.screenSpacePanning = false;

scene.add(new THREE.HemisphereLight(0xffffff, 0x404050, 1.1));
const dl = new THREE.DirectionalLight(0xffffff, 0.7); dl.position.set(1,1,2); scene.add(dl);
const grid = new THREE.GridHelper(200, 40, 0x3a3f4a, 0x2c3038);
grid.rotation.x = Math.PI/2; scene.add(grid);

const toM = p => [ (p[0]-CX)*S, (p[1]-CY)*S ];
const meshes = [];
let useConf = false, wire = false;
const sectionPlane=new THREE.Plane(new THREE.Vector3(0,0,-1),0);
let sectionEnabled=false, sectionHeight=NaN;
renderer.localClippingEnabled=true;

function colorFor(cat, rec){
  if (useConf && cat==='wall') return PAIR_COLOR[rec.pairing] ?? CAT_COLOR.wall;
  return CAT_COLOR[cat] ?? 0xaaaaaa;
}
function addMesh(geo, cat, rec, z, opts={}){
  const mat = new THREE.MeshLambertMaterial({
    color: colorFor(cat, rec),
    transparent: !!opts.trans, opacity: opts.trans ? (opts.op??0.35) : 1.0,
    wireframe: wire, clippingPlanes:sectionEnabled?[sectionPlane]:[]
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
  const wmm = rec.overrides?.width ?? rec.width ?? (rec.radius? rec.radius*2 : 900);
  const hmm = rec.overrides?.height ?? rec.height ?? (isWin?1200:2100);
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
  const E=EFFECTIVE_ELEMENTS;
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
  const errorBox=document.getElementById('err');
  if(!meshes.length){ errorBox.textContent='표시할 수 있는 3D 부재가 없습니다. 원본 도면과 검토 경고를 확인하세요.'; errorBox.style.display='block'; }
  else if(errorBox.textContent.includes('표시할 수 있는 3D')) errorBox.style.display='none';
  applyEditVisuals();
  selected=null;
  for(const mesh of meshes) if(mesh.userData.rec.eid===selectedEid){
    mesh.material.emissive?.setHex(0x333300); selected=mesh;
  }
  if(selectedEid&&!selected){ updateSourceSelection(null); fillPanel(null,null); }
  updateSectionBounds();
  renderSourceOverlay();
  renderReview();
}

function fit(){
  const box=new THREE.Box3(); meshes.forEach(m=>box.expandByObject(m));
  frameBox(box);
}
function frameBox(box,direction=new THREE.Vector3(1,-1,1)){
  if(box.isEmpty()){ cam.position.set(20,-20,20); controls.target.set(0,0,0); controls.update(); return; }
  const sphere=box.getBoundingSphere(new THREE.Sphere());
  const distance=fitDistance(sphere.radius,cam.fov,W()/H());
  cam.aspect=W()/H(); cam.near=Math.max(.01,distance/1000); cam.far=Math.max(1000,distance*20); cam.updateProjectionMatrix();
  cam.position.copy(sphere.center).add(direction.normalize().multiplyScalar(distance));
  controls.target.copy(sphere.center); controls.update();
}
function topView(){
  const box=new THREE.Box3(); meshes.forEach(m=>box.expandByObject(m));
  frameBox(box,new THREE.Vector3(0,-.0001,1));
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

const edits = MepEdit.clone(DATA.project_edits || {}); // canonical server snapshot + local changes
const CATS=['wall','column','slab','beam','zone','opening','pipe','duct','tray','equipment'];
const sel=document.getElementById('e_cat'); CATS.forEach(c=>{const o=document.createElement('option');o.value=o.textContent=c;sel.appendChild(o);});

renderer.domElement.addEventListener('click', ev=>{
  if(ACTION_LOCK) return;
  const r=renderer.domElement.getBoundingClientRect();
  mouse.x=((ev.clientX-r.left)/r.width)*2-1; mouse.y=-((ev.clientY-r.top)/r.height)*2+1;
  ray.setFromCamera(mouse,cam);
  const hit=ray.intersectObjects(meshes).find(x=>x.object.visible && isZVisible(x.point.z/S,sectionHeight,sectionEnabled));
  if(placeMode){
    if(hit && hit.object.userData.cat==='wall'){ placeWindow(hit.object, hit.point); }
    else { document.getElementById('placehint').textContent='⚠ 벽을 클릭하세요(창호는 벽 위에만 배치).'; }
    return;
  }
  select(hit?hit.object:null);
});
function select(m){
  for(const mesh of meshes) mesh.material.emissive?.setHex(0x000000);
  selected=m;
  if(!m){ fillPanel(null, null); updateSourceSelection(null); return; }
  const eid=m.userData.rec.eid;
  const canonical=uniqueByEid(Object.values(EFFECTIVE_ELEMENTS).flat(),eid);
  if(!canonical){ selected=null; fillPanel(null,null); updateSourceSelection(null); document.getElementById('noSel').textContent='같은 EID를 가진 부재가 여러 개여서 안전하게 선택할 수 없습니다: '+eid; return; }
  for(const mesh of meshes) if(mesh.userData.rec.eid===eid) mesh.material.emissive?.setHex(0x333300);
  updateSourceSelection(eid);
  if(canonical.level!=null) setSourceFloor(String(canonical.level));
  focusSourceEid(eid);
  fillPanel(canonical, m.userData.cat);
}
function selectEid(eid,{focus=false}={}){
  const canonical=uniqueByEid(Object.values(EFFECTIVE_ELEMENTS).flat(),eid);
  if(!canonical){ fillPanel(null,null); document.getElementById('noSel').textContent='같은 EID를 가진 부재가 여러 개이거나 대상이 없어 선택할 수 없습니다: '+eid; return false; }
  const matching=meshes.filter(m=>m.userData.rec.eid===eid && m.visible);
  if(!matching.length) return false;
  select(matching[0]);
  if(sectionEnabled){ sectionEnabled=false; document.getElementById('sectionEnabled').checked=false; applySection(); }
  if(focus){
    const box=new THREE.Box3(); matching.forEach(m=>box.expandByObject(m));
    frameBox(box);
  }
  return true;
}

// 3D 와 2D 평면 탭이 **같은 패널을 공유**한다. 뷰마다 인스펙터를 따로 두면
// 필드가 어긋나기 시작하고, 그게 이 저장소가 반복해 밟은 종류의 버그다.
let selRec=null, selCat=null;
function fillPanel(rec, cat){
  const eb=document.getElementById('editbox'), ns=document.getElementById('noSel');
  selRec=rec; selCat=cat;
  if(!rec){ eb.style.display='none'; ns.style.display='block'; return; }
  ns.textContent='요소를 클릭하세요.';
  eb.style.display='block'; ns.style.display='none';
  const eid=rec.eid||'(eid 없음)';
  document.getElementById('e_eid').textContent=eid;
  document.getElementById('e_layer').textContent=rec.layer||'-';
  document.getElementById('e_conf').textContent=(rec.confidence!=null?rec.confidence:'-')+(rec.pairing?' / '+rec.pairing:'');
  sel.value=(edits[eid]?.category)||cat;
  document.getElementById('e_w').value=edits[eid]?.overrides?.width ?? rec.overrides?.width ?? rec.width_detected ?? rec.width ?? '';
  document.getElementById('e_h').value=edits[eid]?.overrides?.height ?? rec.overrides?.height ?? rec.overrides?.thickness ?? '';
  document.getElementById('e_del').checked=!!edits[eid]?.deleted;
  const reviewRow=document.getElementById('reviewrow');
  reviewRow.style.display=(rec.needs_review||rec.review_ack_stale)?'block':'none';
  document.getElementById('e_review').checked=false;
  if(rec.level!=null && document.getElementById('floor2d')) document.getElementById('floor2d').value=String(rec.level);
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
  const same=(EFFECTIVE_ELEMENTS.wall||[]).filter(r=>
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
  if(rec.review_ack_stale) out.push('<b style="color:#e93">형상이 바뀌어 이전 검토 확인이 만료됨</b>');
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
  for(const d of (rec.edit_diagnostics||[])){
    if(d.code==='endpoint_gap') out.push('<b style="color:#e93">끝점 간격 '+Math.round(d.distance_mm||0)+'mm</b>'
      +(d.other?' · '+d.other:'')+' — 평면 편집에서 끝점을 직접 스냅해 확인하세요.');
    else if(d.code==='wall_overlap') out.push('<b style="color:#e93">벽 겹침 '+Math.round(d.length_mm||0)+'mm</b>'
      +(d.other?' · '+d.other:'')+' — 중복인지 확인한 뒤 하나를 삭제하거나 끝점을 옮기세요.');
    else if(d.code==='opening_host_missing') out.push('<b style="color:#e93">개구부 연결 벽 없음</b>'
      +(d.other?' · '+d.other:'')+' — 가까운 벽과 위치를 확인해 다시 배치하세요.');
  }
  return out.length?out.map(x=>'<div class="why">'+x+'</div>').join(''):'';
}
document.getElementById('apply').addEventListener('click', ()=>{
  const rec=selRec; if(!rec) return; const eid=rec.eid; if(!eid){alert('이 요소는 EID가 없어 수정 저장 불가');return;}
  const cat=sel.value;
  const w=parseFloat(document.getElementById('e_w').value), h=parseFloat(document.getElementById('e_h').value);
  const e=MepEdit.applyProperties(edits[eid],rec,selCat,{
    category:cat,width:w,height:h,
    deleted:document.getElementById('e_del').checked,
    reviewResolved:document.getElementById('e_review').checked
  });
  edits[eid]=e;
  if(!Object.keys(e).filter(k=>k[0]!=='_').length) delete edits[eid];
  refreshEdits();
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
  _lastSnap=prev; persistAndQueue(); syncEffective();
}
window.addEventListener('keydown', ev=>{
  if(ACTION_LOCK) return;
  if((ev.ctrlKey||ev.metaKey) && ev.key.toLowerCase()==='z'){ ev.preventDefault(); undo(); }
});

// 서버의 기준 revision 과 함께 보관해야 오래된 오프라인 사본이 새 서버 상태를 덮지 않는다.
let LSKEY=RUNTIME ? MepEdit.backupKey(RUNTIME) : 'mepEdits:standalone:'+encodeURIComponent(DATA.source||'');
function saveLocal(){
  try{ localStorage.setItem(LSKEY,JSON.stringify({project_id:RUNTIME&&RUNTIME.project_id,
        base_revision:RUNTIME&&RUNTIME.revision,edits:MepEdit.clone(edits)})); }catch(e){}
}
function loadEdits(){
  try{
    const raw=localStorage.getItem(LSKEY); if(!raw) return null;
    const o=MepEdit.restoreBackup(raw,RUNTIME); if(o===null) return null;
    for(const k of Object.keys(edits)) delete edits[k];
    Object.assign(edits,o);
    return {count:Object.keys(o).length};
  }catch(e){ return null; }
}

function refreshEdits(){
  pushUndo(); persistAndQueue(); syncEffective();
}
function replaceObject(target,source){ for(const k of Object.keys(target)) delete target[k]; Object.assign(target,MepEdit.clone(source||{})); }
function syncEffective(){
  EFFECTIVE_ELEMENTS=MepEdit.materializeElements(BASE_ELEMENTS,edits);
  if(CANONICAL_PRESENTATION) MepEdit.mergePresentation(EFFECTIVE_ELEMENTS,CANONICAL_PRESENTATION);
  refreshEditsOnly(); rebuild();
  if(svg2.classList.contains('on')) render2();
}
function setSaveStatus(kind,text){
  const bar=document.getElementById('savebar'); bar.className=kind;
  document.getElementById('savestatus').textContent=text;
  document.getElementById('retry').style.display=kind==='error'?'inline-block':'none';
  document.getElementById('serverFresh').style.display=kind==='conflict'?'inline-block':'none';
}
function apiUrl(path){ return String(RUNTIME.base_url||'').replace(/\/$/,'')+path; }
async function apiPost(path,body){
  const response=await fetch(apiUrl(path),{method:'POST',headers:{'Content-Type':'application/json',
    'Authorization':'Bearer '+RUNTIME.token},body:JSON.stringify(body)});
  let data={}; try{ data=await response.json(); }catch(e){}
  if(!response.ok){ const err=new Error(data.error||('HTTP '+response.status)); err.status=response.status; err.state=data; throw err; }
  return data;
}
function mergeServerMetadata(canonical){
  for(const [eid,serverEdit] of Object.entries(canonical||{})){
    if(!edits[eid]) continue;
    for(const key of ['_source','_at','_review_signature'])
      if(serverEdit[key]!=null && edits[eid][key]==null) edits[eid][key]=MepEdit.clone(serverEdit[key]);
  }
}
function mergeServerPresentation(response){
  const elements=response.geometry&&response.geometry.elements; if(!elements) return;
  EDITS_REPORT=response.geometry.edits_report || {};
  orphanSuggestions=((response.geometry.edits_report)||{}).relink_suggestions||[];
  renderOrphans();
}
let SAVE_QUEUE=null;
if(RUNTIME){
  SAVE_QUEUE=MepEdit.createSaveQueue({revision:RUNTIME.revision,acknowledged:DATA.project_edits||{},
    send:(snapshot,revision)=>apiPost('/edits',{project_id:RUNTIME.project_id,expected_revision:revision,edits:snapshot}),
    onAck:(response,sent)=>{
      const unchanged=JSON.stringify(edits)===JSON.stringify(sent);
      if(response.geometry&&response.geometry.elements)
        BASE_ELEMENTS=MepEdit.deriveBaseElements(response.geometry.elements,response.edits||{},excludedEditIds(response.geometry.edits_report));
      if(unchanged) replaceObject(edits,response.edits||{});
      else { mergeServerMetadata(response.edits||{}); queueMicrotask(()=>SAVE_QUEUE.enqueue(edits)); }
      CANONICAL_PRESENTATION=unchanged&&response.geometry?MepEdit.clone(response.geometry.elements):null;
      mergeServerPresentation(response);
      try{ localStorage.removeItem(LSKEY); }catch(e){}
      RUNTIME.revision=response.revision; LSKEY=MepEdit.backupKey(RUNTIME);
      if(!unchanged) saveLocal();
      _lastSnap=JSON.stringify(edits); syncEffective();
    },
    onStatus:kind=>{
      if(kind==='saving') setSaveStatus('saving','저장 중…');
      if(kind==='saved') setSaveStatus('saved','서버에 저장됨');
    },
    onError:error=>setSaveStatus('error','저장 실패 — 로컬 백업에 보관됨: '+error.message),
    onConflict:()=>setSaveStatus('conflict','저장 충돌 — 서버 최신 상태를 덮어쓰지 않았습니다. 로컬 수정은 다운로드할 수 있습니다.')
  });
}
function persistAndQueue(){
  saveLocal();
  CANONICAL_PRESENTATION=null;
  if(!SAVE_QUEUE){ setSaveStatus('unsaved','서버 연결 없음 — 브라우저에 보관됨. edits.json을 다운로드하세요.'); return; }
  setSaveStatus('unsaved','저장 안 됨');
  SAVE_QUEUE.enqueue(edits);
}
document.getElementById('retry').addEventListener('click',()=>SAVE_QUEUE&&SAVE_QUEUE.retry());
document.getElementById('serverFresh').addEventListener('click',()=>location.reload());
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
  for(const cat of Object.keys(BASE_ELEMENTS)) BASE_ELEMENTS[cat]=(BASE_ELEMENTS[cat]||[]).filter(r=>r.eid!==eid);
  delete edits[eid];
  if(typeof sel2!=='undefined'){ sel2=sel2.filter(x=>x!==eid); if(typeof render2==='function') render2(); }
  refreshEdits();
}
document.getElementById('editlist').addEventListener('click', ev=>{
  const a=ev.target.closest('a.rm'); if(a){ ev.preventDefault(); removeAdded(a.dataset.eid); }
});

// 원본 도면은 배경일 뿐이며 EID 를 추측하지 않는다. 선택 가능한 것은 파서가 만든
// EID 오버레이뿐이어서 원본 선과 중심선을 거리로 억지 연결하지 않는다.
const sourceDrawing=DATA.source_drawing || null;
const sourceSvg=document.getElementById('sourceView');
const sourceGeometry=document.getElementById('sourceGeometry');
const sourceOverlay=document.getElementById('sourceOverlay');
const sourceFloor=document.getElementById('sourceFloor');
let sourceVB=null, sourceDrag=null, selectedEid=null;
let renderedSourceFloor=null;
const sourceFloors=(sourceDrawing&&sourceDrawing.floors)||[];
for(const floor of sourceFloors){
  const o=document.createElement('option'); o.value=String(floor.id); o.textContent=floor.label||floor.name||floor.id; sourceFloor.appendChild(o);
}
document.getElementById('sourceBar').insertAdjacentHTML('beforeend',
  '<span class="source-legend original">━ 원본 DXF</span><span class="source-legend derived">━ 해석 부재</span>');
function selectedSourceFloor(){ return sourceFloors.find(f=>String(f.id)===sourceFloor.value) || sourceFloors[0] || null; }
function setSourceFloor(value){
  if([...sourceFloor.options].some(o=>o.value===String(value)) && sourceFloor.value!==String(value)){
    sourceFloor.value=String(value); renderedSourceFloor=null; fitSource(); renderSourceBackdrop();
  }
  renderSourceOverlay();
}
function sourceViewBox(){
  const floor=selectedSourceFloor(), b=floor&&floor.bbox;
  if(!b) return null;
  const pad=Math.max(500,((b[2]-b[0])+(b[3]-b[1]))*.025);
  return {x:b[0]-pad,y:b[1]-pad,w:Math.max(1,b[2]-b[0]+pad*2),h:Math.max(1,b[3]-b[1]+pad*2)};
}
function applySourceVB(){
  if(!sourceVB) return;
  sourceSvg.setAttribute('viewBox',`${sourceVB.x} ${-(sourceVB.y+sourceVB.h)} ${sourceVB.w} ${sourceVB.h}`);
  sourceGeometry.setAttribute('transform','scale(1,-1)'); sourceOverlay.setAttribute('transform','scale(1,-1)');
}
function fitSource(){ sourceVB=sourceViewBox(); applySourceVB(); }
function sourceElement(kind,record,klass){
  if(kind==='circle' && record.center){ return mk2('circle',{cx:record.center[0],cy:record.center[1],r:record.radius||1,class:klass}); }
  const points=record.centerline||record.points;
  if(!points||points.length<2) return null;
  return mk2(record.closed?'polygon':'polyline',{points:points.map(p=>p[0]+','+p[1]).join(' '),class:klass});
}
function updateSourceSelection(eid){
  selectedEid=eid;
  for(const node of sourceOverlay.querySelectorAll('[data-eid]')) node.classList.toggle('selected',node.dataset.eid===eid);
}
function renderSourceBackdrop(){
  const unavailable=document.getElementById('sourceUnavailable'), floor=selectedSourceFloor();
  if(!sourceDrawing || !floor){
    unavailable.style.display='block';
    const warning=(sourceDrawing&&sourceDrawing.warnings||[]).join(' · ');
    unavailable.textContent=warning||'이 결과에는 원본 DXF 도형 데이터가 없습니다. 3D 모델은 계속 볼 수 있습니다.';
    sourceSvg.style.display='none'; return;
  }
  if(renderedSourceFloor===String(floor.id)) return;
  renderedSourceFloor=String(floor.id); sourceGeometry.replaceChildren();
  sourceSvg.style.display='block';
  const disclosures=[];
  if(sourceDrawing.status && sourceDrawing.status!=='available') disclosures.push('상태: '+sourceDrawing.status);
  disclosures.push(...(floor.warnings||[]));
  for(const [kind,count] of Object.entries(floor.omitted||{})) if(count)
    disclosures.push(kind==='TRUNCATED'?'표시 제한으로 나머지 생략 (개수 미집계)':`${kind} ${count}개 생략`);
  unavailable.style.display=disclosures.length?'block':'none'; unavailable.textContent=disclosures.join(' · ');
  for(const primitive of floor.primitives||[]){
    const node=sourceElement(primitive.kind,primitive,primitive.kind==='circle'?'source-circle':'source-line');
    if(node) sourceGeometry.appendChild(node);
  }
}
function renderSourceOverlay(){
  sourceOverlay.replaceChildren();
  const floor=selectedSourceFloor(); if(!floor) return;
  for(const [category,records] of Object.entries(EFFECTIVE_ELEMENTS)) for(const rec of records||[]){
    if(!rec.eid || (floorKeyOf(rec) && floorKeyOf(rec)!==String(floor.id))) continue;
    const node=sourceElement(rec.kind,rec,'model-overlay'); if(!node) continue;
    node.dataset.eid=rec.eid; node.dataset.category=category; sourceOverlay.appendChild(node);
  }
  updateSourceSelection(selectedEid);
}
function focusSourceEid(eid){
  const records=Object.values(EFFECTIVE_ELEMENTS).flat().filter(rec=>rec.eid===eid);
  const points=[];
  for(const rec of records){
    if(rec.center){const r=rec.radius||200;points.push([rec.center[0]-r,rec.center[1]-r],[rec.center[0]+r,rec.center[1]+r]);}
    points.push(...(rec.centerline||rec.points||[]));
  }
  if(!points.length) return;
  const xs=points.map(p=>p[0]), ys=points.map(p=>p[1]), pad=Math.max(500,(Math.max(...xs)-Math.min(...xs)+Math.max(...ys)-Math.min(...ys))*.2);
  sourceVB={x:Math.min(...xs)-pad,y:Math.min(...ys)-pad,w:Math.max(1,Math.max(...xs)-Math.min(...xs)+pad*2),h:Math.max(1,Math.max(...ys)-Math.min(...ys)+pad*2)}; applySourceVB();
}
function sourceWorldPoint(ev){
  const matrix=sourceSvg.getScreenCTM(); if(!matrix) return null;
  const p=new DOMPoint(ev.clientX,ev.clientY).matrixTransform(matrix.inverse()); return [p.x,-p.y];
}
sourceFloor.addEventListener('change',()=>{
  // An explicit floor choice supersedes a previously selected wall on another floor.
  if(floor2d.value!==sourceFloor.value){
    floor2d.value=sourceFloor.value; sel2=[]; drawFrom=null; vb=null; select(null);
  }
  renderedSourceFloor=null;fitSource();renderSourceBackdrop();renderSourceOverlay();
});
document.getElementById('sourceFit').addEventListener('click',fitSource);
sourceSvg.addEventListener('wheel',ev=>{
  if(!sourceVB||ACTION_LOCK) return; ev.preventDefault(); const k=ev.deltaY>0?1.15:.87;
  const point=sourceWorldPoint(ev); if(!point) return; const [x,y]=point;
  sourceVB={x:x-(x-sourceVB.x)*k,y:y-(y-sourceVB.y)*k,w:sourceVB.w*k,h:sourceVB.h*k}; applySourceVB();
},{passive:false});
sourceSvg.addEventListener('pointerdown',ev=>{if(sourceVB&&!ACTION_LOCK){sourceDrag={start:sourceWorldPoint(ev),last:sourceWorldPoint(ev),moved:false,eid:ev.target.dataset&&ev.target.dataset.eid};sourceSvg.setPointerCapture(ev.pointerId);}});
sourceSvg.addEventListener('pointermove',ev=>{if(sourceDrag){const point=sourceWorldPoint(ev);if(!point||!sourceDrag.last)return;const dx=point[0]-sourceDrag.last[0],dy=point[1]-sourceDrag.last[1];sourceDrag.moved=sourceDrag.moved||Math.hypot(point[0]-sourceDrag.start[0],point[1]-sourceDrag.start[1])>sourceVB.w*.004;sourceVB={...sourceVB,x:sourceVB.x-dx,y:sourceVB.y-dy};applySourceVB();sourceDrag.last=sourceWorldPoint(ev);}});
sourceSvg.addEventListener('pointerup',()=>{if(sourceDrag&&!sourceDrag.moved&&sourceDrag.eid&&!ACTION_LOCK)selectEid(sourceDrag.eid);sourceDrag=null;});

let sectionRange={min:0,max:3000};
const sectionSlider=document.getElementById('sectionRange'), sectionInput=document.getElementById('sectionHeight');
sectionSlider.step=sectionInput.step=1;
function updateSectionBounds(){
  // Use what is actually drawn, including window sills and sampled geometry.
  // This is a view range; it does not change the canonical geometry contract.
  const drawn=meshes.filter(mesh=>mesh.visible).map(mesh=>{
    const box=new THREE.Box3().setFromObject(mesh);
    return box.isEmpty()?[]:[box.min.z/S,box.max.z/S];
  });
  const next=deriveSectionRange({drawn},sourceFloors,(_cat,range)=>range);
  const state=reconcileSection({min:Math.floor(next.min),max:Math.ceil(next.max)},sectionHeight); sectionRange={min:state.min,max:state.max}; sectionHeight=state.height;
  sectionSlider.min=sectionInput.min=sectionRange.min; sectionSlider.max=sectionInput.max=sectionRange.max;
  sectionSlider.value=sectionInput.value=sectionHeight;
  applySection();
}
function applySection(){
  sectionPlane.constant=sectionHeight*S;
  for(const mesh of meshes) mesh.material.clippingPlanes=sectionEnabled?[sectionPlane]:[];
}
document.getElementById('sectionEnabled').addEventListener('change',ev=>{sectionEnabled=ev.target.checked;applySection();});
function changeSection(value){ const z=Math.max(sectionRange.min,Math.min(sectionRange.max,Number(value))); if(!Number.isFinite(z)) return; sectionHeight=z;sectionSlider.value=sectionInput.value=Math.round(z);applySection(); }
sectionSlider.addEventListener('input',ev=>changeSection(ev.target.value)); sectionInput.addEventListener('change',ev=>changeSection(ev.target.value));
sectionInput.addEventListener('input',ev=>{
  const value=ev.target.valueAsNumber;
  if(Number.isFinite(value)&&value>=sectionRange.min&&value<=sectionRange.max)changeSection(value);
});

const reviewFloor=document.getElementById('reviewFloor'), reviewCategory=document.getElementById('reviewCategory');
function renderReview(){
  const entries=buildReviewEntries(EFFECTIVE_ELEMENTS,EDITS_REPORT);
  const floors=[...new Set(entries.map(x=>x.floor).filter(Boolean))].sort();
  const categories=[...new Set(entries.map(x=>x.category).filter(Boolean))].sort();
  const keepFloor=reviewFloor.value, keepCategory=reviewCategory.value;
  reviewFloor.innerHTML='<option value="">모든 층</option>'+floors.map(x=>`<option>${escHtml(x)}</option>`).join('');
  reviewCategory.innerHTML='<option value="">모든 종류</option>'+categories.map(x=>`<option>${escHtml(x)}</option>`).join('');
  reviewFloor.value=floors.includes(keepFloor)?keepFloor:''; reviewCategory.value=categories.includes(keepCategory)?keepCategory:'';
  const shown=entries.filter(x=>(!reviewFloor.value||x.floor===reviewFloor.value)&&(!reviewCategory.value||x.category===reviewCategory.value));
  document.getElementById('reviewCount').textContent=shown.length;
  document.getElementById('reviewList').innerHTML=shown.length?shown.map(x=>`<button class="review-item" data-eid="${escHtml(x.eid||'')}" data-orphan="${escHtml(x.orphan||'')}"><b>${escHtml(x.eid||x.orphan||x.kind)}</b> · ${escHtml(x.category)}${x.floor?' · 층 '+escHtml(x.floor):''}<small>${escHtml(x.reason)}</small></button>`).join(''):'없음';
}
reviewFloor.addEventListener('change',renderReview); reviewCategory.addEventListener('change',renderReview);
document.getElementById('reviewList').addEventListener('click',ev=>{
  if(ACTION_LOCK) return;
  const row=ev.target.closest('.review-item'); if(!row) return;
  if(row.dataset.eid){ setTab(false); selectEid(row.dataset.eid,{focus:true}); }
  else {
    const target=document.querySelector(`.orphan[data-orphan="${CSS.escape(row.dataset.orphan)}"]`);
    const destination=target||document.getElementById('orphans');
    destination.scrollIntoView({behavior:'smooth',block:'center'}); target?.querySelector('.candidate')?.focus();
  }
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
const LEVEL_INFO=new Map();
for(const records of Object.values(BASE_ELEMENTS)) for(const rec of records||[])
  if(rec.level!=null && !LEVEL_INFO.has(String(rec.level))) LEVEL_INFO.set(String(rec.level),{
    level:String(rec.level),z_base:rec.z_base,elevation:rec.elevation});
const floor2d=document.getElementById('floor2d');
const editFloorKeys=[...new Set([...sourceFloors.map(f=>String(f.id)),...LEVEL_INFO.keys()])];
if(!editFloorKeys.length) editFloorKeys.push('main');
for(const level of editFloorKeys){ const option=document.createElement('option'); option.value=level; option.textContent=sourceFloors.find(f=>String(f.id)===level)?.label||level; floor2d.appendChild(option); }
document.getElementById('floorwrap').style.display=editFloorKeys.length>1?'inline':'none';
function activeFloorSource(){ return LEVEL_INFO.get(floor2d.value)||{}; }
function onEditFloor(rec){ return recordOnEditFloor(rec,floor2d.value,editFloorKeys.length); }
const editBackdrop=mk2('g',{id:'editSourceGeometry','aria-hidden':'true'});
const editSourceEnabled=document.getElementById('editSourceEnabled');
const editSourceOpacity=document.getElementById('editSourceOpacity');
let renderedEditFloor=null, editSourceAvailable=false;
function updateEditSourceVisibility(){
  const enabled=editSourceEnabled.checked&&editSourceAvailable;
  editBackdrop.style.display=enabled?'':'none';
  editBackdrop.setAttribute('opacity',Number(editSourceOpacity.value)/100);
  svg2.classList.toggle('source-on',enabled);
  editSourceOpacity.disabled=!enabled;
}
function renderEditBackdrop(){
  if(renderedEditFloor!==floor2d.value){
    const state=editBackdropState(sourceDrawing,floor2d.value);
    editBackdrop.replaceChildren();
    // Source primitives already carry the stack offset. The same parent CTM
    // as editable walls keeps them aligned without any second transform.
    for(const primitive of state.floor?.primitives||[]){
      const node=sourceElement(primitive.kind,primitive,'edit-source-line');
      if(node) editBackdrop.appendChild(node);
    }
    editSourceAvailable=state.available; editSourceEnabled.disabled=!state.available;
    document.getElementById('editSourceStatus').textContent=state.notices.join(' · ');
    renderedEditFloor=floor2d.value;
  }
  updateEditSourceVisibility();
}
editSourceEnabled.addEventListener('change',updateEditSourceVisibility);
editSourceOpacity.addEventListener('input',updateEditSourceVisibility);
floor2d.addEventListener('change',()=>{
  dragging=null; drawFrom=null; sel2=[]; fillPanel(null,null);
  setSourceFloor(floor2d.value); fitView(); render2();
});

function wallsAll(){
  return EFFECTIVE_ELEMENTS.wall||[];
}
function clOf(r){ return r.centerline || r.points || []; }
function isDel(r){ return !!(edits[r.eid] && edits[r.eid].deleted); }
function recByEid(eid){ return wallsAll().find(r=>r.eid===eid); }

function fitView(){
  const b=editBackdropState(sourceDrawing,floor2d.value).floor?.bbox||DATA.bbox; if(!b) return;
  const pad=(b[2]-b[0]+b[3]-b[1])*0.03+500;
  vb={x:b[0]-pad, y:b[1]-pad, w:(b[2]-b[0])+pad*2, h:(b[3]-b[1])+pad*2};
  applyVB();
}
function applyVB(){
  // y 를 뒤집는다 — 도면은 위가 +y, SVG 는 아래가 +y.
  svg2.setAttribute('viewBox', vb.x+' '+(-(vb.y+vb.h))+' '+vb.w+' '+vb.h);
  g2.setAttribute('transform','scale(1,-1)');
}
function px2world(){ return drawingUnitsPerPixel(g2.getScreenCTM()); }
function evtWorld(ev){
  return screenToDrawing(g2.getScreenCTM(),ev.clientX,ev.clientY);
}
function mk2(t,a){ const e=document.createElementNS(SVG2NS,t);
  for(const k in a) e.setAttribute(k,a[k]); return e; }

function render2(){
  if(!vb) fitView();
  while(g2.firstChild) g2.removeChild(g2.firstChild);
  for(const r of (EFFECTIVE_ELEMENTS.slab||[]))
    if(onEditFloor(r)&&(r.points||[]).length>2) g2.appendChild(mk2('polygon',
      {points:r.points.map(p=>p[0]+','+p[1]).join(' '), fill:'#1b2029', stroke:'#2b3240'}));
  for(const r of (EFFECTIVE_ELEMENTS.column||[])){
    if(!onEditFloor(r)) continue;
    if(r.kind==='circle') g2.appendChild(mk2('circle',
      {cx:r.center[0],cy:r.center[1],r:r.radius,class:'col'}));
    else if((r.points||[]).length>2) g2.appendChild(mk2('polygon',
      {points:r.points.map(p=>p[0]+','+p[1]).join(' '), class:'col'}));
  }
  for(const r of (EFFECTIVE_ELEMENTS.opening||[])){
    if(!onEditFloor(r)) continue;
    const c=r.center;
    if(c) g2.appendChild(mk2('circle',
      {cx:c[0],cy:c[1],r:Math.max(120,r.radius||150),class:'op'}));
  }
  // Reuse the cached source group across zoom/edit renders; do not reconstruct
  // tens of thousands of original DXF primitives on every interaction.
  renderEditBackdrop(); g2.appendChild(editBackdrop);
  for(const s of orphanSuggestions){
    if(deferredOrphans.has(s.orphan)) continue;
    const src=(s.edit&&s.edit._source)||{};
    if(!onEditFloor(src)) continue;
    const pts=src.centerline||src.points||[];
    if(pts.length>1) g2.appendChild(mk2('polyline',{points:pts.map(p=>p[0]+','+p[1]).join(' '),class:'orphan-old'}));
  }
  for(const rec of wallsAll()){
    if(!onEditFloor(rec)) continue;
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
    if(!onEditFloor(rec) || sel2.indexOf(rec.eid)<0 || isDel(rec)) continue;
    const cl=clOf(rec);
    for(const i of cl.map((_,i)=>i)){
      const h=mk2('circle',{cx:cl[i][0],cy:cl[i][1],r:SNAP_PX*k*0.6,class:'hnd'});
      h.dataset.eid=rec.eid; h.dataset.i=i; g2.appendChild(h);
    }
  }
  document.getElementById('hint2d').textContent = HINT2[mode2] || '';
}

function manualWall(points, src){
  src=src||activeFloorSource();
  return MepEdit.makeManualWall(points,src,{
    width:Math.round(src?gcWidthOf(src,P,'wall'):((P.wall&&P.wall.width)||200)),height:wallH});
}
function addWall(rec){ edits[rec.eid]={added:true, category:'wall', record:rec}; }
function stripBaseEid(eid){ for(const cat of Object.keys(BASE_ELEMENTS)) BASE_ELEMENTS[cat]=(BASE_ELEMENTS[cat]||[]).filter(r=>r.eid!==eid); }
function delWall(rec){
  if(edits[rec.eid] && edits[rec.eid].added){ stripBaseEid(rec.eid); delete edits[rec.eid]; }
  else edits[rec.eid]=Object.assign({}, edits[rec.eid], {deleted:true});
}

// 끝점 스냅 — 수동 벽은 파서의 코너 스냅·junction 치유를 건너뛴다(주입이 그 뒤라서).
// 그래서 여기서 사람이 **보면서** 붙인다. 파서가 나중에 몰래 옮기는 것보다 낫다.
function snapPoint(pt, skipEid){
  const source=(skipEid&&recByEid(skipEid))||activeFloorSource();
  const floor=source?{level:source.level,floor:source.floor,elevation:source.elevation,z_base:source.z_base}:{};
  return MepEdit.snapPoint(pt,wallsAll().filter(onEditFloor),Object.assign({skipEid,
    screenToleranceMm:SNAP_PX*px2world(),physicalToleranceMm:50},floor)).point;
}

function splitWall(rec, pt){
  const parts=MepEdit.splitPolyline(rec,pt); if(parts.length!==2) return;
  const w1=manualWall(clOf(parts[0]),rec), w2=manualWall(clOf(parts[1]),rec);
  delWall(rec); addWall(w1); addWall(w2); sel2=[w1.eid,w2.eid];
  refreshEdits(); fillPanel(w1,'wall'); render2();
}

function joinWalls(r1, r2){
  sel2=[];
  if(!r1 || !r2 || r1===r2){ render2(); return; }
  const joined=MepEdit.joinPolylines(r1,r2,{joinTolerance:JOIN_TOL_MM,coordinateTolerance:1});
  if(!joined){
    document.getElementById('hint2d').textContent='결합 불가: 같은 층의 직선 벽이며 끝점과 모든 치수·속성이 같아야 합니다. 빈 간격은 벽으로 메우지 않습니다.';
    render2(); return;
  }
  const nw=manualWall(clOf(joined),r1);
  delWall(r1); delWall(r2); addWall(nw); sel2=[nw.eid];
  refreshEdits(); fillPanel(nw,'wall'); render2();
}

svg2.addEventListener('pointerdown', ev=>{
  if(ACTION_LOCK) return;
  const pt=evtWorld(ev); if(!pt) return;
  const t=ev.target;
  if(t.classList && t.classList.contains('hnd')){
    dragging={eid:t.dataset.eid, i:+t.dataset.i};
    svg2.setPointerCapture(ev.pointerId); ev.preventDefault(); return;
  }
  const eid=(t.dataset && t.dataset.eid) || null;
  if(mode2==='draw'){
    const p=snapPoint(pt,null);
    if(!drawFrom){ drawFrom=p;
      document.getElementById('hint2d').textContent='끝점을 클릭하세요 (Esc 취소).'; }
    else { addWall(manualWall([drawFrom,p],null)); drawFrom=null;
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
  const pt=evtWorld(ev); if(!pt) return;
  const cl=clOf(rec).map(p=>p.slice());
  cl[dragging.i]=snapPoint(pt, rec.eid);
  dragging.preview=cl;
  for(const e of g2.querySelectorAll('polyline.w'))
    if(e.dataset.eid===dragging.eid) e.setAttribute('points', cl.map(p=>p[0]+','+p[1]).join(' '));
  for(const h of g2.querySelectorAll('.hnd'))
    if(h.dataset.eid===dragging.eid && +h.dataset.i===dragging.i){
      h.setAttribute('cx',cl[dragging.i][0]); h.setAttribute('cy',cl[dragging.i][1]);
    }
});

svg2.addEventListener('pointerup', ()=>{
  if(!dragging) return;
  const rec=recByEid(dragging.eid), cl=dragging.preview, movedIndex=dragging.i;
  dragging=null;
  if(!rec || !cl){ return; }
  const old=clOf(rec);
  if(Math.hypot(cl[movedIndex][0]-old[movedIndex][0],cl[movedIndex][1]-old[movedIndex][1])<1){ render2(); return; }
  let nw;
  if(edits[rec.eid]&&edits[rec.eid].added){
    nw=MepEdit.movePolylinePoint(rec,movedIndex,cl[movedIndex]);
    edits[rec.eid]=Object.assign({},edits[rec.eid],{record:nw});
  } else {
    nw=manualWall(cl,rec); delWall(rec); addWall(nw);
  }
  sel2=[nw.eid];
  refreshEdits(); fillPanel(nw,'wall'); render2();
});

svg2.addEventListener('wheel', ev=>{
  ev.preventDefault();
  const k=ev.deltaY>0?1.15:0.87, c=evtWorld(ev);
  if(!c) return;
  vb={x:c[0]-(c[0]-vb.x)*k, y:c[1]-(c[1]-vb.y)*k, w:vb.w*k, h:vb.h*k};
  applyVB(); render2();
}, {passive:false});

window.addEventListener('keydown', ev=>{
  if(ACTION_LOCK) return;
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

function setTab(mode){
  const two=mode===true||mode==='edit', three=mode==='three';
  const wasTwo=svg2.classList.contains('on');
  document.getElementById('t2d').classList.toggle('on', two);
  document.getElementById('t3d').classList.toggle('on', three);
  document.getElementById('tlinked').classList.toggle('on', !two&&!three);
  document.getElementById('tools2d').classList.toggle('on', two);
  svg2.classList.toggle('on', two);
  document.getElementById('editSourceControls').classList.toggle('on',two);
  document.getElementById('linkedWorkspace').style.display=two?'none':'grid';
  document.getElementById('linkedWorkspace').classList.toggle('three-only',three);
  if(two){
    if(!wasTwo && selCat==='wall' && selRec && recByEid(selRec.eid)){
      sel2=[selRec.eid]; setSourceFloor(floor2d.value); focusSourceEid(selRec.eid);
      if(sourceVB){ vb={...sourceVB}; applyVB(); }
    }
    if(!vb) fitView(); render2();
  }
  else requestAnimationFrame(()=>{cam.aspect=W()/H();cam.updateProjectionMatrix();renderer.setSize(W(),H());if(!three){renderSourceBackdrop();renderSourceOverlay();}});
}
document.getElementById('t2d').addEventListener('click', ()=>setTab(true));
document.getElementById('tlinked').addEventListener('click', ()=>setTab(false));
document.getElementById('t3d').addEventListener('click', ()=>setTab('three'));

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
  for(const key of ['level','floor','elevation','attrs']) if(rec[key]!=null) orec[key]=MepEdit.clone(rec[key]);
  const eid=MepEdit.makeId('om',undefined,rec.level);
  orec.eid=eid;
  edits[eid]={added:true, category:'opening', record: JSON.parse(JSON.stringify(orec))};
  buildOpening(orec);
  document.getElementById('placehint').textContent=`✓ ${s.mark} 배치됨 @(${Math.round(cx)},${Math.round(cy)}). 계속 클릭 가능.`;
  refreshEdits();
}

// ── 고아 수정 복구 ────────────────────────────────────────────────────────
let orphanSuggestions=((DATA.edits_report||{}).relink_suggestions||[]);
const deferredOrphans=new Set();
let ACTION_LOCK=false;
function setActionLock(locked){
  ACTION_LOCK=locked;
  for(const control of document.querySelectorAll('button,input,select')) control.disabled=locked;
  svg2.style.pointerEvents=locked?'none':'';
  renderer.domElement.style.pointerEvents=locked?'none':'';
}
function escHtml(value){ return String(value==null?'':value).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
function renderOrphans(){
  const list=orphanSuggestions.filter(s=>!deferredOrphans.has(s.orphan));
  document.getElementById('norphans').textContent=list.length;
  const box=document.getElementById('orphans');
  if(!list.length){ box.innerHTML='없음'; return; }
  box.innerHTML=list.map(s=>{
    const details=s.details||[];
    const options=(s.candidates||[]).map((eid,i)=>{
      const d=details.find(x=>x.eid===eid)||details[i]||{};
      const suffix=d.distance_mm!=null?' — '+Math.round(d.distance_mm)+'mm':'';
      return '<option value="'+escHtml(eid)+'">'+escHtml(eid+suffix)+'</option>';
    }).join('');
    const reason=options?'빨간 점선은 이전 형상입니다. 후보를 확인한 뒤 연결하세요.':
      escHtml((s.details&&s.details.reason)||s.reason||'위치 정보가 없어 안전한 후보를 만들 수 없습니다.');
    return '<div class="orphan" data-orphan="'+escHtml(s.orphan)+'"><b>'+escHtml(s.orphan)+'</b><div>'+reason+'</div>'+
      (options?'<select class="candidate">'+options+'</select><button class="relink">재연결</button>':'')+
      '<button class="defer">나중에</button><button class="discard">폐기</button></div>';
  }).join('');
}
async function orphanRequest(path,body){
  if(!SAVE_QUEUE){ setSaveStatus('error','서버 연결이 없어 고아 수정을 변경할 수 없습니다.'); return; }
  setActionLock(true);
  try{
    await SAVE_QUEUE.idle();
    if(SAVE_QUEUE.error()) return false;
    setSaveStatus('saving','저장 중…');
    const response=await apiPost(path,Object.assign({project_id:RUNTIME.project_id,
      expected_revision:SAVE_QUEUE.revision()},body));
    SAVE_QUEUE.replaceRemote(response); RUNTIME.revision=response.revision;
    replaceObject(edits,response.edits||{});
    if(response.geometry&&response.geometry.elements)
      BASE_ELEMENTS=MepEdit.deriveBaseElements(response.geometry.elements,response.edits||{},excludedEditIds(response.geometry.edits_report));
    CANONICAL_PRESENTATION=response.geometry?MepEdit.clone(response.geometry.elements):null;
    mergeServerPresentation(response);
    try{ localStorage.removeItem(LSKEY); }catch(e){} LSKEY=MepEdit.backupKey(RUNTIME);
    _lastSnap=JSON.stringify(edits); syncEffective(); renderOrphans();
    setSaveStatus('saved','서버에 저장됨');
    return true;
  }catch(error){
    if(error.status===409) setSaveStatus('conflict','저장 충돌 — 서버 최신 상태를 덮어쓰지 않았습니다.');
    else setSaveStatus('error','저장 실패 — '+error.message);
    return false;
  }finally{
    setActionLock(false);
  }
}
document.getElementById('orphans').addEventListener('change',ev=>{
  if(!ev.target.classList.contains('candidate')) return;
  sel2=[ev.target.value]; setTab(true); render2();
});
document.getElementById('orphans').addEventListener('click',async ev=>{
  const row=ev.target.closest('.orphan'); if(!row) return;
  const orphan=row.dataset.orphan;
  if(ev.target.classList.contains('relink')) await orphanRequest('/relink',{orphan,target:row.querySelector('.candidate').value});
  if(ev.target.classList.contains('discard')) await orphanRequest('/discard',{eid:orphan});
  if(ev.target.classList.contains('defer') && await orphanRequest('/defer',{eid:orphan})){
    deferredOrphans.add(orphan); renderOrphans(); if(svg2.classList.contains('on')) render2();
  }
});

// ── 툴바 ───────────────────────────────────────────────────
const b3d=document.getElementById('b3d'), btop=document.getElementById('btop');
b3d.onclick=()=>{fit(); b3d.classList.add('active'); btop.classList.remove('active');};
btop.onclick=()=>{topView(); btop.classList.add('active'); b3d.classList.remove('active');};
document.getElementById('bfit').onclick=()=>{
  if(svg2.classList.contains('on')){fitView();render2();} else fit();
};
document.getElementById('bconf').onclick=e=>{useConf=!useConf; e.target.classList.toggle('active',useConf); applyEditVisuals();};
document.getElementById('bwire').onclick=e=>{wire=!wire; e.target.classList.toggle('active',wire); meshes.forEach(m=>m.material.wireframe=wire);};

// ── 패널 정보 ──────────────────────────────────────────────
document.getElementById('src').textContent = (DATA.source||'').split(/[\\/]/).pop();
if(RUNTIME) document.getElementById('roundtripHelp').innerHTML='수정은 이 프로젝트에 자동 저장됩니다.<br>서버 확인 뒤에만 “저장됨”으로 표시합니다.';
const _restored = loadEdits();
if(_restored!==null){
  _lastSnap=JSON.stringify(edits);
  EFFECTIVE_ELEMENTS=MepEdit.materializeElements(BASE_ELEMENTS,edits);
}
if(_restored!==null) persistAndQueue();
else setSaveStatus(RUNTIME?'saved':'unsaved',RUNTIME?'서버에 저장됨':'서버 연결 없음 — edits.json 다운로드 사용');
renderOrphans();
renderWarnings();
if(_restored!==null){
  const b=document.getElementById('warnbox');
  b.insertAdjacentHTML('afterbegin',
    '<div style="background:#2a3a2a">이 브라우저에 저장돼 있던 수정 '+_restored.count
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
const E=EFFECTIVE_ELEMENTS; const cnt=Object.entries(E).filter(([,v])=>v.length).map(([k,v])=>`${k} ${v.length}`).join(' · ');
document.getElementById('counts').innerHTML=`<div class="muted">${cnt||'요소 없음'}</div>`;
const wp=DATA.wall_pairing||{};
document.getElementById('legend').innerHTML =
  '<details><summary>색상 범례</summary>'+Object.entries(CAT_COLOR).map(([k,c])=>`<div><span class="sw" style="background:#${c.toString(16).padStart(6,'0')}"></span>${k}</div>`).join('')
  + `<div style="margin-top:6px;border-top:1px solid #3a3f4a;padding-top:6px">신뢰도 색(벽): `
  + Object.entries(PAIR_COLOR).map(([k,c])=>`<span class="sw" style="background:#${c.toString(16).padStart(6,'0')}"></span>${k} `).join('')+`</div></details>`;

fitSource(); renderSourceBackdrop(); rebuild(); refreshEditsOnly(); fit();
addEventListener('resize', ()=>{
  cam.aspect=W()/H(); cam.updateProjectionMatrix(); renderer.setSize(W(),H());
  if(svg2.classList.contains('on') && !dragging) render2();
});
(function loop(){ requestAnimationFrame(loop); controls.update(); renderer.render(scene,cam); })();
