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
      if (edit.overrides) effective.overrides = Object.assign({}, effective.overrides || {}, clone(edit.overrides));
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
    'needs_review','review_reason','review_resolved','review_ack_stale','source']);
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

  function randomUuid() {
    if (typeof crypto!=='undefined' && crypto.randomUUID) return crypto.randomUUID();
    return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g,c=>{
      const r=Math.random()*16|0,v=c==='x'?r:(r&3|8); return v.toString(16);
    });
  }
  function makeId(prefix, uuid=randomUuid, level) {
    return (level == null || level === '' ? '' : String(level)+':')+prefix+':'+uuid();
  }

  function makeManualWall(points, source, defaults={}, uuid=randomUuid) {
    const src=source || {}, overrides=Object.assign({},clone(src.overrides || {}));
    if (overrides.width == null) overrides.width=defaults.width == null ? 200 : defaults.width;
    if (overrides.height == null && defaults.height != null) overrides.height=defaults.height;
    const out={kind:'polyline',closed:false,points:clone(points),centerline:clone(points),
      pairing:'manual',layer:src.layer || '(수동)',confidence:1,needs_review:false,
      source:'manual_preview',overrides,eid:makeId('wm',uuid,src.level)};
    for (const key of ['level','floor','elevation','z_base','attrs']) {
      if (src[key] != null) out[key]=clone(src[key]);
    }
    if (out.z_base == null) out.z_base=0;
    return out;
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
      if (values.deleted) edit.deleted=true; else delete edit.deleted;
    } else {
      if (category && (category!==currentCategory || edit.category)) edit.category=category;
      else delete edit.category;
      const ov=Object.assign({},edit.overrides || {});
      if (Number.isFinite(values.width)) ov.width=values.width;
      if (Number.isFinite(values.height)) ov[category==='slab'?'thickness':'height']=values.height;
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

  return {clone,deriveBaseElements,materializeElements,mergePresentation,movePolylinePoint,splitPolyline,joinPolylines,snapPoint,
          makeManualWall,makeId,applyProperties,backupKey,restoreBackup,createSaveQueue,propertyValue};
});
