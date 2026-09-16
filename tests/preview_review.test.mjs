import test from 'node:test';
import assert from 'node:assert/strict';
import {
  buildReviewEntries,
  reviewBannerText,
  bridgeRequest,
  routineCandidateIds,
  deriveSectionRange,
  floorKeyOf,
  isZVisible,
  reconcileSection,
  uniqueByEid,
  fitDistance,
  recordOnEditFloor,
  editBackdropState,
} from '../frontend/src/review_logic.js';

test('camera framing fits the bounding sphere in both split and wide viewports', () => {
  const radius=12, fov=45;
  for (const aspect of [.35, 1, 2.5]) {
    const distance=fitDistance(radius,fov,aspect);
    const halfVertical=fov*Math.PI/360;
    const halfHorizontal=Math.atan(Math.tan(halfVertical)*aspect);
    const angularRadius=Math.asin(radius/distance);
    assert.ok(angularRadius<halfVertical && angularRadius<halfHorizontal);
  }
  assert.ok(fitDistance(radius,fov,.35)>fitDistance(radius,fov,2.5));
});

test('review queue is deterministic, deduplicated, and keeps actionable state', () => {
  const elements = {
    wall: [
      {eid:'w:z', level:1, needs_review:true, review_reason:'single', edit_diagnostics:['stale signature']},
      {eid:'w:a', level:0, review_required:true, review_reason:'thin_pair'},
    ],
    pipe: [{eid:'p:a', level:0, edit_diagnostics:[{code:'bad_elevation', message:'높이 확인'}]}],
  };
  const report = {
    orphaned:['old:b'], ambiguous:['old:a'],
    relink_suggestions:[{orphan:'old:b', candidates:['w:z']}],
  };
  assert.deepEqual(buildReviewEntries(elements, report).map(x => [x.key,x.kind,x.eid,x.floor,x.action]), [
    ['element:w:a','element','w:a','0','select'],
    ['element:p:a','element','p:a','0','select'],
    ['element:w:z','element','w:z','1','select'],
    ['ambiguous:old:a','ambiguous',null,'','open-orphan'],
    ['orphan:old:b','orphan',null,'','open-orphan'],
  ]);
});

test('clash rows lead the review queue in the given order and select the MEP element', () => {
  const clashes=[
    {id:'b',kind:'wall_penetration',action:'벽 관통 — 슬리브·개구 확인',at:[2500,100],z:[2250,2550],level:'B',
     basis:'assumed',assumed:['height','sill'],
     struct:{eid:'A:w:1',category:'wall',width_mm:200},mep:{eid:'B:d:1',system:'SA',size:'400×300'}},
    {id:'a',kind:'under_wall',action:'바닥 매립 설비가 벽 아래를 지남',at:[1000,100],z:[70,86],
     struct:{eid:'A:w:1',category:'wall',width_mm:200},mep:{eid:'A:p:1',size:'Ø15.9'}}];
  const elements={wall:[{eid:'A:w:9',needs_review:true,review_reason:'thin_pair'}]};
  const rows=buildReviewEntries(elements,{},clashes);
  assert.deepEqual(rows.map(x=>[x.kind,x.eid,x.category]),
    [['clash','B:d:1','간섭'],['clash','A:p:1','간섭'],['element','A:w:9','wall']]);
  assert.match(rows[0].reason, /\(2500, 100\) z 2250~2550 · wall A:w:1 두께 200mm ↔ SA 400×300/);
});

