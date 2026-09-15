// Keep each analytically sampled DXF vertex; a long straight run must not consume all bend samples.
export function sourceSampleCurve(THREE, points) {
  if(points.length<2) throw new Error('A pipe needs at least two source points');
  class SourceCurve extends THREE.Curve {
    getPoint(t, target=new THREE.Vector3()) {
      const position=Math.max(0,Math.min(1,t))*(points.length-1);
      const index=Math.min(points.length-2,Math.floor(position));
      return target.copy(points[index]).lerp(points[index+1],position-index);
    }
    getPointAt(t,target) { return this.getPoint(t,target); }
    getTangentAt(t,target=new THREE.Vector3()) {
      const position=Math.max(0,Math.min(1,t))*(points.length-1);
      const index=Math.min(points.length-2,Math.floor(position));
      target.subVectors(points[index+1],points[index]).normalize();
      if(index>0 && Math.abs(position-index)<1e-8) {
        const previous=new THREE.Vector3().subVectors(points[index],points[index-1]).normalize();
        if(previous.dot(target)>-.999) target.add(previous).normalize();
      }
      return target;
    }
  }
  return new SourceCurve();
}

export function linearMepGeometry(THREE, points, dimensions, scale) {
  if(dimensions.diameter!==undefined) {
    const geometry=new THREE.TubeGeometry(sourceSampleCurve(THREE,points),points.length-1,dimensions.diameter*scale/2,16,false);
    return [{geometry,position:[0,0],rotation:0}];
  }
  const pieces=[];
  for(let i=0;i<points.length-1;i++) {
    const a=points[i],b=points[i+1],dx=b.x-a.x,dy=b.y-a.y,len=Math.hypot(dx,dy);
    if(len<1e-9) continue;
    pieces.push({geometry:new THREE.BoxGeometry(len,dimensions.width_mm*scale,dimensions.height_mm*scale),
      position:[(a.x+b.x)/2,(a.y+b.y)/2],rotation:Math.atan2(dy,dx)});
  }
  return pieces;
}

export function footprintMepGeometry(THREE, shape, range, scale) {
  return new THREE.ExtrudeGeometry(shape,{depth:(range[1]-range[0])*scale,bevelEnabled:false});
}

export function mepPropertyKeys(category, shape='rect') {
  if(category==='pipe'||shape==='round') return ['diameter',null];
  if(category==='duct'||category==='tray') return ['width_mm','height_mm'];
  return ['width',category==='slab'?'thickness':'height'];
}
