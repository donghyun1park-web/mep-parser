// 검토 사유 코드 → 한국어 문장. 사용자가 고칠 대상을 보는 유일한 화면(인스펙터 `whyHtml`)에
// 원문 코드가 그대로 찍히면 안 된다. 파서·프로필·edit_review 가 내는 코드를 전부 담는다.
export const REASON = {
  'thin_pair': '두께가 이 레이어 중앙값의 1/3 미만 — 벽면 옆 마감선과 짝지었을 수 있다. '
             + '실제 두께가 맞으면 layer_map 에 opts pair_min 을 주면 통과한다.',
  'single': '반대편 면선을 못 찾아 이 선 하나를 중심선으로 썼다 — 두께는 기본값이다.',
  'single_offset': '반대편 면선이 없어 중심선을 폭의 절반만큼 밀어 추정했다 — '
                 + '위치·두께 둘 다 추정치다.',
  'closed': '닫힌 폴리선을 그대로 압출했다(면선 페어링을 거치지 않음).',
  'axis': '치수선(DIMENSION)에서 뽑은 축선이다 — 단면은 부재일람표에서 온다.',
  // 기둥 세 사유는 종전에 원문 키가 그대로 찍혔다 — 사용자가 고칠 대상을 보는 유일한 화면인데.
  'column_layer_looks_like_wall': '이 레이어의 선은 기둥이 아니라 **벽처럼** 그려졌다(평행 짝). '
             + 'layer_map 한 줄이면 해결된다 — 경고 맨 위에 붙여 넣을 줄이 있다.',
  'column_boundary_unresolved': '기둥 경계를 닫지 못했다 — 원본 선이 실제로 면을 이루지 않는다. '
             + '레이어 분류가 맞는지 먼저 보고, 맞다면 도면의 경계를 확인한다.',
  'column_outline_inferred': '원본 선이 닫은 면으로 기둥을 복원했다 — 분류가 맞는지 확인이 필요하다.',
  'project_architecture_classification': '레이어명이 아니라 이 프로젝트가 선언한 건축 분류'
             + '(architecture_layers) 규칙으로 분류됐다 — 확인이 필요하다.',
  'mep_source_gap': '이어 그린 조각을 하나로 합쳤는데, 원본 도면에 그 이음 자리 틈이 있었다 — 확인이 필요하다.',
  'mep_dimensions_assumed': '치수가 선언되지 않아 프로필 기본값을 썼다 — 실제 규격을 선언하면 사라진다.',
  'sleeve_symbol': '슬리브 기호에서 만든 장비 몸체다 — 실제 규격(관통 구멍 크기)을 확인해야 한다.',
  'equipment_symbol_envelope': '장비 기호의 외곽선을 그대로 몸체로 썼다 — 실제 장비 치수를 확인해야 한다.',
  'wall_overlap': '편집한 벽이 다른 벽과 겹친다 — 중복인지 확인한 뒤 하나를 지우거나 끝점을 옮긴다.',
  'endpoint_gap': '편집한 끝점이 이웃과 완전히 안 붙었다 — 평면 탭에서 스냅해 확인한다.',
  'opening_host_missing': '이 개구부를 담을 벽을 찾지 못했다 — 위치를 확인해 가까운 벽에 다시 놓는다.',
};

export function floorKeyOf(record={}) {
  if (record.level != null) return String(record.level);
  if (record.floor != null) return String(record.floor);
  return '';
}

export function recordOnEditFloor(record, floorId, floorCount=1) {
  const key=floorKeyOf(record);
  return key ? key===String(floorId) : floorCount<=1;
}

export function editBackdropState(drawing, floorId) {
  const floor=(drawing?.floors||[]).find(f=>String(f.id)===String(floorId))||null;
  const available=!!floor?.primitives?.length;
  const notices=[];
  if(!available) notices.push('이 층의 원본 DXF 선이 없습니다. GUI에서 원본 도면의 미리보기를 다시 열어주세요.');
  if(floor?.status==='partial') notices.push('원본 일부 표시');
  notices.push(...(floor?.warnings||[]));
  for(const [kind,count] of Object.entries(floor?.omitted||{})) if(count)
    notices.push(kind==='TRUNCATED'?'표시 제한으로 나머지 생략 (개수 미집계)':`${kind} ${count}개 생략`);
  return {floor,available,notices};
}