test('connection candidates follow clashes, name the gap and partner, and conflicts name both systems', () => {
  const connectivity={
    candidates:[{id:'g1',kind:'elbow',eids:['d:a','d:b'],systems:['SA','SA'],sizes:['110×54','204×60'],size_change:true,
      gap_mm:212.1,points:[[1000,0],[1150,0],[1150,150]]}],
    conflicts:[{id:'g2',kind:'straight',eids:['d:sa','d:ra'],systems:['SA','RA'],sizes:['110×54','110×54'],
      gap_mm:200,points:[[1000,5000],[1200,5000]]}]};
  const clashes=[{id:'c',action:'벽 관통',at:[0,0],z:[0,1],struct:{eid:'w:1'},mep:{eid:'d:a'}}];
  const rows=buildReviewEntries({wall:[{eid:'w:9',needs_review:true}]},{},clashes,connectivity);
  assert.deepEqual(rows.map(x=>[x.kind,x.eid,x.category]),
    [['clash','d:a','간섭'],['gap','d:a','연결 후보'],['gap','d:sa','계통 충돌'],['element','w:9','wall']]);
  assert.match(rows[1].reason,/엘보 이음 후보 · 틈 212mm · \(1000, 0\) ↔ d:b · SA 110×54 → 204×60 · 규격 바뀜/);
  assert.match(rows[2].reason,/직선형으로 맞닿음 · SA ↔ RA · 틈 200mm/);
  assert.deepEqual(rows.map(x=>x.confirm),[undefined,{id:'g1',confirmed:false},null,undefined]);
});

test('a confirmed joint stays listed so it can be undone, even though it left the candidate list', () => {
  const connectivity={candidates:[],conflicts:[],bridges:{applied:[{id:'g1',kind:'straight',eids:['d:c','d:d'],
    systems:['SA','SA'],sizes:['110×54','110×54'],gap_mm:200,points:[[1000,3000],[1200,3000]]}]}};
  const [row]=buildReviewEntries({},{},[],connectivity);
  assert.deepEqual([row.kind,row.category,row.eid,row.confirm],['gap','확정한 이음','d:c',{id:'g1',confirmed:true}]);
  assert.match(row.reason,/직선 이음으로 확정함 · 틈 200mm · \(1000, 3000\) ↔ d:d · SA 110×54/);
});

test('review queue excludes resolved records unless their acknowledgement is stale', () => {
  const elements={wall:[
    {eid:'done',review_required:true,review_resolved:true,edit_diagnostics:['old diagnostic']},
    {eid:'stale',review_resolved:true,review_ack_stale:true},
  ]};
  assert.deepEqual(buildReviewEntries(elements,{}).map(x=>x.eid),['stale']);
});

test('review queue uses stale_reviews from the edit report', () => {
  assert.deepEqual(buildReviewEntries({}, {stale_reviews:['w:2','w:1']}).map(x=>x.key),
    ['stale:w:1','stale:w:2']);
});

test('section range includes floors and every geometry category without mutating records', () => {
  const wall={eid:'w',z_base:3000};
  const elements={wall:[wall],pipe:[{eid:'p',elevation:7600,diameter:200}]};
  const range=deriveSectionRange(elements,[{z:-500},{z:6000}],(cat,rec)=>
    cat==='wall'?[rec.z_base,rec.z_base+2800]:[rec.elevation-100,rec.elevation+100]);
  assert.deepEqual(range,{min:-500,max:7700});
  assert.deepEqual(wall,{eid:'w',z_base:3000});
});

test('section range has a useful fallback for empty projects', () => {
  assert.deepEqual(deriveSectionRange({},[],()=>{throw new Error('unused');}),{min:0,max:3000});
});

test('dynamic section bounds preserve and clamp the current height at exact millimetres', () => {
  assert.deepEqual(reconcileSection({min:0,max:3000},1555),{min:0,max:3000,height:1555});
  assert.deepEqual(reconcileSection({min:-125,max:8100},9000),{min:-125,max:8100,height:8100});
});

test('clipped geometry cannot be selected above the active section height', () => {
  assert.equal(isZVisible(2499,2500,true),true);
  assert.equal(isZVisible(2501,2500,true),false);
  assert.equal(isZVisible(9000,2500,false),true);
});

test('floor keys accept explicit level and otherwise remain honest', () => {
  assert.equal(floorKeyOf({level:0}),'0');
  assert.equal(floorKeyOf({floor:'B1'}),'B1');
  assert.equal(floorKeyOf({}),'');
});

test('duplicate EIDs are rejected instead of silently choosing the first mesh', () => {
  assert.equal(uniqueByEid([{eid:'a'},{eid:'a'}],'a'),null);
  assert.deepEqual(uniqueByEid([{eid:'a'},{eid:'b'}],'b'),{eid:'b'});
});

