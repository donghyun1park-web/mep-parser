import './style.css';
import MepEdit from 'mep-edit';
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { buildReviewEntries, reviewBannerText, bridgeRequest, routineCandidateIds,
         escHtml, reviewRowHtml, batchButtonHtml, modelBounds, deriveSectionRange, floorKeyOf, isZVisible, reconcileSection, uniqueByEid, fitDistance, recordOnEditFloor, editBackdropState, REASON,
         boqHeightBasisText, boqBodyHtml, createHistory,
         joinedEids, joinedText, systemLineText, isTypingTarget, clashMarkerSpecs, reviewCounts, changeSummaryText,
         levelHeightDeclared, shownClashItems, legendHtml } from './review_logic.js';
import { screenToDrawing, drawingUnitsPerPixel } from './svg_coordinates.js';
import { linearMepGeometry, footprintMepGeometry, mepPropertyKeys } from './mep_preview_geometry.js';

const DATA = JSON.parse(document.getElementById('mep-data').textContent);
const RUNTIME = DATA.project_runtime || null;
let EDITS_REPORT=DATA.edits_report || {};
let CLASH_REVIEW=DATA.clash_review || {};
let CONNECTIVITY=DATA.mep_connectivity || {};
let CONSTRUCTION_RULES=DATA.construction_rules || {};
let SUGGESTIONS_APPLY=DATA.suggestions_apply || [];
let BOQ=DATA.boq || {};
let LEVEL_HEIGHT_DECLARED=levelHeightDeclared(DATA);
function excludedEditIds(report){ return [...((report||{}).orphaned||[]),...((report||{}).ambiguous||[])]; }
let BASE_ELEMENTS = MepEdit.deriveBaseElements(DATA.elements || {},DATA.project_edits || {},excludedEditIds(DATA.edits_report));
let CANONICAL_PRESENTATION = MepEdit.clone(DATA.elements || {});
let EFFECTIVE_ELEMENTS = MepEdit.materializeElements(BASE_ELEMENTS, DATA.project_edits || {});
window.addEventListener('error', e => {
  if (String(e.message).includes('three') || String(e.filename).includes('unpkg'))
    document.getElementById('err').style.display='block';
});

const {gcDim,gcZRange,gcWidthOf,gcCcw,gcBeamRings,gcMepDimensions,gcSectionShape,ELEV_CATS} = globalThis.MepContract;

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
const PAIR_COLOR = { paired:0x4caf50, single:0xff9800, single_offset:0xf44336, closed:0x26a69a, infill:0x9fa8da };

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
// 3D 에서만 숨긴 카테고리(범례 클릭). 저장하지 않는다 — 모델·물량·간섭 계산은 그대로다.
const hiddenCats = new Set();
// 장면 안의 표지 — 간섭 고리(검토 목록에 보이는 것만)와 방금 찾아간 끊긴 끝. meshes 밖에 두어 클릭·맞춤·단면 범위에 안 섞인다.
let clashMarks=[], focusMark=null;
function markerTexture(kind){
  const c=document.createElement('canvas'); c.width=c.height=32;
  const g=c.getContext('2d'); g.strokeStyle=g.fillStyle='#fff'; g.lineWidth=5; g.beginPath(); g.arc(16,16,11,0,Math.PI*2);
  if(kind==='dot') g.fill(); else g.stroke();
  return new THREE.CanvasTexture(c);
}
function makeMarks(points, color, opacity, kind){
  const geo=new THREE.BufferGeometry();
  geo.setAttribute('position',new THREE.Float32BufferAttribute(points.flatMap(p=>[...toM(p),(p[2]||0)*S]),3));
  const mat=new THREE.PointsMaterial({color, size:18, sizeAttenuation:false, map:markerTexture(kind), transparent:true, opacity,
    alphaTest:.1, depthTest:false, clippingPlanes:sectionEnabled?[sectionPlane]:[]});
  const obj=new THREE.Points(geo,mat); obj.renderOrder=10; scene.add(obj); return obj;
}
function dropMark(obj){ if(!obj) return; scene.remove(obj); obj.geometry.dispose(); obj.material.map?.dispose(); obj.material.dispose(); }
function renderClashMarks(items){
  clashMarks.forEach(dropMark); clashMarks=[];
  const specs=clashMarkerSpecs(items).filter(c=>c.z!=null);
  const solid=specs.filter(c=>!c.assumed).map(c=>[c.x,c.y,c.z]), assumed=specs.filter(c=>c.assumed).map(c=>[c.x,c.y,c.z]);
  if(solid.length) clashMarks.push(makeMarks(solid,0xff3b3b,1,'ring'));
  if(assumed.length) clashMarks.push(makeMarks(assumed,0xff3b3b,.45,'ring'));   // 가정 높이로 나온 간섭은 옅게(평면과 같다)
}
function showFocusMark(point){ dropMark(focusMark); focusMark=point?makeMarks([point],0xff4d4d,1,'dot'):null; }
let mepRenderWarnings=[];
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
  const sill = (isWin ? (rec.overrides?.sill ?? rec.sill ?? 900) : 0) + (rec.z_base||0);  // 편집값 먼저 — 납품 IFC(ifc_builder._opening_box)와 같은 순서
  const depth = (rec.host_width ?? (P.wall?.width) ?? 250);
  const dir = rec.host_dir || [1,0];
  const geo=new THREE.BoxGeometry(wmm*S, depth*S, hmm*S);
  const m=addMesh(geo,'opening',rec,(sill+hmm/2)*S, {trans:true, op:0.5});
  const q=toM(c); m.position.x=q[0]; m.position.y=q[1];
  m.position.z=(sill+hmm/2)*S; m.rotation.z=Math.atan2(dir[1],dir[0]);
}
// 계약 v3 path3d 는 납품 IFC 빌더·Blender·Pascal 이 읽지만 이 미리보기는 아직 안 읽는다(js_constants 에
// route/path3d 함수가 없다) — 경사·수직 구간을 평균 높이의 평평한 관으로 보여준다. 조용히 다르게
// 그리는 대신, 다르다는 사실을 말한다(배수 구배 opt-in 이 처음으로 이 자리를 만든다).
function pathIsSloped(rec){
  const segs=rec.path3d&&rec.path3d.segments; if(!segs||!segs.length) return false;
  const z0=segs[0].start[2], eps=1e-6;
  return segs.some(s=>Math.abs(s.start[2]-z0)>eps||Math.abs(s.end[2]-z0)>eps);
}
function buildMepLinear(rec, cat){
  try {
    const dims=gcMepDimensions(cat,rec,P), range=gcZRange(cat,rec,P);
    const elev=(range[0]+range[1])*S/2;
    if(pathIsSloped(rec))
      mepRenderWarnings.push(`${rec.eid||rec.layer||cat}: 3D 미리보기는 경사·수직 구간을 평균 높이의 `
        +`평평한 관으로 보여준다 — 실제 형상은 납품 IFC 를 뷰어(Bonsai 등)로 확인`);
    if(rec.geometry_mode==='footprint'){
      const outer=rec.points||rec.outer||rec.footprint?.outer;
      if(!outer||outer.length<3) throw new Error('Footprint boundary is missing');
      const shape=shapeFrom(outer);
      for(const ring of (rec.holes||rec.footprint?.holes||[])){
        const hole=new THREE.Path();
        ring.forEach((p,i)=>{ const xy=toM(p); if(i) hole.lineTo(...xy); else hole.moveTo(...xy); });
        hole.closePath(); shape.holes.push(hole);
      }
      addMesh(footprintMepGeometry(THREE,shape,range,S),cat,rec,range[0]*S);
      return;
    }
    const raw=rec.points||rec.centerline;
    if(!raw||raw.length<2) throw new Error('Centerline is missing');
    const pts=raw.filter((point,index)=>!index||Math.hypot(point[0]-raw[index-1][0],point[1]-raw[index-1][1])>1e-8);
    const vectors=pts.map(p=>new THREE.Vector3(...toM(p),0));
    for(const piece of linearMepGeometry(THREE,vectors,dims,S)){
      const m=addMesh(piece.geometry,cat,rec,elev);
      [m.position.x,m.position.y]=piece.position; m.rotation.z=piece.rotation;
    }
  } catch(error) {
    mepRenderWarnings.push(`${rec.eid||rec.layer||cat}: 3D 표시 보류 · ${error.message}`);
  }
}
function buildEquip(rec){
  const pts=rec.points||[], range=gcZRange('equipment',rec,P);
  if(pts.length>=3){ const geo=footprintMepGeometry(THREE,shapeFrom(pts),range,S); addMesh(geo,'equipment',rec,range[0]*S); }
}