function diagnosticText(value) {
  if (typeof value === 'string') return value;
  if (!value) return '';
  return value.message || value.reason || value.code || JSON.stringify(value);
}

const GAP_KIND={straight:'직선',elbow:'엘보',tee:'티'};
// 간섭은 그 벽 위에서 센 것이다 — 파서가 검토하라고 표시한 벽이면 그 사실을 줄에 싣는다.
function wallEvidenceText(struct={}) {
  if (!struct.uncertain) return '';
  return ` · 벽 위치·두께 불확실(${struct.review_reason||struct.pairing})`;
}
// 평면도에는 높이가 없다 — 가정 높이로 나온 판정은 그렇다고 말한다(도면이 말해 준 줄과 같아 보이면 안 된다).
function assumedHeightText(row) {
  if ((row.basis||row.z_basis)!=='assumed') return '';
  const keys=row.assumed||[];
  return ` · 높이 근거: 가정${keys.length?`(${keys.join(', ')})`:''}`;
}

// 줄마다 붙는 '높이 근거: 가정' 만으로는 목록 **전체**가 가정 위에 서 있다는 사실이 안 보인다 —
// 그 한 줄을 목록 맨 위에 둔다. 가정이 하나도 없으면 아무 말도 하지 않는다(빈 문자열).
export function reviewBannerText(clashSummary={}, connectivitySummary={}) {
  const clashTotal=clashSummary.total||0, clashAssumed=clashSummary.assumed_basis||0;
  const gapTotal=connectivitySummary.candidates||0;
  const gapAssumed=(connectivitySummary.assumed_basis||{}).candidates||0;
  if (!clashAssumed && !gapAssumed) return '';
  const parts=[];
  if (clashTotal) parts.push(`간섭 ${clashTotal}건 중 가정 높이 ${clashAssumed}건`
    +(clashSummary.on_uncertain_walls?` · 불확실한 벽 위 ${clashSummary.on_uncertain_walls}건`:''));
  if (gapTotal) parts.push(`연결 후보 ${gapTotal}건 중 가정 ${gapAssumed}건`);
  return parts.join(' · ')+' — 평면도에는 높이가 없습니다. 설비 설정에서 계통별 설치 높이·규격을 선언하면 줄어듭니다.';
}

// 확정 요청 본문은 한 곳에서만 만든다 — 한 건이든 여럿이든 저장소가 보는 열쇠는 같다(revision·project_id).
export function bridgeRequest(ids, confirmed, runtime={}) {
  const list=[...new Set((ids||[]).map(String).filter(Boolean))];
  if (!list.length) throw new Error('확정할 후보가 없습니다');
  return {project_id:runtime.project_id, expected_revision:runtime.revision,
    ...(list.length===1?{candidate_id:list[0]}:{candidate_ids:list}), confirmed:!!confirmed};
}

// 한 건씩 26번 누르게 하지 않는다. 티와 규격이 바뀌는 자리는 빼고(도면을 봐야 한다) 남은 것만 묶어 보여 준다.
export function routineCandidateIds(connectivity={}) {
  return (connectivity.candidates||[]).filter(c=>c.routine).map(c=>c.id);
}

