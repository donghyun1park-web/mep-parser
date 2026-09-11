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

export function mepPropertyKeys(category) {
  if(category==='pipe') return ['diameter',null];
  if(category==='duct'||category==='tray') return ['width_mm','height_mm'];
  return ['width',category==='slab'?'thickness':'height'];
}