function rebuild(){
  for(const m of meshes){ scene.remove(m); m.geometry.dispose(); m.material.dispose(); }
  meshes.length=0;
  mepRenderWarnings=[];
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
  selected=highlight3D(selectedEid);
  if(selectedEid&&!selected){ updateSourceSelection(null); fillPanel(null,null); }
  updateSectionBounds();
  renderSourceOverlay();
  renderReview();
  renderWarnings();
}

// 맞춤은 **보이는 것**에 — 범례로 벽을 숨기고 맞추면 설비에 맞는다. 다 숨겼으면 전체에.
function shownMeshes(){ const shown=meshes.filter(m=>m.visible); return shown.length?shown:meshes; }
function fit(){
  const box=new THREE.Box3(); shownMeshes().forEach(m=>box.expandByObject(m));
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
  const box=new THREE.Box3(); shownMeshes().forEach(m=>box.expandByObject(m));
  frameBox(box,new THREE.Vector3(0,-.0001,1));
}

// ── 선택/수정 ──────────────────────────────────────────────
const ray=new THREE.Raycaster(), mouse=new THREE.Vector2();
let selected=null;
// 검토 사유 → 사람이 읽을 수 있는 한 줄 + 조치. 코드 한 단어만 보여 주면
// 사용자는 무엇을 볼지 모른다(실측: 검토 대상 180개 중 154개가 사유 없음이었다).
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
// 3D 강조는 한 곳에서 — 고른 부재(노랑)와, 설비면 **이음으로 이어진 무리**(옅은 초록). 무리는 도면이 이어
// 그렸거나 사람이 확정한 joints 만 따른다(joinedEids) — 연결 후보는 건너지 않는다. 방향은 그리지 않는다:
// 평면도에 흐름 방향이 없다(HighTopo 의 관 표면 흐름 애니메이션을 가져오지 않은 이유, edits-and-preview.md).
// 무리는 emissive 가 아니라 **색을 바꾼다** — emissive 는 더하기라 하늘색 배관 위에서 안 보였다(브라우저 QA). 분홍은 어느 카테고리 색과도 겹치지 않는다(트레이가 초록이다).
const HL_SELECTED=0x333300, HL_JOINED_COLOR=0xff7ad9, HL_PARTNER=0x004a5a;
const ROUTE_CATS=['pipe','duct','tray'];
function highlight3D(eid){
  for(const mesh of meshes){
    mesh.material.emissive?.setHex(0x000000);
    if(mesh.userData.joined){ mesh.userData.joined=false; mesh.material.color.setHex(baseColorOf(mesh)); }
  }
  if(!eid) return null;
  let first=null;
  for(const mesh of meshes) if(mesh.userData.rec.eid===eid){ mesh.material.emissive?.setHex(HL_SELECTED); first=first||mesh; }
  if(first && ROUTE_CATS.includes(first.userData.cat)){
    const group=new Set(joinedEids(EFFECTIVE_ELEMENTS,eid));
    for(const mesh of meshes){
      const other=mesh.userData.rec.eid;
      if(other!==eid&&group.has(other)){ mesh.userData.joined=true; mesh.material.color.setHex(HL_JOINED_COLOR); }
    }
  }
  return first;
}
// 간섭의 상대 구조부재 — 고른 배관을 지우지 않고 **덧칠**한다(select 가 먼저 전부 지운다).
function markPartner(eid){
  if(!eid) return;
  for(const mesh of meshes) if(mesh.userData.rec.eid===eid && mesh.visible) mesh.material.emissive?.setHex(HL_PARTNER);
}
function select(m){
  highlight3D(null); showFocusMark(null);
  selected=m;
  if(!m){ fillPanel(null, null); updateSourceSelection(null); follow2D(null,null); return; }
  const eid=m.userData.rec.eid;
  const canonical=uniqueByEid(Object.values(EFFECTIVE_ELEMENTS).flat(),eid);
  if(!canonical){ selected=null; fillPanel(null,null); updateSourceSelection(null); document.getElementById('noSel').textContent='같은 EID를 가진 부재가 여러 개여서 안전하게 선택할 수 없습니다: '+eid; return; }
  highlight3D(eid);
  updateSourceSelection(eid);
  if(canonical.level!=null) setSourceFloor(String(canonical.level));
  focusSourceEid(eid);
  fillPanel(canonical, m.userData.cat);
  follow2D(eid, m.userData.cat);
}
function viewDirection(){ const dir=cam.position.clone().sub(controls.target); return dir.lengthSq()>1e-12?dir:undefined; }
function selectEid(eid,{focus=false}={}){
  const canonical=uniqueByEid(Object.values(EFFECTIVE_ELEMENTS).flat(),eid);
  if(!canonical){ fillPanel(null,null); document.getElementById('noSel').textContent='같은 EID를 가진 부재가 여러 개이거나 대상이 없어 선택할 수 없습니다: '+eid; return false; }
  // 검토 행이 가리킨 부재를 범례로 숨겨 뒀으면 그 종류를 다시 보인다 — 안 그러면 클릭이 조용히 죽는다.
  const hiddenHere=[...new Set(meshes.filter(m=>m.userData.rec.eid===eid && hiddenCats.has(m.userData.cat)).map(m=>m.userData.cat))];
  if(hiddenHere.length){ hiddenHere.forEach(c=>hiddenCats.delete(c)); applyEditVisuals(); renderLegend(); }
  const matching=meshes.filter(m=>m.userData.rec.eid===eid && m.visible);
  if(!matching.length) return false;
  select(matching[0]);
  if(sectionEnabled){ sectionEnabled=false; document.getElementById('sectionEnabled').checked=false; applySection(); }
  if(focus){
    const box=new THREE.Box3(); matching.forEach(m=>box.expandByObject(m));
    // 지금 보고 있는 각도 그대로 다가간다 — 매번 아이소로 되돌리면 검토 행을 넘길 때마다 방향을 잃는다.
    // frameBox 가 인자를 normalize 로 바꾸므로 복사본을 넘긴다(viewDirection 이 새 벡터를 만든다).
    frameBox(box, viewDirection());
  }
  return true;
}
// 점 하나가 문제인 행(끊긴 끝) — 부재 전체가 아니라 그 점 둘레 4m 로 다가가고 점을 찍어 둔다.
function focusPoint(at){
  const [x,y]=toM(at);
  frameBox(new THREE.Box3().setFromCenterAndSize(new THREE.Vector3(x,y,(at[2]||0)*S),new THREE.Vector3(4,4,4)), viewDirection());
  showFocusMark(at);
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
  const mep=ROUTE_CATS.includes(cat)||cat==='equipment';
  document.getElementById('e_sysrow').style.display=mep?'flex':'none';
  document.getElementById('e_sys').textContent=mep?systemLineText(rec):'';
  document.getElementById('e_joinrow').style.display=ROUTE_CATS.includes(cat)?'flex':'none';
  document.getElementById('e_join').textContent=ROUTE_CATS.includes(cat)?joinedText(joinedEids(EFFECTIVE_ELEMENTS,rec.eid).length):'';
  sel.value=(edits[eid]?.category)||cat;
  if(['pipe','duct','tray'].includes(cat)) {
    try { const dims=gcMepDimensions(cat,rec,P); document.getElementById('e_w').value=dims.diameter??dims.width_mm??''; document.getElementById('e_h').value=dims.height_mm??''; }
    catch { document.getElementById('e_w').value=''; document.getElementById('e_h').value=''; }
  } else {
    document.getElementById('e_w').value=edits[eid]?.overrides?.width ?? rec.overrides?.width ?? rec.width_detected ?? rec.width ?? '';
    document.getElementById('e_h').value=edits[eid]?.overrides?.height ?? rec.overrides?.height ?? rec.overrides?.thickness ?? '';
  }
  // 높이(elevation) 편집은 첫 배포에서 MEP(배관·덕트·트레이·장비)만 — 벽/기둥의 z_base 는
  // floor_z 가 없는 레코드에서 층 배정을 옮길 수 있어(geom_contract.floor_z) 뒤로 미룬다.
  const zwrap=document.getElementById('e_zrow');
  if(ELEV_CATS.includes(cat)){
    zwrap.style.display='block';
    document.getElementById('e_z').value=edits[eid]?.overrides?.elevation ?? rec.overrides?.elevation ?? rec.elevation ?? '';
  } else {
    zwrap.style.display='none';
    document.getElementById('e_z').value='';
  }
  document.getElementById('e_del').checked=!!edits[eid]?.deleted;
  // 평면 탭이 다루는 카테고리일 때만 — 기둥·슬래브·개구부 등은 넘길 곳이 없다.
  document.getElementById('editInPlan').hidden=!EDIT_CATS.includes(cat);
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
  // 창 위아래 벽은 옆 벽의 레이어·두께를 물려받았을 뿐이다 — 그 높이(700 등)로 레이어 규칙을 권하면 층 전체 벽이 낮아진다
  if(!rec || cat!=='wall' || !rec.layer || rec.source==='opening_infill') return '';
  const w=rec.width_detected;
  if(w==null) return '';
  const same=(EFFECTIVE_ELEMENTS.wall||[]).filter(r=>r.source!=='opening_infill' &&
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
  if(ELEV_CATS.includes(cat) && rec.overrides?.elevation!=null)
    out.push('높이: 인스펙터에서 선언 '+Math.round(rec.overrides.elevation)+'mm');
  // 프로젝트 기본값이 채운 필드 — 카테고리 추정이 아니라 사람이 프로젝트를 열 때 적은 선언이다.
  if(rec.declaration_basis && Object.keys(rec.declaration_basis).length)
    out.push('프로젝트 기본값: '+Object.keys(rec.declaration_basis).join(', '));
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
  const zraw=document.getElementById('e_z').value.trim();
  // 빈 칸 = 도면값으로 되돌린다(null → applyProperties 가 overrides 에서 지운다). 숫자 아닌 값은 무시한다.
  const z=ELEV_CATS.includes(cat) ? (zraw===''?null:parseFloat(zraw)) : undefined;
  const e=MepEdit.applyProperties(edits[eid],rec,selCat,{
    category:cat,width:w,height:h,
    zKey:ELEV_CATS.includes(cat)?'elevation':undefined, z,
    deleted:document.getElementById('e_del').checked,
    reviewResolved:document.getElementById('e_review').checked
  });
  if(['pipe','duct','tray'].includes(cat)) {
    const [widthKey,heightKey]=mepPropertyKeys(cat,gcSectionShape(cat,rec)), ov=e.added?e.record.overrides:e.overrides;
    if(ov){ delete ov.width; delete ov.height; if(Number.isFinite(w)) ov[widthKey]=w; if(heightKey&&Number.isFinite(h)) ov[heightKey]=h; }
  }
  edits[eid]=e;
  if(!Object.keys(e).filter(k=>k[0]!=='_').length) delete edits[eid];
  refreshEdits();
});
// 3D 는 기하 보기 전용이다 — 끝점·경로 편집은 평면 탭에서만. 이 버튼은 탭 전환(setTab)에
// 얹는다: 카테고리·층·선택 동기화는 setTab 이 이미 한다(2D 평면 탭 절 참조).
document.getElementById('editInPlan').addEventListener('click', ()=>{ setTab(true); });
// 되돌리기·다시 실행 — 스택 로직은 `review_logic.createHistory`(순수 함수, node 테스트가 잠근다).
// 시간 디바운스가 아니라 '한 동작 = 한 단계' 다. 무동작(값이 같음)은 쌓지 않는다.
const HISTORY=createHistory(JSON.stringify(edits));
function pushUndo(){ HISTORY.push(JSON.stringify(edits)); }
function applySnapshot(snapshot){
  if(snapshot==null) return;
  for(const k of Object.keys(edits)) delete edits[k];
  Object.assign(edits, JSON.parse(snapshot));
  persistAndQueue(); syncEffective();
}
window.addEventListener('keydown', ev=>{
  if(ACTION_LOCK) return;
  if(isTypingTarget(ev.target)) return;   // 칸 안의 Ctrl+Z 는 그 칸의 글자를 되돌린다 — 편집 전체가 아니다
  if(!(ev.ctrlKey||ev.metaKey)) return;
  const k=ev.key.toLowerCase();
  if(k==='z' && !ev.shiftKey){ ev.preventDefault(); applySnapshot(HISTORY.undo()); }
  else if(k==='y' || (k==='z' && ev.shiftKey)){ ev.preventDefault(); applySnapshot(HISTORY.redo()); }
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
// 저장 뒤 "무엇이 바뀌었나" — 기준은 **저장 사이클이 시작될 때**(사용자 동작) 뜨고, 서버 응답을 받을 때마다
// 다시 잰다. 편집 저장은 응답 뒤 남은 수정을 한 번 더 보내므로(onAck 의 unchanged=false) 그때는 기준을
// 비우지 않는다 — 비우면 두 번째 응답이 첫 문장을 '변화 없음' 으로 덮는다.
let CHANGE_BASE=null, LAST_CHANGE='', RESEND_PENDING=false;
function markChangeBase(){ if(!CHANGE_BASE) CHANGE_BASE=reviewCounts(CLASH_REVIEW,CONNECTIVITY); }
function setSaveStatus(kind,text){
  if(kind==='saved'){
    if(LAST_CHANGE) text+=' · '+LAST_CHANGE;
    if(!RESEND_PENDING) CHANGE_BASE=null;
  }
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
  CLASH_REVIEW=response.geometry.clash_review || {};
  CONNECTIVITY=response.geometry.mep_connectivity || {};
  CONSTRUCTION_RULES=response.geometry.construction_rules || {};
  SUGGESTIONS_APPLY=response.geometry.suggestions_apply || [];
  BOQ=response.geometry.boq || {};
  LEVEL_HEIGHT_DECLARED=levelHeightDeclared(response.geometry);
  orphanSuggestions=((response.geometry.edits_report)||{}).relink_suggestions||[];
  renderOrphans();
  renderBoq();
  LAST_CHANGE=CHANGE_BASE?changeSummaryText(CHANGE_BASE,reviewCounts(CLASH_REVIEW,CONNECTIVITY)):'';
}
function renderBoq(){
  document.getElementById('boqHeightBasis').textContent=boqHeightBasisText(LEVEL_HEIGHT_DECLARED);
  document.getElementById('boqBody').innerHTML=boqBodyHtml(BOQ);
}
let SAVE_QUEUE=null;
if(RUNTIME){
  SAVE_QUEUE=MepEdit.createSaveQueue({revision:RUNTIME.revision,acknowledged:DATA.project_edits||{},
    send:(snapshot,revision)=>apiPost('/edits',{project_id:RUNTIME.project_id,expected_revision:revision,edits:snapshot}),
    onAck:(response,sent)=>{
      const unchanged=JSON.stringify(edits)===JSON.stringify(sent);
      RESEND_PENDING=!unchanged;
      if(response.geometry&&response.geometry.elements)
        BASE_ELEMENTS=MepEdit.deriveBaseElements(response.geometry.elements,response.edits||{},excludedEditIds(response.geometry.edits_report));
      if(unchanged) replaceObject(edits,response.edits||{});
      else { mergeServerMetadata(response.edits||{}); queueMicrotask(()=>SAVE_QUEUE.enqueue(edits)); }
      CANONICAL_PRESENTATION=unchanged&&response.geometry?MepEdit.clone(response.geometry.elements):null;
      mergeServerPresentation(response);
      try{ localStorage.removeItem(LSKEY); }catch(e){}
      RUNTIME.revision=response.revision; LSKEY=MepEdit.backupKey(RUNTIME);
      if(!unchanged) saveLocal();
      HISTORY.rebase(JSON.stringify(edits)); syncEffective();
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
  markChangeBase();
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
      } else if(['pipe','duct','tray'].includes(v.category)){
        const cl=r.centerline||r.points||[];
        const L=cl.length>1?Math.hypot(cl[cl.length-1][0]-cl[0][0],
                                       cl[cl.length-1][1]-cl[0][1]):0;
        const ov=r.overrides||{};
        const dim=ov.diameter!=null?'Ø'+Math.round(ov.diameter)
                 :(ov.width_mm!=null||ov.height_mm!=null)?Math.round(ov.width_mm||0)+'×'+Math.round(ov.height_mm||0):'';
        lbl=(CAT_LABEL[v.category]||v.category)+' '+Math.round(L)+'mm'+(dim?' · '+dim:'');
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
// 기본은 **부재에** 맞춘다 — 설계변경 표·안내선이 도면 밖으로 크게 뻗어 평면이 손톱만 해지는 것을 막는다.
// '맞춤' 버튼을 다시 누르면 층 전체(원본 선 포함)로 오간다.
let sourceFitWide=false;
function fitSource(wide){
  const floor=selectedSourceFloor();
  const model=wide?null:modelBounds(EFFECTIVE_ELEMENTS,floor&&floor.id);
  sourceVB=model||sourceViewBox(); applySourceVB();
}
function toggleSourceFit(){ sourceFitWide=!sourceFitWide; fitSource(sourceFitWide); }
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
document.getElementById('sourceFit').addEventListener('click',toggleSourceFit);
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
  for(const mark of [...clashMarks,focusMark]) if(mark) mark.material.clippingPlanes=sectionEnabled?[sectionPlane]:[];
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
  const entries=buildReviewEntries(EFFECTIVE_ELEMENTS,EDITS_REPORT,CLASH_REVIEW.items||[],CONNECTIVITY,CONSTRUCTION_RULES.items||[],SUGGESTIONS_APPLY);
  const floors=[...new Set(entries.map(x=>x.floor).filter(Boolean))].sort();
  const categories=[...new Set(entries.map(x=>x.category).filter(Boolean))].sort();
  const keepFloor=reviewFloor.value, keepCategory=reviewCategory.value;
  reviewFloor.innerHTML='<option value="">모든 층</option>'+floors.map(x=>`<option>${escHtml(x)}</option>`).join('');
  reviewCategory.innerHTML='<option value="">모든 종류</option>'+categories.map(x=>`<option>${escHtml(x)}</option>`).join('');
  reviewFloor.value=floors.includes(keepFloor)?keepFloor:''; reviewCategory.value=categories.includes(keepCategory)?keepCategory:'';
  const shown=entries.filter(x=>(!reviewFloor.value||x.floor===reviewFloor.value)&&(!reviewCategory.value||x.category===reviewCategory.value));
  document.getElementById('reviewCount').textContent=shown.length;
  // 목록 전체가 가정 높이 위에 서 있으면 줄마다의 표시로는 안 보인다 — 맨 위에 한 줄.
  const banner=document.getElementById('reviewBanner');
  banner.textContent=reviewBannerText(CLASH_REVIEW.summary||{},CONNECTIVITY.summary||{});
  banner.hidden=!banner.textContent;
  // 일상 후보(직선·엘보 · 규격 그대로)는 한 번에 확정한다 — 확인은 사람이 하되 26번 누르게 하지 않는다.
  const batch=document.getElementById('reviewBatch');
  batch.innerHTML=batchButtonHtml(routineCandidateIds(CONNECTIVITY),!!RUNTIME);
  batch.hidden=!batch.innerHTML;
  document.getElementById('reviewList').innerHTML=
    shown.length?shown.map(x=>reviewRowHtml(x,!!RUNTIME)).join(''):'없음';
  renderClashMarks(shownClashItems(CLASH_REVIEW.items||[],shown));
}
async function confirmGap(ids,confirmed){
  markChangeBase();
  setSaveStatus('saving','저장 중…');
  try{
    const response=await apiPost('/bridges',bridgeRequest([].concat(ids),confirmed,RUNTIME));
    RUNTIME.revision=response.revision;
    if(response.geometry&&response.geometry.elements)
      BASE_ELEMENTS=MepEdit.deriveBaseElements(response.geometry.elements,response.edits||{},excludedEditIds(response.geometry.edits_report));
    mergeServerPresentation(response);
    syncEffective();
    setSaveStatus('saved','서버에 저장됨');
  }catch(error){
    setSaveStatus('error','이음 확정 실패 — 프로젝트는 그대로입니다: '+error.message);
  }
  renderReview();
}
document.getElementById('reviewBatch').addEventListener('click',ev=>{
  if(ACTION_LOCK||!ev.target.closest('.confirm-gap')) return;
  confirmGap(routineCandidateIds(CONNECTIVITY),true);
});
async function applySuggestion(suggestion){
  markChangeBase();
  setSaveStatus('saving','저장 중…');
  try{
    const response=await apiPost('/layer-rule',
      {project_id:RUNTIME.project_id, expected_revision:RUNTIME.revision, suggestion});
    RUNTIME.revision=response.revision;
    if(response.geometry&&response.geometry.elements)
      BASE_ELEMENTS=MepEdit.deriveBaseElements(response.geometry.elements,response.edits||{},excludedEditIds(response.geometry.edits_report));
    mergeServerPresentation(response);
    syncEffective();
    setSaveStatus('saved','서버에 저장됨 — layer_map.csv 갱신, 다시 해석됨');
  }catch(error){
    setSaveStatus('error','규칙 적용 실패 — 프로젝트는 그대로입니다: '+error.message);
  }
  renderReview();
}
reviewFloor.addEventListener('change',renderReview); reviewCategory.addEventListener('change',renderReview);
document.getElementById('reviewList').addEventListener('click',ev=>{
  if(ACTION_LOCK) return;
  const applyBtn=ev.target.closest('.apply-suggest');
  if(applyBtn){ applySuggestion(JSON.parse(applyBtn.dataset.apply)); return; }
  const gap=ev.target.closest('.confirm-gap');
  if(gap){ confirmGap(gap.dataset.gap,!gap.dataset.confirmed); return; }
  const row=ev.target.closest('.review-item'); if(!row) return;
  if(row.dataset.eid){
    setTab(false);
    const at=(row.dataset.at||'').split(',').map(Number), point=at.length===3&&at.every(Number.isFinite)?at:null;
    if(selectEid(row.dataset.eid,{focus:!point})){ markPartner(row.dataset.struct); if(point) focusPoint(point); }
  }
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
let mode2='select', cat2='wall', sel2=[], vb=null, dragging=null, drawFrom=null;
const SNAP_PX=12;          // 스냅은 화면 기준(집기 편하게)
const JOIN_TOL_MM=600;     // 결합은 실치수 기준(줌과 무관해야 한다)
const EDIT_CATS=['wall','pipe','duct','tray'];   // 평면 탭에서 고를 수 있는 카테고리
const CAT_LABEL={wall:'벽',pipe:'배관',duct:'덕트',tray:'트레이'};
const HINT2={
  wall:{select:'벽을 클릭 → 끝점을 끌어 옮깁니다. Delete 삭제 · Ctrl+Z 되돌리기 · Ctrl+Y 다시 · Shift 직교 · Space 드래그 이동.',
        split:'벽 위의 나눌 지점을 클릭합니다.',
        join:'이어 붙일 벽 2개를 차례로 클릭합니다.',
        draw:'시작점과 끝점을 클릭해 벽을 그립니다. Shift 로 직교, Esc 취소.'},
  pipe:{select:'배관을 클릭 → 끝점을 끌어 옮깁니다. Delete 삭제 · Ctrl+Z 되돌리기 · Ctrl+Y 다시 · Shift 직교 · Space 드래그 이동.',
        split:'배관 위의 나눌 지점을 클릭합니다.',
        join:'이어 붙일 배관 2개를 차례로 클릭합니다.',
        draw:'시작점과 끝점을 클릭해 배관을 그립니다. Shift 로 직교, Esc 취소.'},
  duct:{select:'덕트를 클릭 → 끝점을 끌어 옮깁니다. Delete 삭제 · Ctrl+Z 되돌리기 · Ctrl+Y 다시 · Shift 직교 · Space 드래그 이동.',
        split:'덕트 위의 나눌 지점을 클릭합니다.',
        join:'이어 붙일 덕트 2개를 차례로 클릭합니다.',
        draw:'시작점과 끝점을 클릭해 덕트를 그립니다. Shift 로 직교, Esc 취소.'},
  tray:{select:'트레이를 클릭 → 끝점을 끌어 옮깁니다. Delete 삭제 · Ctrl+Z 되돌리기 · Ctrl+Y 다시 · Shift 직교 · Space 드래그 이동.',
        split:'트레이 위의 나눌 지점을 클릭합니다.',
        join:'이어 붙일 트레이 2개를 차례로 클릭합니다.',
        draw:'시작점과 끝점을 클릭해 트레이를 그립니다. Shift 로 직교, Esc 취소.'}};
const JOIN_FAIL={
  wall:'결합 불가: 같은 층의 직선 벽이며 끝점과 모든 치수·속성이 같아야 합니다. 빈 간격은 벽으로 메우지 않습니다.',
  pipe:'결합 불가: 같은 층의 직선 배관이며 끝점과 모든 치수·속성이 같아야 합니다. 빈 간격은 배관으로 메우지 않습니다.',
  duct:'결합 불가: 같은 층의 직선 덕트며 끝점과 모든 치수·속성이 같아야 합니다. 빈 간격은 덕트로 메우지 않습니다.',
  tray:'결합 불가: 같은 층의 직선 트레이며 끝점과 모든 치수·속성이 같아야 합니다. 빈 간격은 트레이로 메우지 않습니다.'};
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

function editable2(){
  // 평면 탭은 현재 선택한 카테고리(cat2)만 편집 대상으로 다룬다. 창 위아래 벽은 개구부에서
  // 파생되고 같은 평면 선에 아래·위 두 개가 겹쳐 하나만 집히며 스냅도 흐트러진다 — 3D 에서 고친다.
  // 외곽선(footprint) 덕트는 축선이 없어 경로 편집 대상이 아니다(mep_network 도 건너뛴다).
  const records=EFFECTIVE_ELEMENTS[cat2]||[];
  return cat2==='wall' ? records.filter(r=>r.source!=='opening_infill')
                        : records.filter(r=>r.geometry_mode!=='footprint');
}
function clOf(r){ return r.centerline || r.points || []; }
function isDel(r){ return !!(edits[r.eid] && edits[r.eid].deleted); }
function recByEid(eid){ return editable2().find(r=>r.eid===eid); }
function nearestOnFloor(point){
  // 새로 그린 설비의 치수·높이 기본값을 물려받을 이웃 — 같은 층, 같은 카테고리에서 가장 가까운 것.
  if(!point) return null;
  let best=null, bestD=Infinity;
  for(const r of editable2()){
    if(!onEditFloor(r)) continue;
    for(const p of clOf(r)){
      const d=Math.hypot(p[0]-point[0],p[1]-point[1]);
      if(d<bestD){ bestD=d; best=r; }
    }
  }
  return best;
}

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
  if(cat2!=='wall'){
    // 설비를 고르면 벽은 배경으로만 — 편집 대상은 아니지만 자리를 잡을 때 보여야 한다.
    for(const r of (EFFECTIVE_ELEMENTS.wall||[])){
      if(!onEditFloor(r) || r.source==='opening_infill') continue;
      const cl=clOf(r); if(cl.length<2) continue;
      g2.appendChild(mk2('polyline',{points:cl.map(p=>p[0]+','+p[1]).join(' '),
                                      class:'w-bg','stroke-width':Math.max(30,gcWidthOf(r,P,'wall'))}));
    }
  }
  for(const rec of editable2()){
    if(!onEditFloor(rec)) continue;
    const cl=clOf(rec); if(cl.length<2) continue;
    const w=strokeWidthOf2(rec);
    const cls=['w',cat2];
    if(isDel(rec)) cls.push('del');
    else if(sel2.indexOf(rec.eid)>=0) cls.push('sel');
    else if(rec.pairing==='manual') cls.push('man');
    else if(rec.needs_review) cls.push('rev');
    const e=mk2('polyline',{points:cl.map(p=>p[0]+','+p[1]).join(' '),
                            class:cls.join(' '),'stroke-width':w});
    e.dataset.eid=rec.eid; g2.appendChild(e);
  }
  const k=px2world();
  // 설비 연결(보기 전용) — 끊긴 끝 · 이음 후보(확정 아님) · 다른 계통 끝 맞닿음. 모델은 바뀌지 않는다.
  for(const gap of [...(CONNECTIVITY.candidates||[]),...(CONNECTIVITY.conflicts||[]).map(c=>({...c,conflict:true}))]){
    if(!onEditFloor({level:gap.level})||(gap.points||[]).length<2) continue;
    g2.appendChild(mk2('polyline',{points:gap.points.map(p=>p[0]+','+p[1]).join(' '),class:gap.conflict?'gap-conflict':'gap-cand'}));
  }
  for(const end of CONNECTIVITY.open_ends||[])
    if(onEditFloor({level:end.level})) g2.appendChild(mk2('circle',{cx:end.at[0],cy:end.at[1],r:5*k,class:'mep-end '+end.status}));
  // 간섭 지점(보기 전용) — 이 파싱 결과의 판정이다. 가정 높이로 나온 것은 옅게.
  for(const c of clashMarkerSpecs(CLASH_REVIEW.items||[]))
    if(onEditFloor({level:c.level})) g2.appendChild(mk2('circle',{cx:c.x,cy:c.y,r:9*k,class:'clash'+(c.assumed?' assumed':'')}));
  for(const rec of editable2()){
    if(!onEditFloor(rec) || sel2.indexOf(rec.eid)<0 || isDel(rec)) continue;
    const cl=clOf(rec);
    for(const i of cl.map((_,i)=>i)){
      const h=mk2('circle',{cx:cl[i][0],cy:cl[i][1],r:SNAP_PX*k*0.6,class:'hnd'});
      h.dataset.eid=rec.eid; h.dataset.i=i; g2.appendChild(h);
    }
  }
  document.getElementById('hint2d').textContent = (HINT2[cat2]||HINT2.wall)[mode2] || '';
}

// 선택은 한 줄기 — 평면에서 고르면 3D 도 같은 부재를, 3D(나란히 보기)에서 고르면 평면도 같은 부재를 밝힌다.
// selectEid 는 쓰지 않는다: 단면을 끄고 원본 창을 옮겨 편집 중 화면이 튄다.
function markIn3D(eid){ selected=highlight3D(eid); showFocusMark(null); updateSourceSelection(eid||null); }
function follow2D(eid, cat){
  if(!svg2.classList.contains('on')) return;
  if(eid && EDIT_CATS.includes(cat) && cat2!==cat){ cat2=cat; if(cat2Select) cat2Select.value=cat2; }
  sel2=(eid && EDIT_CATS.includes(cat) && recByEid(eid))?[eid]:[];
  render2();
}

function strokeWidthOf2(rec){
  if(cat2==='wall') return Math.max(30, gcWidthOf(rec,P,'wall'));
  try{ const dims=gcMepDimensions(cat2,rec,P); return Math.max(30, dims.diameter ?? dims.width_mm ?? 100); }
  catch{ return 30; }
}

function manualRec(points, src){
  if(cat2==='wall'){
    src=src||activeFloorSource();
    return MepEdit.makeManualWall(points,src,{
      width:Math.round(src?gcWidthOf(src,P,'wall'):((P.wall&&P.wall.width)||200)),height:wallH});
  }
  // 이웃(끌어온 src, 없으면 같은 층·같은 카테고리에서 가장 가까운 것)의 치수·높이를 물려받는다.
  // 아무것도 없으면 geom_contract 기본값(DEFAULT_DIMS)과 층 소스의 높이로 떨어진다.
  const base=src||nearestOnFloor(points[0])||{};
  let dims={};
  try{ dims=gcMepDimensions(cat2,base,P); }catch{}
  const seed=Object.assign({},base);
  if(seed.elevation==null) seed.elevation=activeFloorSource().elevation ?? 0;
  if(seed.level==null) seed.level=floor2d.value;
  return MepEdit.makeManualRecord(cat2, points, seed, dims);
}
function addWall(rec){ edits[rec.eid]={added:true, category:cat2, record:rec}; }
function stripBaseEid(eid){ for(const cat of Object.keys(BASE_ELEMENTS)) BASE_ELEMENTS[cat]=(BASE_ELEMENTS[cat]||[]).filter(r=>r.eid!==eid); }
function delWall(rec){
  if(edits[rec.eid] && edits[rec.eid].added){ stripBaseEid(rec.eid); delete edits[rec.eid]; }
  else edits[rec.eid]=Object.assign({}, edits[rec.eid], {deleted:true});
}

// 끝점 스냅 — 수동 벽은 파서의 코너 스냅·junction 치유를 건너뛴다(주입이 그 뒤라서).
// 그래서 여기서 사람이 **보면서** 붙인다. 파서가 나중에 몰래 옮기는 것보다 낫다.
// Shift 직교는 **스냅보다 먼저** 건다(edit_geometry.orthoPoint 주석 참조).
function snapPoint(pt, skipEid, orthoFrom){
  if(orthoFrom) pt=MepEdit.orthoPoint(orthoFrom, pt);
  const source=(skipEid&&recByEid(skipEid))||activeFloorSource();
  // MEP 는 elevation 을 층 프로브에서 뺀다 — 넣으면 sameFloor(edit_geometry.js)가 level/floor 없을 때
  // elevation 으로 폴백해, 높이가 다른 배관끼리는 서로 안 붙는다.
  const floor=source?(cat2==='wall'?{level:source.level,floor:source.floor,elevation:source.elevation,z_base:source.z_base}
                                    :{level:source.level,floor:source.floor}):{};
  return MepEdit.snapPoint(pt,editable2().filter(onEditFloor),Object.assign({skipEid,
    screenToleranceMm:SNAP_PX*px2world(),physicalToleranceMm:50},floor)).point;
}

function splitWall(rec, pt){
  const parts=MepEdit.splitPolyline(rec,pt); if(parts.length!==2) return;
  const w1=manualRec(clOf(parts[0]),rec), w2=manualRec(clOf(parts[1]),rec);
  delWall(rec); addWall(w1); addWall(w2); sel2=[w1.eid,w2.eid];
  refreshEdits(); fillPanel(w1,cat2); markIn3D(w1.eid); render2();
}

function joinWalls(r1, r2){
  sel2=[];
  if(!r1 || !r2 || r1===r2){ render2(); return; }
  const joined=MepEdit.joinPolylines(r1,r2,{joinTolerance:JOIN_TOL_MM,coordinateTolerance:1});
  if(!joined){
    document.getElementById('hint2d').textContent=JOIN_FAIL[cat2]||JOIN_FAIL.wall;
    render2(); return;
  }
  const nw=manualRec(clOf(joined),r1);
  delWall(r1); delWall(r2); addWall(nw); sel2=[nw.eid];
  refreshEdits(); fillPanel(nw,cat2); markIn3D(nw.eid); render2();
}

// 화면 이동(팬) — 확대해 놓으면 편집할 자리로 갈 방법이 없었다. 원본 DXF 창의 `sourceDrag` 와
// 같은 방식이다(중버튼이나 Space 를 누른 채 드래그). 선택·그리기와 겹치지 않게 조건을 먼저 본다.
let spaceHeld=false, pan2=null;
window.addEventListener('keydown', ev=>{ if(ev.code==='Space'&&svg2.classList.contains('on')){ spaceHeld=true;
  if(ev.target===document.body) ev.preventDefault(); } });
window.addEventListener('keyup', ev=>{ if(ev.code==='Space') spaceHeld=false; });
window.addEventListener('blur', ()=>{ spaceHeld=false; pan2=null; });

svg2.addEventListener('pointerdown', ev=>{
  if(ACTION_LOCK) return;
  const pt=evtWorld(ev); if(!pt) return;
  if(ev.button===1 || spaceHeld){
    pan2={last:pt}; svg2.setPointerCapture(ev.pointerId); ev.preventDefault(); return;
  }
  const t=ev.target;
  if(t.classList && t.classList.contains('hnd')){
    dragging={eid:t.dataset.eid, i:+t.dataset.i};
    svg2.setPointerCapture(ev.pointerId); ev.preventDefault(); return;
  }
  const eid=(t.dataset && t.dataset.eid) || null;
  if(mode2==='draw'){
    const p=snapPoint(pt,null,ev.shiftKey?drawFrom:null);
    if(!drawFrom){ drawFrom=p;
      document.getElementById('hint2d').textContent='끝점을 클릭하세요 (Esc 취소).'; }
    else { addWall(manualRec([drawFrom,p],null)); drawFrom=null;
      refreshEdits(); render2(); }
    return;
  }
  if(!eid){ if(mode2!=='join'){ sel2=[]; fillPanel(null,null); markIn3D(null); render2(); } return; }
  const rec=recByEid(eid); if(!rec) return;
  if(mode2==='split'){ splitWall(rec, pt); return; }
  if(mode2==='join'){
    if(sel2.indexOf(eid)<0) sel2.push(eid);
    if(sel2.length===2) joinWalls(recByEid(sel2[0]), recByEid(sel2[1]));
    render2(); return;
  }
  sel2=[eid]; fillPanel(rec,cat2); markIn3D(eid); render2();
});

svg2.addEventListener('pointermove', ev=>{
  if(pan2){
    const pt=evtWorld(ev); if(!pt) return;
    vb={...vb, x:vb.x-(pt[0]-pan2.last[0]), y:vb.y-(pt[1]-pan2.last[1])};
    applyVB(); pan2.last=evtWorld(ev); return;
  }
  if(!dragging) return;
  const rec=recByEid(dragging.eid); if(!rec) return;
  const pt=evtWorld(ev); if(!pt) return;
  const cl=clOf(rec).map(p=>p.slice());
  // 직교 기준은 **끌지 않는 이웃 끝점** 이다 — 그래야 그 구간이 수평·수직으로 선다.
  const neighbour=cl[dragging.i===0?1:dragging.i-1];
  cl[dragging.i]=snapPoint(pt, rec.eid, ev.shiftKey?neighbour:null);
  dragging.preview=cl;
  for(const e of g2.querySelectorAll('polyline.w'))
    if(e.dataset.eid===dragging.eid) e.setAttribute('points', cl.map(p=>p[0]+','+p[1]).join(' '));
  for(const h of g2.querySelectorAll('.hnd'))
    if(h.dataset.eid===dragging.eid && +h.dataset.i===dragging.i){
      h.setAttribute('cx',cl[dragging.i][0]); h.setAttribute('cy',cl[dragging.i][1]);
    }
});

svg2.addEventListener('pointerup', ()=>{
  if(pan2){ pan2=null; return; }
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
    nw=manualRec(cl,rec); delWall(rec); addWall(nw);
  }
  sel2=[nw.eid];
  refreshEdits(); fillPanel(nw,cat2); markIn3D(nw.eid); render2();
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
  if(isTypingTarget(ev.target)) return;   // 인스펙터 폭 칸의 Delete 는 글자를 지운다 — 고른 벽이 아니다
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
const cat2Select=document.getElementById('cat2d');
if(cat2Select) cat2Select.addEventListener('change', ()=>{
  cat2=cat2Select.value; dragging=null; drawFrom=null; sel2=[];
  fillPanel(null,null); render2();
});

function setTab(mode){
  // 'side' = 평면 편집 + 3D 나란히. 편집 경로는 그대로다 — 3D 는 계속 보기 전용이고,
  // `syncEffective→rebuild()` 가 이미 매 편집마다 3D 를 다시 만든다(저장을 기다리지 않는다).
  const side=mode==='side';
  const two=mode===true||mode==='edit'||side, three=mode==='three';
  const wasTwo=svg2.classList.contains('on');
  document.getElementById('t2d').classList.toggle('on', two&&!side);
  document.getElementById('tside').classList.toggle('on', side);
  document.getElementById('t3d').classList.toggle('on', three);
  document.getElementById('tlinked').classList.toggle('on', !two&&!three);
  document.getElementById('tools2d').classList.toggle('on', two);
  svg2.classList.toggle('on', two);
  document.getElementById('view').classList.toggle('side', side);
  document.getElementById('editSourceControls').classList.toggle('on',two&&!side);
  document.getElementById('linkedWorkspace').style.display=(two&&!side)?'none':'grid';
  document.getElementById('linkedWorkspace').classList.toggle('three-only',three);
  if(two){
    // 3D 에서 고른 부재를 평면으로 넘긴다 — 카테고리를 먼저 맞춰야 editable2() 가 찾는다.
    if(!wasTwo && EDIT_CATS.includes(selCat) && selRec){
      cat2=selCat;
      if(cat2Select) cat2Select.value=cat2;
      if(recByEid(selRec.eid)){
        sel2=[selRec.eid]; setSourceFloor(floor2d.value); focusSourceEid(selRec.eid);
        if(sourceVB){ vb={...sourceVB}; applyVB(); }
      }
    }
    if(!vb) fitView(); render2();
  }
  if(!two || side) requestAnimationFrame(()=>{cam.aspect=W()/H();cam.updateProjectionMatrix();renderer.setSize(W(),H());if(!three&&!side){renderSourceBackdrop();renderSourceOverlay();}});
}
document.getElementById('t2d').addEventListener('click', ()=>setTab(true));
document.getElementById('tside').addEventListener('click', ()=>setTab('side'));
document.getElementById('tlinked').addEventListener('click', ()=>setTab(false));
document.getElementById('t3d').addEventListener('click', ()=>setTab('three'));

function baseColorOf(m){
  const eid=m.userData.rec.eid; const e=eid&&edits[eid];
  return (e&&e.category) ? (CAT_COLOR[e.category]??0xffffff) : colorFor(m.userData.cat,m.userData.rec);
}
function applyEditVisuals(){
  for(const m of meshes){
    const eid=m.userData.rec.eid; const e=eid&&edits[eid];
    m.visible = !(e&&e.deleted) && !hiddenCats.has(m.userData.cat);
    m.material.color.setHex(m.userData.joined?HL_JOINED_COLOR:baseColorOf(m));
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
    z_base:(rec.floor_z ?? rec.z_base ?? 0)};   // 창 위 벽을 클릭해도 창은 그 층 바닥 기준
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
    markChangeBase();
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
    HISTORY.rebase(JSON.stringify(edits)); syncEffective(); renderOrphans();
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
  HISTORY.rebase(JSON.stringify(edits));
  EFFECTIVE_ELEMENTS=MepEdit.materializeElements(BASE_ELEMENTS,edits);
}
if(_restored!==null) persistAndQueue();
else setSaveStatus(RUNTIME?'saved':'unsaved',RUNTIME?'서버에 저장됨':'서버 연결 없음 — edits.json 다운로드 사용');
renderOrphans();
renderWarnings();
renderBoq();
if(_restored!==null){
  const b=document.getElementById('warnbox');
  b.insertAdjacentHTML('afterbegin',
    '<div style="background:#2a3a2a">이 브라우저에 저장돼 있던 수정 '+_restored.count
    +'건을 복원했습니다 (Ctrl+Z 로 되돌리기).</div>');
}

// 파서가 낸 경고·통계를 화면에 옮긴다. 종전엔 CLI 로그에만 있었고, 3D 를 보는
// 사람은 무엇이 의심스러운지 알 수 없었다.
function renderWarnings(){
  // 분류 의심이 맨 위다 — 검토 대기 수십 건의 **원인**이 한 줄로 해결되는 경우가 있다.
  const suspect=(DATA.column_layers_like_wall||[]).map(c=>
    `[분류 의심] '${c.layer}' 은 기둥으로 분류됐지만 ${c.lines}선 중 ${c.paired}개가 `
    +`${Math.round(c.spacing_mm)}mm 짝 — 벽 레이어일 수 있다. layer_map 에 붙여 넣을 줄: ${c.suggested_row}`);
  const evidence=(DATA.suggestions||[]).filter(s=>s.source==='layer'&&s.evidence&&s.evidence.wall_like).map(s=>
    `미매핑 '${s.layer}' 은 벽처럼 그려졌다 — ${s.geom_reason}`);
  const quiet=(DATA.suggestions||[]).filter(s=>s.source==='layer'&&s.evidence&&!s.evidence.wall_like);
  const w=[...suspect,...evidence,...(DATA.warnings||[]).filter(x=>!String(x).startsWith('[분류 의심]')),
           ...mepRenderWarnings];
  if(quiet.length)
    w.push(`벽이 아닌 미매핑 레이어 ${quiet.length}개(평행 짝 없음 — 마감선·해치·안내선): `
           +quiet.slice(0,10).map(s=>s.layer).join(' · ')+(quiet.length>10?' · …':''));
  for(const c of (DATA.width_conflicts||[]).slice(0,6))
    w.push('두께 불일치: '+c.layer+' 선언 '+Math.round(c.declared)
           +' != 실측 '+Math.round(c.detected)+'mm x '+c.count+'개');
  const q=DATA.qa||{};
  if(q.face_coverage_pct!=null)
    w.push('면선 회수율 '+Math.round(q.face_coverage_pct)+'% '
           +(q.face_coverage_pct<90?'— 낮으면 페어링 실패(opts pair_max 확인)':'')
           // 회수율은 **벽으로 매핑된** 레이어만 센 값이다 — 그 옆에 모델 밖의 벽 같은 선을 같이 말한다.
           +(q.wall_like_unmapped_m?` (벽으로 매핑된 레이어 기준 — 모델 밖에 벽처럼 보이는 선 ${q.wall_like_unmapped_m}m)`:''));
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
const LEGEND_EXTRA=`<div style="margin-top:6px;border-top:1px solid #3a3f4a;padding-top:6px">신뢰도 색(벽): `
  + Object.entries(PAIR_COLOR).map(([k,c])=>`<span class="sw" style="background:#${c.toString(16).padStart(6,'0')}"></span>${k} `).join('')+`</div>`
  + `<div style="margin-top:6px;border-top:1px solid #3a3f4a;padding-top:6px">강조: 노랑 빛 = 고른 부재 · 분홍 = 이음으로 이어진 설비 · 청록 빛 = 간섭 상대 구조부재</div>`
  + `<div>표지: 빨간 고리 = 검토 목록에 보이는 간섭(옅으면 가정 높이) · 빨간 점 = 방금 찾아간 끊긴 끝</div>`;
function renderLegend(){
  const box=document.getElementById('legend'), open=!!box.querySelector('details')?.open;
  box.innerHTML=legendHtml(CAT_COLOR,hiddenCats,open,LEGEND_EXTRA);
}
document.getElementById('legend').addEventListener('click', ev=>{
  const row=ev.target.closest('.legend-cat'); if(!row) return;
  const cat=row.dataset.cat;
  if(hiddenCats.has(cat)) hiddenCats.delete(cat); else hiddenCats.add(cat);
  // 숨긴 부재를 고른 채로 두지 않는다 — 인스펙터가 안 보이는 것을 가리키게 된다.
  if(selected && hiddenCats.has(selected.userData.cat)) select(null);
  applyEditVisuals(); updateSectionBounds(); renderLegend();
});
renderLegend();

renderSourceBackdrop(); rebuild(); refreshEditsOnly(); fit(); fitSource();   // 부재가 선 뒤에 맞춘다
addEventListener('resize', ()=>{
  cam.aspect=W()/H(); cam.updateProjectionMatrix(); renderer.setSize(W(),H());
  if(svg2.classList.contains('on') && !dragging) render2();
});
(function loop(){ requestAnimationFrame(loop); controls.update(); renderer.render(scene,cam); })();