// 파서가 낸 구조화 제안(dxf_parser.suggestions_apply) → 검토 줄 문구. 경고 문장과 같은 숫자를
// 사람이 검산할 수 있게 evidence 를 그대로 보여준다.
function suggestionReasonText(s={}) {
  const ev=s.evidence||{};
  if (s.code==='thin_pair')
    return `[얇은 오결합] 두께가 중앙값 ${Math.round(ev.median_mm||0)}mm 의 1/3 미만인 벽 ${ev.count||0}개 — pair_min=${s.opts?.pair_min} 을 주면 통과한다.`;
  if (s.code==='column_layer_looks_like_wall')
    return `[분류 의심] '${s.row_layer}' 은 column 인데 벽처럼 그려짐 — 짝 ${ev.paired||0}개, ${Math.round(ev.spacing_mm||0)}mm 간격.`;
  if (s.code==='width_conflict')
    return `[두께 불일치] 실측 ${Math.round(ev.detected_mm||0)}mm ≠ 선언 ${Math.round(ev.declared_mm||0)}mm · ${ev.count||0}개.`;
  return `'${s.row_layer}' 레이어 제안(${s.code}).`;
}

export function buildReviewEntries(elements={}, report={}, clashes=[], connectivity={}, rules=[], suggestions=[]) {
  const entries=[];
  // 시공기준 위반(construction_rules.py) — 간섭 다음, 형상은 안 바뀐다. 정보 항목(kind:'info', 예:
  // 배수 구배 필요 낙차)은 위반이 아니라 여기 안 싣는다 — V013 이 위반만 올리는 것과 같은 이유다.
  (rules || []).filter(r=>r.kind==='violation').forEach((rule, order) => {
    entries.push({
      key:`rule:${rule.rule}:${rule.eid}`, kind:'rule', eid:rule.eid||null, category:'시공기준', order,
      floor:'', action:'select',
      reason:`${rule.standard||''} ${rule.clause||''} 위반`.trim()
        +(rule.values?` · ${Object.entries(rule.values).map(([k,v])=>`${k}=${v}`).join(', ')}`:''),
    });
  });
  // 레이어 규칙 제안 — 간섭·시공기준 다음. 클릭 한 번으로 layer_map.csv 를 고친다(project_server
  // 의 /layer-rule). 파서가 이미 계산을 끝낸 세 가지만(thin_pair·column_layer_looks_like_wall·
  // width_conflict) — 사람 판단이 남은 일괄 수정(bulkHtml)과는 다른 신뢰 수준이다.
  (suggestions || []).forEach((suggestion, order) => {
    entries.push({
      key:`suggestion:${suggestion.code}:${suggestion.row_layer}`, kind:'suggestion',
      eid:null, label:suggestion.row_layer, category:'레이어 제안', order, floor:'', action:'select',
      apply:suggestion, reason:suggestionReasonText(suggestion),
    });
  });
  // 간섭은 목록 맨 앞에, 받은 순서(조치 종류 → 위치) 그대로 — 건축 분류 확인 수십 건에 묻히지 않게.
  (clashes || []).forEach((clash, order) => {
    const s=clash.struct||{}, m=clash.mep||{}, at=clash.at||[0,0], z=clash.z||[0,0];
    entries.push({
      key:`clash:${clash.id}`, kind:'clash', eid:m.eid||null, category:'간섭', order,
      floor:clash.level!=null?String(clash.level):'', action:'select', struct:s.eid||null,
      reason:`${clash.action} · (${Math.round(at[0])}, ${Math.round(at[1])}) z ${Math.round(z[0])}~${Math.round(z[1])} · `
        // 합성 슬래브(층 높이 선언)는 부재가 아니라 EID 가 없다 — 빈칸 대신 출처를 적는다.
        +`${s.category||''} ${s.eid||s.layer||''}${s.width_mm!=null?` 두께 ${s.width_mm}mm`:''} ↔ ${[m.system,m.size].filter(Boolean).join(' ')}`
        +assumedHeightText(clash)+wallEvidenceText(s),
    });
  });
  // 설비 이음 후보·계통 충돌은 간섭 다음, 받은 순서 그대로. 후보는 모델에 쓰지 않았다 — 확정은 사람이 한다.
  // 확정한 이음은 다음 파싱부터 후보에서 빠지므로 `bridges.applied` 로 따로 싣는다 — 취소할 자리가 화면에 있어야 한다.
  const gapRows=[...(connectivity?.bridges?.applied||[]).map(b=>({...b,confirmed:true})),
                 ...(connectivity?.candidates||[]),
                 ...(connectivity?.conflicts||[]).map(c=>({...c,conflict:true}))];
  gapRows.forEach((gap, order) => {
    const at=(gap.points||[[0,0]])[0], systems=(gap.systems||[]).map(s=>s??'계통 없음'), sizes=gap.sizes||[], eids=gap.eids||[];
    const kind=GAP_KIND[gap.kind]||gap.kind, size=sizes[0]===sizes[1]?(sizes[0]||''):sizes.join(' → ');
    const where=`틈 ${Math.round(gap.gap_mm)}mm · (${Math.round(at[0])}, ${Math.round(at[1])}) ↔ ${eids[1]||''}`;
    entries.push({
      key:`gap:${gap.id}`, kind:'gap', eid:eids[0]||null, order,
      category:gap.conflict?'계통 충돌':gap.confirmed?'확정한 이음':'연결 후보',
      floor:gap.level!=null?String(gap.level):'', action:'select',
      confirm:gap.conflict?null:{id:gap.id, confirmed:!!gap.confirmed},
      reason:gap.conflict?`다른 계통 끝이 ${kind}형으로 맞닿음 · ${systems.join(' ↔ ')} · ${where} — 도면 확인`
        :`${gap.confirmed?`${kind} 이음으로 확정함`:`${kind} 이음 후보`} · ${where} · `
         +`${[gap.systems?.[0],size].filter(Boolean).join(' ')}${gap.size_change?' · 규격 바뀜':''}`
         +assumedHeightText(gap),
    });
  });
  for (const category of Object.keys(elements).sort()) {
    for (const record of elements[category] || []) {
      const diagnostics=(record.edit_diagnostics || record.diagnostics || []).map(diagnosticText).filter(Boolean);
      const pendingReview=!!(record.review_required || record.needs_review);
      if (record.review_resolved && !record.review_ack_stale) continue;
      if (!pendingReview && !record.review_ack_stale && !diagnostics.length) continue;
      const eid=record.eid;
      if (!eid) continue;
      const reasons=[];
      if (record.review_ack_stale) reasons.push('이전 검토 확인이 형상 변경으로 만료됨');
      if (record.review_reason) reasons.push(String(record.review_reason));
      reasons.push(...diagnostics);
      entries.push({
        key:`element:${eid}`, kind:'element', eid, category,
        floor:floorKeyOf(record), reason:reasons.join(' · ') || '검토 필요', action:'select',
      });
    }
  }
  const relink=new Map((report.relink_suggestions || []).map(item=>[item.orphan,item]));
  for (const eid of [...new Set(report.ambiguous || [])].sort())
    entries.push({key:`ambiguous:${eid}`,kind:'ambiguous',eid:null,category:'ambiguous',floor:'',
      reason:'여러 부재가 같은 식별자를 공유하여 수정을 적용하지 못함',action:'open-orphan',orphan:eid});
  for (const eid of [...new Set(report.orphaned || [])].sort())
    entries.push({key:`orphan:${eid}`,kind:'orphan',eid:null,category:'orphan',floor:'',
      reason:relink.has(eid)?'이전 수정의 재연결 후보를 검토해야 함':'이전 수정을 붙일 부재를 찾지 못함',
      action:'open-orphan',orphan:eid});
  for (const eid of [...new Set(report.stale_reviews || [])].sort())
    if (!entries.some(x=>x.key===`element:${eid}`))
      entries.push({key:`stale:${eid}`,kind:'stale',eid:null,category:'stale',floor:'',
        reason:'이전 검토 또는 수정의 원본 형상이 변경됨',action:'open-orphan',orphan:eid});
  const categoryOrder=['wall','column','slab','beam','zone','opening','pipe','duct','tray','equipment'];
  const rank=entry=>({clash:-4,rule:-3,suggestion:-2,gap:-1,element:0})[entry.kind]??1;
  const catRank=entry=>{ const index=categoryOrder.indexOf(entry.category); return index<0?999:index; };
  const ordered=new Set(['clash','gap','rule','suggestion']);
  return entries.sort((a,b)=> rank(a)-rank(b) ||
    (a.kind===b.kind&&ordered.has(a.kind) ? a.order-b.order :
    a.floor.localeCompare(b.floor,undefined,{numeric:true}) ||
    catRank(a)-catRank(b) || a.key.localeCompare(b.key)));
}

