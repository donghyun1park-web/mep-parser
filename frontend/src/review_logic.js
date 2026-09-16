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

export function buildReviewEntries(elements={}, report={}, clashes=[], connectivity={}) {
  const entries=[];
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
  const rank=entry=>({clash:-2,gap:-1,element:0})[entry.kind]??1;
  const catRank=entry=>{ const index=categoryOrder.indexOf(entry.category); return index<0?999:index; };
  return entries.sort((a,b)=> rank(a)-rank(b) ||
    (a.kind===b.kind&&(a.kind==='clash'||a.kind==='gap') ? a.order-b.order :
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
    +`<b>${escHtml(entry.eid||entry.orphan||entry.kind)}</b> · ${escHtml(entry.category)}`
    +`${entry.floor?' · 층 '+escHtml(entry.floor):''}<small>${escHtml(entry.reason)}</small></button>`;
  // 이음 확정은 서버(프로젝트)에 저장된다 — 독립 HTML 로 연 미리보기에는 저장할 곳이 없어 버튼을 두지 않는다.
  if (!entry.confirm || !canSave) return head;
  return `<div class="review-row">${head}<button class="confirm-gap" data-gap="${escHtml(entry.confirm.id)}"`
    +` data-confirmed="${entry.confirm.confirmed?'1':''}">${entry.confirm.confirmed?'확정 취소':'이음 확정'}</button></div>`;
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
