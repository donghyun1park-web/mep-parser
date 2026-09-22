import test from 'node:test';
import assert from 'node:assert/strict';
import {
  buildReviewEntries,
  reviewBannerText,
  bridgeRequest,
  routineCandidateIds,
  reviewRowHtml,
  batchButtonHtml,
  modelBounds,
  deriveSectionRange,
  floorKeyOf,
  isZVisible,
  reconcileSection,
  uniqueByEid,
  fitDistance,
  recordOnEditFloor,
  editBackdropState,
  REASON,
  summarizeBoq,
  boqHeightBasisText,
  boqBodyHtml,
  BOQ_SCOPE_NOTE,
  createHistory,
  joinedEids,
  joinedText,
  systemOf,
  systemLineText,
  isTypingTarget,
  clashMarkerSpecs,
  reviewCounts,
  changeSummaryText,
  levelHeightDeclared,
  shownClashItems,
  legendHtml,
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

test('construction-rule violations lead the queue after clashes, and info items never appear', () => {
  const clashes=[{id:'c',action:'벽 관통',at:[0,0],z:[0,1],struct:{eid:'w:1'},mep:{eid:'d:a'}}];
  const rules=[
    {eid:'d:flat',rule:'duct-aspect-ratio',kind:'violation',standard:'KCS 31 20 20',clause:'3.2.1(2)②',
     values:{ratio:4.4}},
    {eid:'p:drain',rule:'drain-slope-by-diameter',kind:'info',standard:'KDS 31 30 25',clause:'4.1',
     values:{required_fall_mm:100}}];
  const rows=buildReviewEntries({},{},clashes,{},rules);
  assert.deepEqual(rows.map(x=>[x.kind,x.eid,x.category]),
    [['clash','d:a','간섭'],['rule','d:flat','시공기준']]);          // info 는 목록에 없다
  assert.match(rows[1].reason,/KCS 31 20 20 3\.2\.1\(2\)② 위반 · ratio=4\.4/);
  assert.deepEqual(buildReviewEntries({},{},[],{},[]),[]);           // 규칙 0건은 조용하다
});

test('layer-rule suggestions follow rules, carry an apply payload, and never leak onto other rows', () => {
  const clashes=[{id:'c',action:'벽 관통',at:[0,0],z:[0,1],struct:{eid:'w:1'},mep:{eid:'d:a'}}];
  const suggestions=[
    {code:'thin_pair', row_layer:'A-CON', pattern:'^A\\-CON$', op:'set_opts', category:'wall',
     opts:{pair_min:83}, evidence:{median_mm:250, count:2}},
    {code:'width_conflict', row_layer:'A-STEEL', pattern:'^A-STEEL$', op:'set_width', category:'wall',
     width:450, evidence:{declared_mm:200, detected_mm:450, count:3}}];
  const rows=buildReviewEntries({},{},clashes,{},[],suggestions);
  assert.deepEqual(rows.map(x=>[x.kind,x.category,x.label]),
    [['clash','간섭',undefined],['suggestion','레이어 제안','A-CON'],['suggestion','레이어 제안','A-STEEL']]);
  assert.match(rows[1].reason, /얇은 오결합.*250mm.*2개.*pair_min=83/);
  assert.match(rows[2].reason, /두께 불일치.*450mm ≠ 선언 200mm.*3개/);
  assert.deepEqual(rows[1].apply, suggestions[0]);
  assert.equal(rows[0].apply, undefined);                              // 간섭 줄에는 새지 않는다
  assert.deepEqual(buildReviewEntries({},{},[],{},[],[]),[]);          // 제안 0건은 조용하다
});

test('reviewRowHtml renders an apply button only for suggestion rows with a server, and escapes the payload', () => {
  const entry={key:'suggestion:thin_pair:A-CON', kind:'suggestion', eid:null, label:'A-CON',
    category:'레이어 제안', floor:'', reason:'제안 문구',
    apply:{code:'thin_pair', row_layer:'A-CON', opts:{pair_min:83}}};
  const saved=reviewRowHtml(entry, true);
  assert.match(saved, /class="apply-suggest"/);
  assert.deepEqual(JSON.parse(saved.match(/data-apply='([^']*)'/)[1].replace(/&quot;/g,'"').replace(/&#39;/g,"'")), entry.apply);
  const standalone=reviewRowHtml(entry, false);
  assert.ok(!standalone.includes('apply-suggest'));                    // 서버 없이 연 독립 HTML 에는 못 쓴다
  const clashRow=reviewRowHtml({key:'clash:1', kind:'clash', eid:'d:a', category:'간섭', floor:'', reason:'r'}, true);
  assert.ok(!clashRow.includes('apply-suggest'));                      // apply 없는 행에는 버튼이 없다
});

test('every review_reason code the parser/profile/edit_review emit has a Korean REASON entry', () => {
  const codes=['thin_pair','single','single_offset','closed','axis',
    'column_layer_looks_like_wall','column_boundary_unresolved','column_outline_inferred',
    'project_architecture_classification','mep_source_gap','mep_dimensions_assumed',
    'sleeve_symbol','equipment_symbol_envelope','wall_overlap','endpoint_gap','opening_host_missing'];
  for (const code of codes) assert.ok(REASON[code] && REASON[code].length > 0, code);
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

test('a clash row and the banner say when the wall under them is one the parser flagged', () => {
  const clashes=[{id:'a',kind:'under_wall',action:'바닥 매립 설비가 벽 아래를 지남',at:[1000,100],z:[70,86],
    struct:{eid:'w:g',category:'wall',width_mm:50,pairing:'single_offset',review_reason:'single_offset',
            uncertain:true},
    mep:{eid:'p:1',size:'Ø15.9'}}];
  const [row]=buildReviewEntries({}, {}, clashes);
  assert.match(row.reason, /벽 위치·두께 불확실\(single_offset\)/);
  assert.match(reviewBannerText({total:24, assumed_basis:24, on_uncertain_walls:14}, {}),
    /간섭 24건 중 가정 높이 24건 · 불확실한 벽 위 14건/);
  // 검토 표시가 없는 벽은 아무 말도 붙이지 않는다.
  const clean=[{...clashes[0], struct:{eid:'w:ok',category:'wall',width_mm:200}}];
  assert.ok(!buildReviewEntries({}, {}, clean)[0].reason.includes('벽 위치'));
});

test('review rows carry the hooks the click handler needs, and no save buttons without a server', () => {
  const gap={key:'gap:1', kind:'gap', eid:'d:1', category:'연결 후보', floor:'', reason:'직선 이음 후보',
             confirm:{id:'gap:abc', confirmed:false}};
  const saved=reviewRowHtml(gap, true);
  assert.match(saved, /data-gap="gap:abc"/);
  assert.match(saved, /data-confirmed=""/);
  assert.match(saved, /이음 확정</);
  assert.match(reviewRowHtml({...gap, confirm:{id:'gap:abc', confirmed:true}}, true), /data-confirmed="1".*확정 취소</s);
  // 서버 없이 연 독립 HTML 에는 저장할 곳이 없다 — 버튼을 그리지 않는다.
  const standalone=reviewRowHtml(gap, false);
  assert.ok(!standalone.includes('confirm-gap') && standalone.includes('data-eid="d:1"'));
  // 간섭 행은 상대 구조부재를 싣는다 — 클릭 핸들러가 배관과 같이 켠다. 합성 슬래브(부재 아님)는 빈칸.
  const [clash]=buildReviewEntries({}, {}, [{id:'c',action:'벽 관통',at:[0,0],z:[0,1],
    struct:{eid:'w:<1>',category:'wall'},mep:{eid:'p:1'}}]);
  assert.match(reviewRowHtml(clash,false), /data-eid="p:1"[^>]*data-struct="w:&lt;1&gt;"/);
  const [slab]=buildReviewEntries({}, {}, [{id:'s',action:'슬래브 관통',at:[0,0],z:[0,1],
    struct:{eid:null,category:'slab',synthetic:'천장'},mep:{eid:'p:1'}}]);
  assert.match(reviewRowHtml(slab,false), /data-struct=""/);
  assert.equal(batchButtonHtml(['a','b'], false), '');
  assert.match(batchButtonHtml(['a','b'], true), /data-batch="1".*2건/);
  assert.equal(batchButtonHtml([], true), '');
});

test('review text is escaped so a layer name cannot inject markup', () => {
  const row=reviewRowHtml({eid:'<img src=x>', category:'간섭', floor:'', reason:'a & b "c"'}, false);
  assert.ok(!row.includes('<img'));
  assert.match(row, /&lt;img src=x&gt;/);
  assert.match(row, /a &amp; b &quot;c&quot;/);
});

test('the source pane frames the parsed building, not the change-note blocks around it', () => {
  // 실측: 평면은 14.6m × 11.2m 인데 설계변경 표가 69m × 79m 라 층 bbox 에 맞추면 평면이 손톱만 했다.
  const elements={wall:[{eid:'w:1', points:[[0,0],[14600,0]]}, {eid:'w:2', points:[[0,0],[0,11200]]}]};
  const box=modelBounds(elements);
  assert.ok(box.w < 20000 && box.h < 20000, JSON.stringify(box));
  assert.ok(box.x < 0 && box.y < 0);                       // 여백이 붙는다
  assert.equal(modelBounds({}), null);                     // 부재가 없으면 종전대로 층 bbox
  assert.equal(modelBounds({wall:[]}), null);
  // 층 필터: 다른 층 부재는 안 센다.
  const twoFloors={wall:[{eid:'a', level:'1F', points:[[0,0],[1000,0]]},
                         {eid:'b', level:'2F', points:[[90000,90000],[91000,90000]]}]};
  assert.ok(modelBounds(twoFloors,'1F').x < 1000);
  assert.ok(modelBounds(twoFloors,'2F').x > 80000);
  // 원(기둥)도 반지름만큼 담는다.
  assert.ok(modelBounds({column:[{eid:'c', center:[0,0], radius:300}]}).w >= 600);
});

// ── 되돌리기·다시 실행(Planform 대조에서 가져온 편집기 손맛) ─────────────
test('createHistory walks back and forward, and a new action drops the redo branch', () => {
  const h = createHistory('a');
  h.push('b'); h.push('c');
  assert.deepEqual(h.sizes(), {undo: 2, redo: 0});
  assert.equal(h.undo(), 'b');
  assert.equal(h.undo(), 'a');
  assert.equal(h.undo(), null);                       // 더 갈 곳이 없으면 null (호출측은 아무것도 안 한다)
  assert.equal(h.redo(), 'b');
  assert.equal(h.redo(), 'c');
  assert.equal(h.redo(), null);
  // 되돌린 뒤 새 동작 → 되돌릴 미래는 버린다(분기를 남기지 않는다)
  h.undo();
  h.push('d');
  assert.equal(h.sizes().redo, 0);
  assert.equal(h.redo(), null);
  assert.equal(h.current(), 'd');
});

test('createHistory ignores a no-op and caps the stack', () => {
  const h = createHistory('a');
  assert.equal(h.push('a'), false);                   // 같은 값은 단계를 만들지 않는다
  assert.deepEqual(h.sizes(), {undo: 0, redo: 0});
  const capped = createHistory('0', 3);
  for (const s of ['1', '2', '3', '4']) capped.push(s);
  assert.equal(capped.sizes().undo, 3);
  // ★ 저장 응답(rebase)은 기준선만 옮긴다 — 되돌릴 미래를 먹으면 안 된다.
  //   되돌리기가 저장을 부르고 그 저장이 redo 를 지우면 '다시 실행'이 영원히 안 된다(브라우저 QA 실측).
  const served = createHistory('a');
  served.push('b');
  assert.equal(served.undo(), 'a');
  served.rebase('a');                                 // 저장 응답 — 로컬 상태는 그대로다
  assert.equal(served.sizes().redo, 1, 'a save acknowledgement must not eat the redo branch');
  assert.equal(served.redo(), 'b');
});

// ── Phase 4: 물량 요약 패널 ────────────────────────────────────────────
const SAMPLE_BOQ = {
  '벽': [['두께','개수','길이(m)','높이(m)','면적(㎡)','체적(㎥)'],
         [['T200', 5, 45.2, 2.8, 126.6, 25.3], ['T300', 2, 10.1, '', 28.3, 8.5]],
         ['합계', 7, 55.3, '', 154.9, 33.8]],
  'MEP': [['구분','규격(mm)','개수','길이(m)','길이기준'],
          [['배관', '100', 3, 12.5, '원본(2D)'], ['덕트', '400x300', 2, 8.0, '모델(수정·경사 포함)']],
          ['합계', '', 5, 20.5, '']],
  '창호': [['구분','규격(mm)','개수'], [['창', '1200x1200', 4], ['문', '900x2100', 2]], ['합계', '', 6]],
};

test('summarizeBoq extracts wall/MEP/opening rows by the exact aggregate() column order', () => {
  const s = summarizeBoq(SAMPLE_BOQ);
  assert.equal(s.error, null);
  assert.deepEqual(s.wall, [{key:'T200', lengthM:45.2, heightM:2.8, volumeM3:25.3},
                             {key:'T300', lengthM:10.1, heightM:'', volumeM3:8.5}]);
  assert.deepEqual(s.mep, [{label:'배관', size:'100', lengthM:12.5, basis:'원본(2D)'},
                            {label:'덕트', size:'400x300', lengthM:8.0, basis:'모델(수정·경사 포함)'}]);
  assert.deepEqual(s.openings, [{kind:'창', size:'1200x1200', count:4}, {kind:'문', size:'900x2100', count:2}]);
});

test('summarizeBoq reuses aggregate() totals instead of re-adding them on screen', () => {
  const withSlab = Object.assign({}, SAMPLE_BOQ, {
    '슬래브': [['번호','레이어','면적(㎡)','두께(mm)','체적(㎥)'],
               [['Slab_0','A-SLAB',120.5,200,24.1]], ['합계','',120.5,'',24.1]]});
  const t = summarizeBoq(withSlab).totals;
  assert.equal(t.wallCount, 7);          // 합계행 그대로 — 화면이 다시 더하지 않는다
  assert.equal(t.wallLengthM, 55.3);
  assert.equal(t.slabAreaM2, 120.5);
  assert.equal(t.windows, 4);
  assert.equal(t.doors, 2);
  const html = boqBodyHtml(withSlab);
  assert.match(html, /슬래브 120\.5㎡/);
  assert.match(html, /벽 55\.3m · 7개/);
  assert.match(html, /문\/창 2\/4/);
  assert.match(html, /개구부 미차감/);     // 물량표가 무엇을 안 세는지 화면에도 적는다
  assert.ok(BOQ_SCOPE_NOTE.includes('검토용'));
  // 벽이 없는 설비 도면 — 0 을 실측처럼 적지 않는다(합계 줄에서 아예 뺀다)
  const mepOnly = boqBodyHtml({'벽': [['두께','개수','길이(m)','높이(m)','면적(㎡)','체적(㎥)'], [], ['합계',0,0,'',0,0]],
                               'MEP': SAMPLE_BOQ['MEP']});
  assert.doesNotMatch(mepOnly, /벽 0m|0개/);
});

test('summarizeBoq tolerates missing sections and a project_server error fallback', () => {
  assert.deepEqual(summarizeBoq({}), {error:null, wall:[], mep:[], openings:[],
    totals:{wallCount:null, wallLengthM:null, slabAreaM2:null, doors:0, windows:0}});
  assert.equal(summarizeBoq(null).totals, null);     // boq 자체가 없으면 합계도 없다(0 으로 꾸미지 않는다)
  const failed = summarizeBoq({error:'ValueError: boom'});
  assert.equal(failed.error, 'ValueError: boom');
  assert.deepEqual(failed.wall, []);
  assert.equal(failed.totals, null);
  assert.equal(boqBodyHtml({}), '없음');              // 빈 물량표에는 각주도 안 붙인다
});

test('boqHeightBasisText names the source, and the panel actually carries the column it points to', () => {
  // 어긋난 적 있음(리뷰 발견): "행마다 높이 열 참고"라 해 놓고 패널에 높이가 안 보였다.
  // 문구가 가리키는 h= 값이 boqBodyHtml 출력에 실제로 있는지 여기서 같이 잠근다.
  assert.match(boqHeightBasisText(true), /선언/);
  assert.match(boqHeightBasisText(false), /기본값/);
  assert.match(boqBodyHtml(SAMPLE_BOQ), /h2\.8m/);
});

test('boqBodyHtml renders each section (including the honest per-row height), escapes content, and says so when aggregate() failed', () => {
  const html = boqBodyHtml(SAMPLE_BOQ);
  assert.match(html, /T200/);
  assert.match(html, /h2\.8m · 45\.2m · 25\.3㎥/);     // 단일 높이 — 그대로 보여준다
  assert.match(html, /T300/);
  assert.match(html, /h\?m · 10\.1m · 8\.5㎥/);          // 섞인 높이 — 숫자 하나를 대표로 꾸며내지 않는다
  assert.match(html, /배관 100/);
  assert.match(html, /모델\(수정·경사 포함\)/);
  assert.match(html, /창 1200x1200/);
  assert.equal(boqBodyHtml({}), '없음');
  const failed = boqBodyHtml({error:'<script>x</script>'});
  assert.doesNotMatch(failed, /<script>x<\/script>/);
  assert.match(failed, /물량 계산 실패/);
});

// ── 장면이 문제를 가리킨다(2026-09-22 HighTopo 대조) ─────────────────────────────

test('joinedEids follows shared joint ids and never crosses a candidate-only gap', () => {
  const elements={
    pipe:[{eid:'p:a',joints:[{id:'j1',port:'end'}]},
          {eid:'p:b',joints:[{id:'j1',port:'start'},{id:'j:bridged',port:'end',basis:'bridged'}]},
          {eid:'p:c',joints:[{id:'j:bridged',port:'start',basis:'bridged'}]},
          {eid:'p:lonely'}],                      // 후보 틈 건너편 — joints 가 없으면 무리가 아니다
    wall:[{eid:'w:1',joints:[{id:'j1'}]}]};           // 경로가 아닌 카테고리는 보지 않는다
  assert.deepEqual(joinedEids(elements,'p:c'), ['p:a','p:b','p:c']);   // 확정한 이음(bridged)도 joints 다
  assert.deepEqual(joinedEids(elements,'p:lonely'), ['p:lonely']);
  assert.deepEqual(joinedEids(elements,'w:1'), []);
  assert.deepEqual(joinedEids(elements,'missing'), []);
  assert.match(joinedText(4), /4개/);
  assert.match(joinedText(1), /없음.*연결 후보는 검토 목록에서 확정/);
});

test('systemLineText prefers the override the editor saved and says so when nothing was declared', () => {
  assert.equal(systemOf({system:'SA',overrides:{system:'RA'}}), 'RA');
  assert.equal(systemOf({system:'  '}), null);
  assert.equal(systemLineText({system:'SA',service:'supply_air'}), 'SA / supply_air');
  assert.equal(systemLineText({service:'drain'}), '— / drain');
  // 이름에서 아무것도 추론하지 않는다 — 없으면 없다고 말한다.
  assert.match(systemLineText({}), /^계통 없음 — 설비 설정에서 계통을 선언하면/);
});

test('shortcut keys are ignored while typing in a text field but not on buttons, checkboxes or sliders', () => {
  assert.equal(isTypingTarget({tagName:'INPUT',type:'number'}), true);
  assert.equal(isTypingTarget({tagName:'input'}), true);             // type 생략 = text
  assert.equal(isTypingTarget({tagName:'TEXTAREA'}), true);
  assert.equal(isTypingTarget({tagName:'DIV',isContentEditable:true}), true);
  assert.equal(isTypingTarget({tagName:'INPUT',type:'checkbox'}), false);
  assert.equal(isTypingTarget({tagName:'INPUT',type:'range'}), false);
  assert.equal(isTypingTarget({tagName:'BUTTON'}), false);
  assert.equal(isTypingTarget({tagName:'SELECT'}), false);
  assert.equal(isTypingTarget(null), false);
});

test('clash markers keep the point and the assumed basis, and survive a synthetic slab without a struct eid', () => {
  const specs=clashMarkerSpecs([
    {id:'clash:1',at:[1000,2000],z:[2500,2700],basis:'assumed',level:'B1',struct:{eid:'w:1'},mep:{eid:'p:1'}},
    {id:'clash:2',at:[5,6],z:[0,200],basis:'declared',struct:{eid:null,synthetic:'천장'},mep:{eid:'d:1'}},
    {id:'bad',at:['x',1]}, {id:'none'}]);
  assert.deepEqual(specs, [
    {key:'clash:1',x:1000,y:2000,z:2600,assumed:true,eid:'p:1',struct:'w:1',level:'B1'},
    {key:'clash:2',x:5,y:6,z:100,assumed:false,eid:'d:1',struct:null,level:null}]);
  assert.deepEqual(clashMarkerSpecs(undefined), []);
});

test('change summary names what moved, says when nothing moved, and never turns a failed clash run into a drop', () => {
  const before=reviewCounts({summary:{total:7}}, {summary:{candidates:12}});
  assert.deepEqual(before, {clash:7,clashError:false,gaps:12,gapsError:false});
  assert.equal(changeSummaryText(before, reviewCounts({summary:{total:5}},{summary:{candidates:12}})),
    '간섭 7 → 5 (−2) · 연결 후보 12 그대로');
  assert.equal(changeSummaryText(before, reviewCounts({summary:{total:8}},{summary:{candidates:10}})),
    '간섭 7 → 8 (+1) · 연결 후보 12 → 10 (−2)');
  assert.equal(changeSummaryText(before, before), '간섭 7 · 연결 후보 12 — 변화 없음');
  // project_server 는 간섭 계산이 죽으면 total:0 과 error 를 싣는다 — 7 → 0 으로 읽으면 안 된다.
  const failed=reviewCounts({summary:{total:0,error:'ValueError: x'}}, {summary:{candidates:12}});
  assert.equal(failed.clash, null);
  assert.equal(changeSummaryText(before, failed), '연결 후보 12 — 변화 없음 · 간섭 계산 실패 — 비교 불가');
  // 설비가 없는 건축 도면: 둘 다 0 이면 아무 말도 하지 않는다(저장마다 '0 그대로' 는 소음이다).
  const none=reviewCounts({summary:{total:0}}, {summary:{candidates:0}});
  assert.equal(changeSummaryText(none, none), '');
  assert.equal(changeSummaryText(null, before), '');
});

test('the storey-height basis survives a save response, which carries the parser key and not the preview key', () => {
  assert.equal(levelHeightDeclared({level_height_declared:true}), true);     // 첫 화면(preview.py 페이로드)
  assert.equal(levelHeightDeclared({level_height_overrode:0}), true);        // 저장 응답 — 0 건 덮어도 선언이다
  assert.equal(levelHeightDeclared({level_height_declared:false}), false);
  assert.equal(levelHeightDeclared({}), false);
  assert.equal(levelHeightDeclared(null), false);
});

// ── 두 번째 묶음(2026-09-22): 끊긴 끝 행 · 범례 숨김 · 3D 간섭 고리 ─────────────────────

test('open ends become review rows so a broken end is clickable, and only the unexplained ones', () => {
  const connectivity={
    candidates:[{id:'gap:1',kind:'straight',gap_mm:40,points:[[0,0],[40,0]],eids:['p:1','p:2'],systems:['CW','CW'],sizes:['Ø50','Ø50']}],
    open_ends:[
      {eid:'p:9',port:'end',at:[1200.4,300],z:2600,z_basis:'assumed',status:'open',system:'CW',level:'B1'},
      {eid:'p:1',port:'start',at:[0,0],z:2600,status:'candidate'},   // 후보 행이 이미 있다
      {eid:'p:3',port:'end',at:[5,5],z:2600,status:'terminal'},      // 장비에서 끝났다 — 문제가 아니다
      {eid:'p:4',port:'end',at:[6,6],z:2600,status:'sleeve'}]};
  const elements={pipe:[{eid:'p:7',needs_review:true,review_reason:'mep_source_gap'}]};
  const rows=buildReviewEntries(elements,{},[],connectivity);
  assert.deepEqual(rows.map(r=>[r.kind,r.eid]), [['gap','p:1'],['end','p:9'],['element','p:7']]);
  const end=rows[1];
  assert.equal(end.category,'끊긴 끝');
  assert.equal(end.floor,'B1');
  assert.deepEqual(end.at,[1200.4,300,2600]);
  assert.match(end.reason, /끝 쪽이 열림 · \(1200, 300\) z 2600 · CW/);
  assert.match(end.reason, /높이 근거: 가정/);
  // 행이 점을 싣는다 — 클릭 핸들러가 부재 전체가 아니라 그 점으로 카메라를 댄다.
  assert.match(reviewRowHtml(end,false), /data-eid="p:9"[^>]*data-at="1200.4,300,2600"/);
  assert.match(reviewRowHtml(rows[2],false), /data-at=""/);
});

test('the 3D clash rings follow the review filters: only clashes the list is showing', () => {
  const items=[{id:'a',at:[0,0],z:[0,1]},{id:'b',at:[1,1],z:[0,1]}];
  const shown=[{kind:'clash',key:'clash:b'},{kind:'element',key:'element:w:1'},{kind:'gap',key:'clash:a'}];
  assert.deepEqual(shownClashItems(items,shown).map(c=>c.id), ['b']);
  assert.deepEqual(shownClashItems(items,[]), []);
  assert.deepEqual(shownClashItems(undefined,undefined), []);
});

test('the legend is a hide switch per category that says what is hidden and escapes names', () => {
  const colors={wall:0x6b8fb5, pipe:0x4cc9f0};
  const shown=legendHtml(colors,new Set(),false,'<i>x</i>');
  assert.match(shown, /data-cat="wall" role="button" aria-pressed="false"/);
  assert.ok(!shown.includes('숨김 ') && !shown.includes('<details open'));
  assert.ok(shown.includes('<i>x</i></details>'));
  const hidden=legendHtml(colors,new Set(['wall','nope']),true);
  assert.match(hidden, /<details open><summary>색상 범례 · 숨김 1<\/summary>/);   // 모르는 이름은 세지 않는다
  assert.match(hidden, /class="legend-cat off" data-cat="wall"[^>]*aria-pressed="true"><span class="sw" style="background:#6b8fb5"><\/span>wall \(숨김\)/);
  assert.ok(legendHtml({'<b>':1},new Set()).includes('&lt;b&gt;'));
});