// ── 검토 목록의 화면 문구 ────────────────────────────────────────────────────
// 여기 있는 것은 전부 **문자열 in / 문자열 out** 이다. DOM 은 `app.js` 가 붙이기만 한다 —
// 그래야 브라우저 없이 node:test 로 잠글 수 있고, 화면을 믿을 근거가 테스트에 남는다.
export function escHtml(value) {
  return String(value==null?'':value).replace(/[&<>"']/g,
    c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

export function reviewRowHtml(entry, canSave=false) {
  const head=`<button class="review-item" data-eid="${escHtml(entry.eid||'')}" data-orphan="${escHtml(entry.orphan||'')}">`
    +`<b>${escHtml(entry.eid||entry.orphan||entry.label||entry.kind)}</b> · ${escHtml(entry.category)}`
    +`${entry.floor?' · 층 '+escHtml(entry.floor):''}<small>${escHtml(entry.reason)}</small></button>`;
  // 이음 확정·규칙 적용은 서버(프로젝트)에 저장된다 — 독립 HTML 로 연 미리보기에는 저장할 곳이
  // 없어 버튼을 두지 않는다.
  if (entry.confirm && canSave)
    return `<div class="review-row">${head}<button class="confirm-gap" data-gap="${escHtml(entry.confirm.id)}"`
      +` data-confirmed="${entry.confirm.confirmed?'1':''}">${entry.confirm.confirmed?'확정 취소':'이음 확정'}</button></div>`;
  if (entry.apply && canSave)
    return `<div class="review-row">${head}<button class="apply-suggest" data-apply='${escHtml(JSON.stringify(entry.apply))}'>적용</button></div>`;
  return head;
}

export function batchButtonHtml(ids, canSave=false) {
  const count=(ids||[]).length;
  if (!count || !canSave) return '';
  return `<button class="confirm-gap" data-batch="1">일상 이음 후보 ${count}건 일괄 확정</button>`;
}

// 원본 창은 층 bbox 전체에 맞춘다 — 그런데 실무 도면은 평면 옆에 설계변경 표·안내선을 크게 둔다
// (실측: 평면은 14.6m × 11.2m 인데 설계변경 블록이 69m × 79m, 안내선이 105m × 65m → 평면이 손톱만 했다).
// 파서가 만든 부재만의 bbox 로 맞추면 건물이 창을 채운다. 부재가 없으면 null 을 내고 종전대로 층 bbox.
export function modelBounds(elements={}, floorId=null, pad=0.2) {
  const xs=[], ys=[];
  for (const records of Object.values(elements)) for (const rec of records||[]) {
    if (floorId!=null && floorKeyOf(rec) && floorKeyOf(rec)!==String(floorId)) continue;
    if (rec.center) {
      const r=rec.radius||200;
      xs.push(rec.center[0]-r, rec.center[0]+r); ys.push(rec.center[1]-r, rec.center[1]+r);
    }
    for (const p of rec.centerline||rec.points||[]) { xs.push(p[0]); ys.push(p[1]); }
  }
  if (!xs.length) return null;
  const x0=Math.min(...xs), x1=Math.max(...xs), y0=Math.min(...ys), y1=Math.max(...ys);
  const margin=Math.max(500,((x1-x0)+(y1-y0))*pad/2);
  return {x:x0-margin, y:y0-margin, w:Math.max(1,x1-x0+margin*2), h:Math.max(1,y1-y0+margin*2)};
}

export function deriveSectionRange(elements={}, floors=[], zRange) {
  const values=[];
  for (const floor of floors || []) if (Number.isFinite(Number(floor.z))) values.push(Number(floor.z));
  for (const [category,records] of Object.entries(elements)) {
    for (const record of records || []) {
      let range;
      try { range=zRange(category,record); } catch (_) { range=null; }
      if (!range) continue;
      for (const z of range) if (Number.isFinite(Number(z))) values.push(Number(z));
    }
  }
  return values.length ? {min:Math.min(...values),max:Math.max(...values)} : {min:0,max:3000};
}

export function isZVisible(z, sectionHeight, enabled) {
  return !enabled || Number(z) <= Number(sectionHeight);
}

export function reconcileSection(range, currentHeight) {
  const min=Number(range.min), max=Number(range.max);
  const requested=Number(currentHeight);
  const height=Number.isFinite(requested)?Math.max(min,Math.min(max,requested)):max;
  return {min,max,height};
}

export function uniqueByEid(records, eid) {
  const matches=records.filter(record=>record&&record.eid===eid);
  return matches.length===1?matches[0]:null;
}

export function fitDistance(radius, verticalFovDegrees, aspect) {
  const vertical=verticalFovDegrees*Math.PI/360;
  const horizontal=Math.atan(Math.tan(vertical)*Math.max(.01,aspect));
  return Math.max(radius,.5)/Math.sin(Math.min(vertical,horizontal))*1.1;
}

// 되돌리기·다시 실행 — `edits` 는 평평한 객체라 스냅샷 **문자열** 스택 둘이면 충분하다.
// '한 동작 = 한 단계'(같은 값은 단계를 만들지 않는다). 새 동작은 redo 를 버린다 — 분기를 남기면
// 되돌린 뒤 다른 편집을 했을 때 '다시 실행'이 어느 미래로 가는지 아무도 모른다.
// ★ `rebase()` 는 기준선만 옮긴다 — **되돌릴 미래를 지우지 않는다**. 저장 응답(onAck)이 이걸 부르는데,
//   여기서 redo 를 비우면 되돌리기가 저장을 부르고 그 저장이 redo 를 먹어 '다시 실행'이 영원히 안 된다
//   (브라우저 QA 에서 실제로 그랬다). 미래를 버리는 것은 **새 동작**(push)뿐이다.
export function createHistory(initial, limit = 100) {
  let last = initial;
  const undoStack = [], redoStack = [];
  return {
    push(snapshot) {
      if (snapshot === last) return false;
      undoStack.push(last); last = snapshot; redoStack.length = 0;
      if (undoStack.length > limit) undoStack.shift();
      return true;
    },
    rebase(snapshot) { last = snapshot; },
    undo() { if (!undoStack.length) return null; redoStack.push(last); last = undoStack.pop(); return last; },
    redo() { if (!redoStack.length) return null; undoStack.push(last); last = redoStack.pop(); return last; },
    current() { return last; },
    sizes() { return {undo: undoStack.length, redo: redoStack.length}; },
  };
}

// 물량 요약 패널(Phase 4) — `boq_export.aggregate()` 의 `{섹션: [헤더, rows, 합계]}` 를 화면이
// 쓰는 모양으로 축약한다. 저장할 때마다 서버가 다시 계산해 보내므로(`geometry.boq`) 여기는 순수
// 변환만 — 언제 다시 부르는지는 app.js 가 정한다.
export function summarizeBoq(boq) {
  if (!boq || boq.error) return {error: (boq && boq.error) || null, wall: [], mep: [], openings: [], totals: null};
  const rowsOf = key => ((boq[key] || [[], []])[1]) || [];
  const totOf = key => ((boq[key] || [])[2]) || null;
  const openings = rowsOf('창호').map(r => ({kind: r[0], size: r[1], count: r[2]}));
  const wallTot = totOf('벽'), slabTot = totOf('슬래브');
  // 합계는 `aggregate()` 가 이미 낸 합계행을 그대로 쓴다 — 화면이 다시 더하면 두 수가 갈라진다.
  const totals = {
    wallCount: wallTot ? wallTot[1] : null,
    wallLengthM: wallTot ? wallTot[2] : null,
    slabAreaM2: slabTot ? slabTot[2] : null,
    doors: openings.filter(o => o.kind === '문').reduce((n, o) => n + (o.count || 0), 0),
    windows: openings.filter(o => o.kind === '창').reduce((n, o) => n + (o.count || 0), 0),
  };
  return {
    error: null,
    wall: rowsOf('벽').map(r => ({key: r[0], lengthM: r[2], heightM: r[3], volumeM3: r[5]})),
    mep: rowsOf('MEP').map(r => ({label: r[0], size: r[1], lengthM: r[3], basis: r[4]})),
    openings,
    totals,
  };
}

// 물량표가 무엇을 세고 무엇을 안 세는지 — 이 문장이 없으면 읽는 사람이 "개구부는 당연히 뺐겠지"
// 로 읽는다(벽 면적은 중심선 길이×높이 그대로다, `boq_export.py`). 같은 문장이 Excel 2행에도 간다.
export const BOQ_SCOPE_NOTE =
  '벽 면적·체적은 중심선 길이×높이×두께(개구부 미차감) · 접합부 중복·마감·할증 미포함 · 지지 개수는 하한 · 검토용';

// 벽·기둥 표에는 근거 열이 없다 — 개별 높이는 이미 계약대로 정직하지만(overrides > 레코드 > params,
// 섞이면 그 행은 높이를 비운다), 그 값이 '질문에서 선언'인지 'params 기본값'인지는 표만 보면 모른다.
// 숫자를 다시 말하지 않고 출처만 말한다 — 숫자는 행마다 이미 있다(아래 boqBodyHtml 의 h= 값).
export function boqHeightBasisText(heightDeclared) {
  return heightDeclared
    ? '층고: 열 때 선언한 값 (행마다 h= 참고)'
    : '층고: 미선언 — 레이어·params 기본값 사용';
}

export function boqBodyHtml(boq) {
  const s = summarizeBoq(boq);
  if (s.error) return `<div>물량 계산 실패: ${escHtml(s.error)}</div>`;
  const parts = [];
  const t = s.totals;
  if (t) {
    // 없는 것은 줄이지 않고 **빼낸다** — 벽이 없는 설비 도면에 "벽 0m" 을 적으면 0 을 실측처럼 읽는다.
    const bits = [];
    if (t.slabAreaM2) bits.push(`슬래브 ${t.slabAreaM2}㎡`);
    if (t.wallCount) bits.push(`벽 ${t.wallLengthM}m · ${t.wallCount}개`);
    if (t.doors || t.windows) bits.push(`문/창 ${t.doors}/${t.windows}`);
    if (bits.length) parts.push(`<div class="kv boq-total"><span>합계</span><span>${escHtml(bits.join(' · '))}</span></div>`);
  }
  if (s.wall.length) parts.push('<div><b>벽</b>'
    + s.wall.map(w => `<div class="kv"><span>${escHtml(w.key)}</span>`
      + `<span>h${w.heightM===''?'?':w.heightM}m · ${w.lengthM}m · ${w.volumeM3}㎥</span></div>`).join('')
    + '</div>');
  if (s.mep.length) parts.push('<div><b>MEP</b>'
    + s.mep.map(m => `<div class="kv"><span>${escHtml(m.label)} ${escHtml(m.size)}</span><span>${m.lengthM}m(${escHtml(m.basis)})</span></div>`).join('')
    + '</div>');
  if (s.openings.length) parts.push('<div><b>창호</b>'
    + s.openings.map(o => `<div class="kv"><span>${escHtml(o.kind)} ${escHtml(o.size)}</span><span>${o.count}개</span></div>`).join('')
    + '</div>');
  if (!parts.length) return '없음';
  parts.push(`<div class="boq-note">${escHtml(BOQ_SCOPE_NOTE)}</div>`);
  return parts.join('');
}
