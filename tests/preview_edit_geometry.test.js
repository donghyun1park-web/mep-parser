'use strict';

const assert = require('node:assert/strict');
const E = require('../vendor/edit_geometry.js');

function rec(eid, points, extra={}) {
  return Object.assign({
    eid, kind:'polyline', centerline:points, points,
    layer:'A-WALL', level:'L1', z_base:0,
    overrides:{width:200, height:2800, material:'Concrete'}, attrs:{fire:'2h'}
  }, extra);
}

async function main() {
  // Shift 직교 — 이동량이 큰 축만 남긴다. 기준점이 없으면(첫 클릭) 손대지 않는다.
  assert.deepEqual(E.orthoPoint([0,0],[1000,40]), [1000,0], 'nearly horizontal must snap to the axis');
  assert.deepEqual(E.orthoPoint([0,0],[40,1000]), [0,1000], 'nearly vertical must keep x');
  assert.deepEqual(E.orthoPoint([100,200],[300,400]), [300,200], 'a tie takes the horizontal axis');
  assert.deepEqual(E.orthoPoint(null,[7,9]), [7,9], 'no anchor yet means no constraint');
  const anchor=[10,20], orthoed=E.orthoPoint(anchor,[99,21]);
  assert.deepEqual(anchor, [10,20], 'orthoPoint must not mutate its anchor');
  assert.deepEqual(orthoed, [99,20]);

  const base = {wall:[rec('w:1', [[0,0],[1000,0],[1000,800]])], opening:[]};
  const edits = {
    'w:1':{deleted:true, _source:{centerline:[[0,0],[1000,0],[1000,800]]}},
    'wm:a':{added:true, category:'wall', record:rec('wm:a', [[5,5],[15,5]], {source:'manual_preview'})}
  };
  const effective = E.materializeElements(base, edits);
  assert.deepEqual(effective.wall.map(x=>x.eid), ['wm:a']);
  effective.wall[0].centerline[0][0] = 999;
  assert.equal(edits['wm:a'].record.centerline[0][0], 5, 'effective geometry must not mutate edit snapshots');
  assert.equal(base.wall[0].centerline[0][0], 0, 'effective geometry must not mutate canonical base');

  const reopenedCanonical={wall:[],column:[rec('w:moved',[[0,0],[1000,0]],{overrides:{width:350,height:2800}})],
    opening:[],equipment:[{eid:'L2:wm:saved',level:'L2',centerline:[[0,0],[50,0]],points:[[0,0],[50,0]]}]};
  const reopenedEdits={
    'w:moved':{category:'column',overrides:{width:350},_source:{eid:'w:moved',category:'wall',kind:'polyline',
      centerline:[[0,0],[1000,0]],points:[[0,0],[1000,0]],layer:'A-WALL',z_base:0,review_reason:'single'}},
    'L2:wm:saved':{added:true,category:'equipment',record:{eid:'L2:wm:saved'}}
  };
  const cleanBase=E.deriveBaseElements(reopenedCanonical,reopenedEdits);
  assert.deepEqual(cleanBase.column,[]);
  assert.deepEqual(cleanBase.equipment,[],'persisted additions must not leak into the unedited base');
  assert.equal(cleanBase.wall[0].eid,'w:moved');
  assert.equal(cleanBase.wall[0].overrides,undefined,'applied dimensions must be removed when source had none');
  assert.equal(cleanBase.wall[0].needs_review,true);
  assert.deepEqual(E.materializeElements(cleanBase,{}).wall.map(r=>r.eid),['w:moved'],
                   'an offline empty snapshot must visibly undo all canonical edits');

  const withElevation = {wall:[], pipe:[rec('p:1',[[0,0],[1000,0]],{overrides:{width:200,height:2800,elevation:2600}})]};
  const clearedElevation = E.materializeElements(withElevation, {'p:1':{overrides:{elevation:null}}});
  assert.equal(clearedElevation.pipe[0].overrides.elevation, undefined,
               'null override merges as a delete, not a stored null (edit_geometry.js materializeElements)');
  assert.equal(clearedElevation.pipe[0].overrides.width, 200, 'unrelated overrides survive a null-key merge');

  const changedSource={wall:[rec('w:same',[[20,0],[1020,0]],{overrides:{width:350}})]};
  const changedBase=E.deriveBaseElements(changedSource,{
    'w:same':{overrides:{width:350},_source:{eid:'w:same',category:'wall',centerline:[[0,0],[1000,0]],points:[[0,0],[1000,0]]}},
    'w:orphan':{overrides:{width:999},_source:{eid:'w:orphan',category:'wall',centerline:[[5,5],[10,5]],points:[[5,5],[10,5]]}}
  });
  assert.deepEqual(changedBase.wall[0].centerline,[[20,0],[1020,0]],'historical snapshots must not replace fresh source coordinates');
  assert.equal(changedBase.wall.some(r=>r.eid==='w:orphan'),false,'orphan snapshots are overlay evidence, not modeled geometry');
  const deletedOrphan=E.deriveBaseElements({wall:[]},{
    'w:deleted-orphan':{deleted:true,_source:{eid:'w:deleted-orphan',category:'wall',centerline:[[1,1],[2,2]],points:[[1,1],[2,2]]}}
  },['w:deleted-orphan']);
  assert.equal(deletedOrphan.wall.some(r=>r.eid==='w:deleted-orphan'),false,'a deleted orphan must remain overlay-only during undo-all');

  const bent = rec('w:bent', [[0,0],[1000,0],[1000,1000],[2000,1000]]);
  const moved = E.movePolylinePoint(bent, 1, [900,100]);
  assert.deepEqual(moved.centerline, [[0,0],[900,100],[1000,1000],[2000,1000]]);
  assert.deepEqual(moved.points, moved.centerline);
  assert.deepEqual(bent.centerline, [[0,0],[1000,0],[1000,1000],[2000,1000]]);

  const split = E.splitPolyline(bent, [1000,400]);
  assert.deepEqual(split.map(x=>x.centerline), [
    [[0,0],[1000,0],[1000,400]],
    [[1000,400],[1000,1000],[2000,1000]]
  ], 'split must target the nearest actual segment and retain intermediate vertices');

  const left = rec('w:l', [[0,0],[1000,0]]);
  const right = rec('w:r', [[1000,0],[2000,0]]);
  assert.deepEqual(E.joinPolylines(left, right, {joinTolerance:600, coordinateTolerance:1}).centerline,
                   [[0,0],[1000,0],[2000,0]]);
  assert.equal(E.joinPolylines(left, rec('w:other-floor', [[1000,0],[2000,0]], {level:'L2'}), {joinTolerance:600}), null);
  assert.equal(E.joinPolylines(left, rec('w:bent2', [[1000,0],[1500,0],[1500,500]]), {joinTolerance:600}), null,
               'joining must never flatten a bent chain');
  assert.equal(E.joinPolylines(left, rec('w:wide', [[1000,0],[2000,0]], {overrides:{width:250,height:2800,material:'Concrete'}}), {joinTolerance:600}), null);
  assert.equal(E.joinPolylines(left, rec('w:gap', [[1200,0],[2000,0]]), {joinTolerance:600, coordinateTolerance:1}), null,
               'a gap must remain visible instead of becoming wall');

  // 평면 탭 설비 편집 — makeManualRecord 는 카테고리별 치수 키를 쓰고, 도면 값이 아니라
  // wm: 접두를 유지한다(category 는 edits.json 의 필드가 결정한다 — Pascal 과 같은 규약).
  const pipeSrc = {level:'L2', elevation:2600, system:'heating', section_shape:'round', layer:'H-PIPE'};
  const madePipe = E.makeManualRecord('pipe', [[0,0],[1000,0]], pipeSrc, {diameter:15.9},
                                       ()=>'11111111-2222-4333-8444-555555555555');
  assert.equal(madePipe.eid, 'L2:wm:11111111-2222-4333-8444-555555555555');
  assert.equal(madePipe.pairing, 'manual');
  assert.equal(madePipe.elevation, 2600); assert.equal(madePipe.system, 'heating');
  assert.equal(madePipe.section_shape, 'round');
  assert.equal(madePipe.overrides.diameter, 15.9);
  assert.equal(madePipe.z_base, undefined, '배관은 z_base 를 지어내지 않는다(elevation 이 기준)');

  const madeDuct = E.makeManualRecord('duct', [[0,0],[1000,0]], {}, {width_mm:200, height_mm:100});
  assert.deepEqual([madeDuct.overrides.width_mm, madeDuct.overrides.height_mm], [200, 100]);
  assert.equal(madeDuct.overrides.width, undefined, '설비는 width/height 키를 쓰지 않는다');

  // 이음 자리에서 끊어 그린 두 조각 — joints/source_refs/source_length_mm 만 달라도 결합된다.
  const pipeA = rec('p:a', [[0,0],[1000,0]], {overrides:{diameter:100}, elevation:2600,
    joints:[{id:'j1'}], source_refs:[{handle:'A'}], source_length_mm:1000, route_length_mm:1000, dimension_status:'specified'});
  const pipeB = rec('p:b', [[1000,0],[2000,0]], {overrides:{diameter:100}, elevation:2600,
    joints:[{id:'j2'}], source_refs:[{handle:'B'}], source_length_mm:1000, route_length_mm:1000, dimension_status:'assumed'});
  assert.deepEqual(E.joinPolylines(pipeA, pipeB, {joinTolerance:600}).centerline, [[0,0],[1000,0],[2000,0]]);
  const pipeElsewhere = rec('p:c', [[1000,0],[2000,0]], {overrides:{diameter:100}, elevation:3200});
  assert.equal(E.joinPolylines(pipeA, pipeElsewhere, {joinTolerance:600}), null,
               '높이(elevation)가 다르면 여전히 막는다 — OMIT 확장은 이음 부기용 필드에만 한정된다');

  const snapped = E.snapPoint([23,10], [
    rec('w:s', [[0,0],[100,0]], {level:'L1'}),
    rec('w:t', [[20,10],[20,30]], {level:'L2'})
  ], {level:'L1', screenToleranceMm:500, physicalToleranceMm:50});
  assert.deepEqual(snapped.point, [23,0], 'segment projection should beat a farther endpoint');
  assert.equal(snapped.kind, 'segment');
  assert.equal(snapped.distance, 10);
  assert.deepEqual(E.snapPoint([80,40], [rec('w:s', [[0,0],[100,0]])],
                               {screenToleranceMm:500, physicalToleranceMm:30}).point, [80,40]);

  const made = E.makeManualWall([[0,0],[500,0]], rec('w:src', [[0,0],[500,0]], {
    level:'L3', elevation:7200, attrs:{zone:'A'}, overrides:{width:250,height:3100}
  }), {width:200,height:2800}, ()=>'11111111-2222-4333-8444-555555555555');
  assert.equal(made.eid, 'L3:wm:11111111-2222-4333-8444-555555555555');
  assert.equal(made.level, 'L3'); assert.equal(made.elevation, 7200);
  assert.deepEqual(made.attrs, {zone:'A'});
  assert.equal(E.makeId('om',()=>'aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee','L2'),
               'L2:om:aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee');
  assert.equal(E.makeId('om',()=>'aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee'),
               'om:aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee');

  const added = {added:true, category:'wall', record:made, _source:{historical:true}, _at:[0,0]};
  const changed = E.applyProperties(added, made, 'wall', {category:'wall', width:275, height:3200, deleted:false, reviewResolved:false});
  assert.equal(changed.added, true);
  assert.equal(changed.record.eid, made.eid);
  assert.equal(changed.record.overrides.width, 275);
  assert.deepEqual(changed._source, {historical:true});
  assert.equal(changed.review_resolved, undefined, 'property edits must not acknowledge review');

  const recategorized=E.applyProperties({category:'column',_source:{category:'wall'}},made,'column',
                                        {category:'column',width:300,reviewResolved:false});
  assert.equal(recategorized.category,'column','editing a recategorized element must preserve its absolute category');

  // 높이(elevation) 편집 — zKey 는 호출자(app.js)가 정한다. 여기서는 카테고리를 다시 보지 않는다.
  const pipeRec = rec('p:1', [[0,0],[1000,0]], {overrides:{diameter:100}});
  const zSet = E.applyProperties(undefined, pipeRec, 'pipe', {category:'pipe', zKey:'elevation', z:2600});
  assert.equal(zSet.overrides.elevation, 2600);
  assert.equal(zSet.overrides.diameter, undefined, '기존 overrides 에 없던 diameter 를 applyProperties 가 지어내면 안 된다');
  const zCleared = E.applyProperties(zSet, pipeRec, 'pipe', {category:'pipe', zKey:'elevation', z:null});
  assert.equal(zCleared.overrides, undefined, 'null 은 지운다 — 남은 override 가 없으면 overrides 자체를 지운다');
  const zUntouched = E.applyProperties(zSet, pipeRec, 'pipe', {category:'pipe', zKey:'elevation', z:NaN});
  assert.equal(zUntouched.overrides.elevation, 2600, '숫자가 아니면(빈 입력 아님) 손대지 않는다');
  const addedPipe = {added:true, category:'pipe', record:pipeRec};
  const zAdded = E.applyProperties(addedPipe, pipeRec, 'pipe', {category:'pipe', zKey:'elevation', z:2400});
  assert.equal(zAdded.record.overrides.elevation, 2400, 'added 레코드도 같은 규칙');

  const acked = E.applyProperties({_review_signature:'old'}, Object.assign({}, made, {needs_review:true}), 'wall',
                                  {category:'wall', reviewResolved:true});
  assert.equal(acked.acknowledge, true);
  assert.equal(acked._review_signature, undefined, 'explicit acknowledgement must request a fresh server fingerprint');

  assert.equal(E.backupKey({project_id:'p/1', revision:7}), 'mepEdits:p%2F1:7');
  assert.deepEqual(E.restoreBackup(JSON.stringify({project_id:'p/1',base_revision:7,edits:{}}),
                                   {project_id:'p/1',revision:7}),{},'an empty snapshot is still a pending backup');
  assert.equal(E.restoreBackup(JSON.stringify({project_id:'p/1',base_revision:6,edits:{stale:{}}}),
                               {project_id:'p/1',revision:7}),null,'a backup from another base revision must not load');

  const presentationBase={wall:[rec('w:p',[[0,0],[1000,0]])]};
  E.mergePresentation(presentationBase,{wall:[rec('w:p',[[9,9],[20,20]],{
    needs_review:true,review_ack_stale:true,edit_diagnostics:[{code:'endpoint_gap',distance_mm:12}]
  })]});
  assert.deepEqual(presentationBase.wall[0].centerline,[[0,0],[1000,0]],'server display merge must not alter local geometry');
  assert.equal(presentationBase.wall[0].review_ack_stale,true);
  assert.deepEqual(presentationBase.wall[0].edit_diagnostics,[{code:'endpoint_gap',distance_mm:12}]);

  const calls=[]; let releaseFirst;
  const firstGate = new Promise(resolve=>{ releaseFirst=resolve; });
  const queue = E.createSaveQueue({
    revision:3,
    send: async (snapshot, revision) => {
      calls.push({snapshot, revision});
      if(calls.length===1) await firstGate;
      return {revision:revision+1, edits:snapshot, geometry:{elements:{wall:[]}}};
    }
  });
  const p1=queue.enqueue({a:{deleted:true}});
  queue.enqueue({a:{deleted:true},b:{deleted:true}});
  queue.enqueue({c:{deleted:true}});
  assert.equal(calls.length, 1, 'only one request may be in flight');
  releaseFirst(); await p1; await queue.idle();
  assert.deepEqual(calls.map(x=>x.revision), [3,4]);
  assert.deepEqual(calls[1].snapshot, {c:{deleted:true}}, 'latest snapshot must be coalesced, never dropped');
  assert.deepEqual(queue.acknowledged(), {c:{deleted:true}});

  let conflict;
  const q2=E.createSaveQueue({revision:9, send:async()=>{ const err=new Error('conflict'); err.status=409; err.state={revision:12,edits:{fresh:{deleted:true}}}; throw err; }, onConflict:x=>{conflict=x;}});
  q2.enqueue({stale:{deleted:true}}); await q2.idle();
  assert.equal(q2.revision(), 12);
  assert.deepEqual(conflict.remote.edits, {fresh:{deleted:true}});
  assert.deepEqual(q2.acknowledged(), {}, 'conflict must not make stale edits look saved');
}

main().then(()=>console.log('preview edit geometry tests passed')).catch(err=>{console.error(err);process.exit(1);});
