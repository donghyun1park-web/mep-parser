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

export function buildReviewEntries(elements={}, report={}) {
  const entries=[];
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
  const rank=entry=>entry.kind==='element'?0:1;
  const catRank=entry=>{ const index=categoryOrder.indexOf(entry.category); return index<0?999:index; };
  return entries.sort((a,b)=> rank(a)-rank(b) ||
    a.floor.localeCompare(b.floor,undefined,{numeric:true}) ||
    catRank(a)-catRank(b) || a.key.localeCompare(b.key));
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
