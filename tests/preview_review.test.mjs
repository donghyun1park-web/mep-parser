import test from 'node:test';
import assert from 'node:assert/strict';
import {
  buildReviewEntries,
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
