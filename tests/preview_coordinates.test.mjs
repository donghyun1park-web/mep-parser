import test from 'node:test';
import assert from 'node:assert/strict';
import { screenToDrawing, drawingUnitsPerPixel } from '../frontend/src/svg_coordinates.js';

function near(actual, expected) {
  assert.equal(actual.length, expected.length);
  actual.forEach((value, i) => assert.ok(Math.abs(value - expected[i]) < 1e-7,
    `${actual} should equal ${expected}`));
}

// These CTMs include the SVG's aspect-ratio padding and drawing Y-axis flip.
test('wide viewport: endpoint follows the cursor through horizontal letterboxing', () => {
  const matrix = {a:0.1, b:0, c:0, d:-0.1, e:300, f:800};
  near(screenToDrawing(matrix, 350, 700), [500, 1000]);
  near(screenToDrawing(matrix, 410, 670), [1100, 1300]);
  near([drawingUnitsPerPixel(matrix)], [10]);
});

test('tall viewport: dragging does not compress the Y displacement into the SVG bounds', () => {
  const matrix = {a:0.05, b:0, c:0, d:-0.05, e:0, f:650};
  near(screenToDrawing(matrix, 250, 600), [5000, 1000]);
  near(screenToDrawing(matrix, 280, 580), [5600, 1400]);
  near([drawingUnitsPerPixel(matrix)], [20]);
});

test('browser offset, zoom and negative drawing coordinates use the current transform', () => {
  const matrix = {a:0.25, b:0, c:0, d:-0.25, e:1120, f:-720};
  near(screenToDrawing(matrix, 120, 280), [-4000, -4000]);
  near(screenToDrawing(matrix, 160, 310), [-3840, -4120]);
  assert.equal(drawingUnitsPerPixel(matrix), 4);
});

test('observed sample-plan endpoint maps back from its actual rendered position', () => {
  const scale = 846 / 12080;
  const matrix = {a:scale, b:0, c:0, d:-scale, e:1040*scale, f:460+4000*scale};
  near(screenToDrawing(matrix, 423, 733.1291390728477), [5000, 100]);
  const [x,y] = screenToDrawing(matrix, 483, 703);
  near([matrix.a*x+matrix.e, matrix.d*y+matrix.f], [483,703]);
});

test('handle and snap distance remain measured in CSS pixels after zoom or resize', () => {
  for (const scale of [0.01, 0.05, 0.5, 2]) {
    const matrix = {a:scale, b:0, c:0, d:-scale, e:900, f:100};
    const units = drawingUnitsPerPixel(matrix);
    near([7.2*units*scale, 12*units*scale], [7.2,12]);
  }
});

test('hidden or invalid SVG transforms cannot move an endpoint to bogus coordinates', () => {
  for (const matrix of [null, {a:0,b:0,c:0,d:0,e:0,f:0},
    {a:1,b:0,c:0,d:-1,e:NaN,f:0}]) {
    assert.equal(screenToDrawing(matrix, 100, 100), null);
    assert.equal(drawingUnitsPerPixel(matrix), 0);
  }
  assert.equal(screenToDrawing({a:1,b:0,c:0,d:-1,e:0,f:0}, NaN, 100), null);
});
