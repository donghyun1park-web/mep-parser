import test from 'node:test';
import assert from 'node:assert/strict';
import {execFileSync} from 'node:child_process';
import vm from 'node:vm';
import {fileURLToPath} from 'node:url';
import * as THREE from '../vendor/three.module.js';
import { sourceSampleCurve, mepPropertyKeys, linearMepGeometry, footprintMepGeometry } from '../frontend/src/mep_preview_geometry.js';

test('tube sampling passes every source vertex, including a tiny bend after a long run', () => {
  const points = [[0,0,0],[100,0,0],[100.01,.01,0],[100.01,100,0]].map(p=>new THREE.Vector3(...p));
  const curve = sourceSampleCurve(THREE, points);
  points.forEach((p,i)=>assert.ok(curve.getPointAt(i/(points.length-1)).distanceTo(p)<1e-10));
  const geometry = new THREE.TubeGeometry(curve, points.length-1, .00795, 16, false);
  const positions=geometry.getAttribute('position');
  for(let i=0;i<positions.count;i++) for(const value of [positions.getX(i),positions.getY(i),positions.getZ(i)]) assert.ok(Number.isFinite(value));
  geometry.computeBoundingBox();
  assert.ok(Math.abs(geometry.boundingBox.max.z-geometry.boundingBox.min.z-.0159)<1e-7);
});

test('MEP property edits use OD/rectangular dimensions', () => {
  assert.deepEqual(mepPropertyKeys('pipe'), ['diameter', null]);
  assert.deepEqual(mepPropertyKeys('duct'), ['width_mm','height_mm']);
  assert.deepEqual(mepPropertyKeys('duct','round'), ['diameter',null]);
  assert.deepEqual(mepPropertyKeys('wall'), ['width','height']);
});

test('unknown dimensions do not block selecting the keys for deletion or repair', () => {
  const script=execFileSync(process.env.MEP_PYTHON||'python',['-c',
    'import sys;sys.stdout.reconfigure(encoding="utf-8");import preview;print(preview.contract_script())'],
    {cwd:fileURLToPath(new URL('..',import.meta.url)),encoding:'utf8'});
  const context={}; vm.runInNewContext(script,context);
  for(const section_shape of ['round','rect']) {
    const record={dimension_status:'unknown',section_shape};
    assert.throws(()=>context.MepContract.gcMepDimensions('duct',record,{}),/Unresolved/);
    const shape=context.MepContract.gcSectionShape('duct',record);
    assert.deepEqual(mepPropertyKeys('duct',shape),section_shape==='round'?['diameter',null]:['width_mm','height_mm']);
  }
});

test('round and flat duct geometry retains physical section dimensions without NaN', () => {
  const points=[[0,0,0],[2,0,0]].map(p=>new THREE.Vector3(...p));
  for(const dims of [{diameter:100},{diameter:125},{width_mm:110,height_mm:54},{width_mm:204,height_mm:60}]) {
    const pieces=linearMepGeometry(THREE,points,dims,.001);
    assert.equal(pieces.length,1);
    const geometry=pieces[0].geometry;
    for(const value of geometry.attributes.position.array) assert.ok(Number.isFinite(value));
    geometry.computeBoundingBox();
    const bounds=geometry.boundingBox;
    assert.ok(Math.abs(bounds.max.y-bounds.min.y-(dims.diameter??dims.width_mm)*.001)<1e-7);
    assert.ok(Math.abs(bounds.max.z-bounds.min.z-(dims.diameter??dims.height_mm)*.001)<1e-7);
  }
});

test('terminal symbol envelope uses its 80 mm contract height', () => {
  const shape=new THREE.Shape().moveTo(0,0).lineTo(.15,0).lineTo(.15,.15).lineTo(0,.15).closePath();
  const geometry=footprintMepGeometry(THREE,shape,[2520,2600],.001);
  geometry.computeBoundingBox();
  assert.ok(Math.abs(geometry.boundingBox.max.z-.08)<1e-8);
  assert.equal(geometry.boundingBox.min.z,0);
});