test('editing shows only the active floor, even when different floors overlap', () => {
  assert.equal(recordOnEditFloor({level:'L1'},'L1',2),true);
  assert.equal(recordOnEditFloor({level:'L2'},'L1',2),false);
  assert.equal(recordOnEditFloor({floor:0},'0',2),true);
  assert.equal(recordOnEditFloor({},'main',1),true);
  assert.equal(recordOnEditFloor({},'L1',2),false);
});

test('DXF backdrop uses the exact floor and retains already transformed coordinates', () => {
  const floor={id:'L2',status:'available',primitives:[{points:[[1100,2100],[6000,2100]]}]};
  const drawing={floors:[{id:'L1',primitives:[{points:[[100,100],[5000,100]]}]},floor]};
  const before=JSON.stringify(drawing);
  const state=editBackdropState(drawing,'L2');
  assert.equal(state.floor,floor);
  assert.equal(state.available,true);
  assert.deepEqual(state.notices,[]);
  assert.deepEqual(state.floor.primitives[0].points,[[1100,2100],[6000,2100]]);
  assert.equal(JSON.stringify(drawing),before);
  assert.equal(editBackdropState(drawing,'missing').floor,null);
  assert.equal(editBackdropState(drawing,'missing').available,false);
});

test('missing and partial DXF remain disclosed in the edit view', () => {
  assert.equal(editBackdropState(null,'main').available,false);
  assert.ok(editBackdropState(null,'main').notices.length);
  const state=editBackdropState({floors:[{id:'main',status:'partial',primitives:[{points:[[0,0],[1,1]]}],
    omitted:{TEXT:3,TRUNCATED:1},warnings:['Source extraction stopped']}]},'main');
  assert.equal(state.available,true);
  assert.ok(state.notices.some(x=>x.includes('TEXT 3')));
  assert.ok(state.notices.some(x=>x.includes('제한')));
  assert.ok(state.notices.includes('Source extraction stopped'));
});

test('the banner says how much of the list stands on assumed heights, and nothing when none does', () => {
  assert.equal(reviewBannerText({total:24, assumed_basis:0}, {candidates:26, assumed_basis:{candidates:0}}), '');
  assert.equal(reviewBannerText({}, {}), '');
  const text=reviewBannerText({total:24, assumed_basis:24}, {candidates:26, assumed_basis:{candidates:3}});
  assert.match(text, /간섭 24건 중 가정 높이 24건 · 연결 후보 26건 중 가정 3건/);
  assert.match(text, /평면도에는 높이가 없습니다/);
  // 간섭이 없는 도면은 간섭 쪽을 말하지 않는다.
  assert.match(reviewBannerText({total:0, assumed_basis:0}, {candidates:4, assumed_basis:{candidates:4}}),
    /^연결 후보 4건 중 가정 4건 —/);
});

test('a batch confirmation carries the ids the list actually offered, and one id stays a single request', () => {
  const connectivity={candidates:[
    {id:'gap:1', routine:true}, {id:'gap:2', routine:false}, {id:'gap:3', routine:true}]};
  assert.deepEqual(routineCandidateIds(connectivity), ['gap:1','gap:3']);
  assert.deepEqual(routineCandidateIds({}), []);
  const runtime={project_id:'p', revision:7};
  assert.deepEqual(bridgeRequest(['gap:1','gap:3'], true, runtime),
    {project_id:'p', expected_revision:7, candidate_ids:['gap:1','gap:3'], confirmed:true});
  // 한 건은 종전 본문 그대로다 — 저장소의 단건 경로를 바꾸지 않는다.
  assert.deepEqual(bridgeRequest(['gap:1'], false, runtime),
    {project_id:'p', expected_revision:7, candidate_id:'gap:1', confirmed:false});
  assert.deepEqual(bridgeRequest(['gap:1','gap:1'], true, runtime).candidate_id, 'gap:1');
  assert.throws(()=>bridgeRequest([], true, runtime), /후보/);
});
