import test from 'node:test';
import assert from 'node:assert/strict';
import * as THREE from '../vendor/three.module.js';
import { sourceSampleCurve, mepPropertyKeys } from '../frontend/src/mep_preview_geometry.js';

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
  assert.deepEqual(mepPropertyKeys('wall'), ['width','height']);
});
