(function(root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.MepEdit = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function() {
  'use strict';

  const clone = value => value == null ? value : JSON.parse(JSON.stringify(value));
  const pointsOf = rec => (rec && (rec.centerline || rec.points)) || [];
  const distance = (a,b) => Math.hypot(a[0]-b[0], a[1]-b[1]);

  function deriveBaseElements(canonicalElements, edits, excludedIds=[]) {
    const out={};
    const excluded=new Set(excludedIds || []);
    for (const [cat,records] of Object.entries(canonicalElements || {})) out[cat]=clone(records || []);
    const reversible=['overrides','width','height','thickness','diameter','width_mm','height_mm','sill',
      'review_resolved','_review_signature','_edited','review_ack_stale','edit_diagnostics'];
    for (const [eid,edit] of Object.entries(edits || {})) {
      let canonical=null, canonicalCategory=null;
      for (const [cat,records] of Object.entries(out)) {
        const index=records.findIndex(rec=>rec.eid===eid);
        if (index>=0) { canonical=records[index]; canonicalCategory=cat; records.splice(index,1); }
      }
      if (!edit || edit.added || excluded.has(eid) || !edit._source || (!canonical && !edit.deleted)) continue;
      const source=clone(edit._source), category=source.category || canonicalCategory || edit.category || 'wall';
      delete source.category;
      const record=canonical ? clone(canonical) : source;
      if (canonical) for (const field of reversible) {
        if (Object.prototype.hasOwnProperty.call(source,field)) record[field]=clone(source[field]);
        else delete record[field];
      }
      if (!Object.prototype.hasOwnProperty.call(source,'needs_review'))
        record.needs_review=!!source.review_reason;
      record.eid=eid;
      (out[category]=out[category]||[]).push(record);
    }
    return out;
  }

  function materializeElements(baseElements, edits) {
    const out = {};
    for (const [cat, records] of Object.entries(baseElements || {})) out[cat] = clone(records || []);
    const addedIds = new Set(Object.entries(edits || {}).filter(([,e])=>e && e.added).map(([eid])=>eid));
    for (const cat of Object.keys(out)) out[cat] = out[cat].filter(rec=>!addedIds.has(rec.eid));
    for (const [eid, edit] of Object.entries(edits || {})) {
      if (!edit || edit.added) continue;
      let found = null;
      for (const [cat, records] of Object.entries(out)) {
        const index = records.findIndex(rec=>rec.eid===eid);
        if (index >= 0) { found={cat,index,record:records[index]}; break; }
      }
      if (!found) continue;
      out[found.cat].splice(found.index, 1);
      if (edit.deleted) continue;
      const target = edit.category || found.cat;
      const effective = clone(found.record);
      if (edit.overrides) {
        const ov = Object.assign({}, effective.overrides || {});
        for (const [key, value] of Object.entries(edit.overrides)) {
          if (value == null) delete ov[key];   // null = 이 키를 지우고 도면값으로 되돌린다(병합은 union 만 하므로)
          else ov[key] = clone(value);
        }
        effective.overrides = ov;
      }
      (out[target] = out[target] || []).push(effective);
    }
    for (const edit of Object.values(edits || {})) {
      if (!edit || !edit.added || edit.deleted || !edit.record) continue;
      const target = edit.category || 'wall';
      (out[target] = out[target] || []).push(clone(edit.record));
    }
    return out;
  }

  function mergePresentation(baseElements, canonicalElements) {
    const canonical=new Map();
    for (const records of Object.values(canonicalElements || {}))
      for (const record of records || []) if (record.eid) canonical.set(record.eid,record);
    const fields=['needs_review','review_reason','review_resolved','review_ack_stale',
      '_review_signature','edit_diagnostics'];
    for (const records of Object.values(baseElements || {})) for (const record of records || []) {
      const source=canonical.get(record.eid); if (!source) continue;
      for (const field of fields) {
        if (Object.prototype.hasOwnProperty.call(source,field)) record[field]=clone(source[field]);
        else delete record[field];
      }
    }
    return baseElements;
  }

  function movePolylinePoint(record, index, point) {
    const out = clone(record);
    const moved = pointsOf(out).map(p=>p.slice());
    if (index < 0 || index >= moved.length) return out;
    moved[index] = point.slice();
    if (out.centerline) out.centerline = moved.map(p=>p.slice());
    if (out.points) out.points = moved.map(p=>p.slice());
    if (!out.centerline && !out.points) out.centerline = moved;
    return out;
  }

  function projection(point, a, b) {
    const dx=b[0]-a[0], dy=b[1]-a[1], l2=dx*dx+dy*dy;
    if (!l2) return {point:a.slice(), t:0, distance:distance(point,a)};
    const t=Math.max(0,Math.min(1,((point[0]-a[0])*dx+(point[1]-a[1])*dy)/l2));
    const q=[a[0]+dx*t,a[1]+dy*t];
    return {point:q,t,distance:distance(point,q)};
  }

  function splitPolyline(record, point) {
    const pts=pointsOf(record);
    if (pts.length < 2) return [];
    let best=null;
    for (let i=0;i<pts.length-1;i++) {
      const p=projection(point,pts[i],pts[i+1]);
      if (!best || p.distance < best.distance) best=Object.assign({index:i},p);
    }
    const splitPoint=best.point;
    const a=pts.slice(0,best.index+1).map(p=>p.slice());
    const b=pts.slice(best.index+1).map(p=>p.slice());
    if (distance(a[a.length-1],splitPoint)>1e-9) a.push(splitPoint.slice());
    if (!b.length || distance(splitPoint,b[0])>1e-9) b.unshift(splitPoint.slice());
    if (a.length<2 || b.length<2) return [];
    return [withPoints(record,a),withPoints(record,b)];
  }

  function withPoints(record, points) {
    const out=clone(record), p=points.map(x=>x.slice());
    if (out.centerline) out.centerline=p.map(x=>x.slice());
    if (out.points) out.points=p.map(x=>x.slice());
    if (!out.centerline && !out.points) out.centerline=p;
    return out;
  }

  function floorValue(rec) {
    if (rec.level != null) return ['level',rec.level];
    if (rec.floor != null) return ['floor',rec.floor];
    if (rec.elevation != null) return ['elevation',rec.elevation];
    return ['z_base',rec.z_base || 0];
  }

  const OMIT = new Set(['eid','points','centerline','kind','closed','pairing','confidence',
    'needs_review','review_reason','review_resolved','review_ack_stale','source',
    // MEP 전용 — 도면이 이음 자리에서 끊어 그린 두 조각은 이 값들이 서로 달라도 결합 대상이다.
    // 형상이 같은 물리적 경로면 조인한다; 계통·단면·elevation 이 다르면 아래 속성 비교가 여전히 막는다.
    'joints','source_refs','source_length_mm','route_length_mm','dimension_status']);
  function propertyValue(rec) {
    const out={};
    for (const key of Object.keys(rec || {}).sort()) {
      if (OMIT.has(key) || key[0]==='_') continue;
      out[key]=rec[key];
    }
    return JSON.stringify(out);
  }

  function isStraight(points, tolerance) {
    if (points.length < 2) return false;
    const a=points[0], b=points[points.length-1];
    const length=distance(a,b); if (length <= tolerance) return false;
    for (const p of points) {
      const area=Math.abs((b[0]-a[0])*(p[1]-a[1])-(b[1]-a[1])*(p[0]-a[0]));
      if (area/length > tolerance) return false;
    }
    return true;
  }

  function joinPolylines(first, second, options={}) {
    const joinTolerance=options.joinTolerance == null ? 600 : options.joinTolerance;
    const coordinateTolerance=options.coordinateTolerance == null ? 1 : options.coordinateTolerance;
    const a=pointsOf(first), b=pointsOf(second);
    if (a.length<2 || b.length<2 || !isStraight(a,coordinateTolerance) || !isStraight(b,coordinateTolerance)) return null;
    if (JSON.stringify(floorValue(first)) !== JSON.stringify(floorValue(second))) return null;
    if (propertyValue(first) !== propertyValue(second)) return null;
    const choices=[
      {d:distance(a[a.length-1],b[0]), left:a, right:b},
      {d:distance(a[a.length-1],b[b.length-1]), left:a, right:b.slice().reverse()},
      {d:distance(a[0],b[0]), left:a.slice().reverse(), right:b},
      {d:distance(a[0],b[b.length-1]), left:b, right:a}
    ].sort((x,y)=>x.d-y.d);
    const best=choices[0];
    if (best.d>joinTolerance || best.d>coordinateTolerance) return null;
    const combined=best.left.map(p=>p.slice());
    if (distance(combined[combined.length-1],best.right[0])<=coordinateTolerance) {
      if (distance(combined[combined.length-1],best.right[0])>1e-9) return null;
      combined.push(...best.right.slice(1).map(p=>p.slice()));
    }
    if (!isStraight(combined,coordinateTolerance)) return null;
    return withPoints(first,combined);
  }

  function sameFloor(rec, options) {
    if (options.level == null && options.floor == null && options.elevation == null && options.z_base == null) return true;
    const probe={};
    for (const k of ['level','floor','elevation','z_base']) if (options[k]!=null) probe[k]=options[k];
    return JSON.stringify(floorValue(rec))===JSON.stringify(floorValue(probe));
  }

  function snapPoint(point, records, options={}) {
    const tolerance=Math.min(options.screenToleranceMm == null ? Infinity : options.screenToleranceMm,
                             options.physicalToleranceMm == null ? 50 : options.physicalToleranceMm);
    let best={point:point.slice(),distance:tolerance,kind:null,eid:null};
    for (const rec of records || []) {
      if (!rec || rec.eid===options.skipEid || rec.deleted || !sameFloor(rec,options)) continue;
      const pts=pointsOf(rec);
      for (const p of pts) {
        const d=distance(point,p);
        if (d<best.distance) best={point:p.slice(),distance:d,kind:'endpoint',eid:rec.eid};
      }
      for (let i=0;i<pts.length-1;i++) {
        const p=projection(point,pts[i],pts[i+1]);
        if (p.t>0 && p.t<1 && p.distance<best.distance)
          best={point:p.point,distance:p.distance,kind:'segment',eid:rec.eid,segment:i};
      }
    }
    return best.kind ? best : {point:point.slice(),distance:null,kind:null,eid:null};
  }

  // Shift 직교 — 기준점에서 본 이동량이 큰 축만 남긴다(작은 축을 기준점 값으로 고정).
  // 스냅보다 **먼저** 건다: 직교로 민 뒤 그 자리 근처의 끝점에 붙어야, 직교가 스냅을 이기고
  // 어긋난 좌표가 남는 일이 없다. 기준점이 없으면(첫 클릭) 그대로 돌려준다.
  function orthoPoint(from, to) {
    if (!from || !to) return (to||[]).slice();
    const dx=to[0]-from[0], dy=to[1]-from[1];
    return Math.abs(dx) >= Math.abs(dy) ? [to[0], from[1]] : [from[0], to[1]];
  }

  function randomUuid() {
    if (typeof crypto!=='undefined' && crypto.randomUUID) return crypto.randomUUID();
    return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g,c=>{
      const r=Math.random()*16|0,v=c==='x'?r:(r&3|8); return v.toString(16);
    });
  }
  function makeId(prefix, uuid=randomUuid, level) {
    return (level == null || level === '' ? '' : String(level)+':')+prefix+':'+uuid();
  }

  // 카테고리별 수동 치수 키. 벽은 항상 폭을 채운다(기본 200mm) — 도면에 맞댈 근거가
  // 없는 새 벽도 치수 없이 세워지면 안 된다. 설비는 근거(이웃 부재·기본값)가 없으면 비워
  // 두고 geom_contract 의 기본값(DEFAULT_DIMS)에 맡긴다.
  const MANUAL_DIM_KEYS = {pipe:['diameter'], duct:['width_mm','height_mm'], tray:['width_mm','height_mm']};

  function makeManualRecord(category, points, source, defaults={}, uuid=randomUuid) {
    const src=source || {}, overrides=Object.assign({},clone(src.overrides || {}));
    if (category === 'wall') {
      if (overrides.width == null) overrides.width=defaults.width == null ? 200 : defaults.width;
      if (overrides.height == null && defaults.height != null) overrides.height=defaults.height;
    } else {
      for (const key of (MANUAL_DIM_KEYS[category] || [])) {
        if (overrides[key] == null && defaults[key] != null) overrides[key]=defaults[key];
      }
    }
    const out={kind:'polyline',closed:false,points:clone(points),centerline:clone(points),
      pairing:'manual',layer:src.layer || '(수동)',confidence:1,needs_review:false,
      source:'manual_preview',overrides,eid:makeId('wm',uuid,src.level)};
    // 수동 MEP 도 wm: 접두를 쓴다(Pascal 과 같은 규약) — category 는 EID 가 아니라 edits.json 의
    // `category` 필드가 결정한다(project_store.validate_edits 가 added 편집에 필수로 요구한다).
    for (const key of ['level','floor','elevation','z_base','attrs','system','section_shape']) {
      if (src[key] != null) out[key]=clone(src[key]);
    }
    if (category === 'wall' && out.z_base == null) out.z_base=0;
    return out;
  }
  function makeManualWall(points, source, defaults, uuid) {
    return makeManualRecord('wall', points, source, defaults, uuid);
  }

  function applyZ(ov, values) {
    // zKey 는 호출자가 정한다(geom_contract.ELEV_CATS 가 유일한 출처 — 여기서 카테고리를 다시 안 본다).
    // z===null 은 "지우고 도면값으로 되돌린다", 유한수면 설정, 그 외(비었거나 숫자 아님)는 손대지 않는다.
    if (!values.zKey) return;
    if (values.z === null) delete ov[values.zKey];
    else if (Number.isFinite(values.z)) ov[values.zKey] = values.z;
  }

  function applyProperties(existing, record, currentCategory, values) {
    const edit=clone(existing || {}), rec=clone(record || {});
    const category=values.category || currentCategory;
    if (edit.added) {
      edit.category=category;
      edit.record=rec;
      edit.record.overrides=Object.assign({},edit.record.overrides || {});
      if (Number.isFinite(values.width)) edit.record.overrides.width=values.width;
      if (Number.isFinite(values.height)) edit.record.overrides[category==='slab'?'thickness':'height']=values.height;
      applyZ(edit.record.overrides, values);
      if (values.deleted) edit.deleted=true; else delete edit.deleted;
    } else {
      if (category && (category!==currentCategory || edit.category)) edit.category=category;
      else delete edit.category;
      const ov=Object.assign({},edit.overrides || {});
      if (Number.isFinite(values.width)) ov.width=values.width;
      if (Number.isFinite(values.height)) ov[category==='slab'?'thickness':'height']=values.height;
      applyZ(ov, values);
      if (Object.keys(ov).length) edit.overrides=ov; else delete edit.overrides;
      if (values.deleted) edit.deleted=true; else delete edit.deleted;
    }
    if (values.reviewResolved) {
      edit.acknowledge=true;
      delete edit._review_signature;
    }
    return edit;
  }

  function backupKey(runtime) {
    if (runtime && runtime.project_id != null && runtime.revision != null)
      return 'mepEdits:'+encodeURIComponent(runtime.project_id)+':'+runtime.revision;
    return 'mepEdits:standalone';
  }

  function restoreBackup(raw, runtime) {
    if (!raw) return null;
    try {
      const stored=JSON.parse(raw);
      if (runtime && (stored.project_id!==runtime.project_id || stored.base_revision!==runtime.revision)) return null;
      return clone(stored.edits == null ? stored : stored.edits);
    } catch (_) { return null; }
  }

  function createSaveQueue(options) {
    let rev=options.revision || 0, ack=clone(options.acknowledged || {}), pending=null;
    let running=null, stopped=false, lastError=null;
    async function drain() {
      while (pending && !stopped) {
        const snapshot=pending; pending=null;
        try {
          if (options.onStatus) options.onStatus('saving');
          const response=await options.send(clone(snapshot),rev);
          rev=response.revision;
          ack=clone(response.edits == null ? snapshot : response.edits);
          lastError=null;
          if (options.onAck) options.onAck(clone(response),clone(snapshot));
          if (options.onStatus) options.onStatus(pending ? 'saving' : 'saved');
        } catch (error) {
          pending=snapshot;
          lastError=error; stopped=true;
          if (error && error.status===409) {
            const remote=error.state || {};
            rev=remote.revision == null ? (remote.current_revision == null ? rev : remote.current_revision) : remote.revision;
            if (options.onConflict) options.onConflict({local:clone(snapshot),remote:clone(remote),error});
            if (options.onStatus) options.onStatus('conflict',error);
          } else {
            if (options.onError) options.onError(error,clone(snapshot));
            if (options.onStatus) options.onStatus('error',error);
          }
        }
      }
      running=null;
    }
    function start() { if (!running && pending && !stopped) running=drain(); return running || Promise.resolve(); }
    return {
      enqueue(snapshot) { pending=clone(snapshot); start(); return running || Promise.resolve(); },
      retry() { stopped=false; return start(); },
      idle() { return running || Promise.resolve(); },
      revision() { return rev; },
      acknowledged() { return clone(ack); },
      pending() { return clone(pending); },
      error() { return lastError; },
      replaceRemote(state) { rev=state.revision; ack=clone(state.edits || {}); pending=null; stopped=false; lastError=null; }
    };
  }

  return {clone,deriveBaseElements,materializeElements,mergePresentation,movePolylinePoint,splitPolyline,joinPolylines,snapPoint,orthoPoint,
          makeManualWall,makeManualRecord,makeId,applyProperties,backupKey,restoreBackup,createSaveQueue,propertyValue};
});
