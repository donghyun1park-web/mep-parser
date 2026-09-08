// Use the drawing group's screen CTM: it includes viewBox letterboxing,
// layout offsets, zoom and scale(1,-1). Bounding-box ratios omit the padding.
function determinant(matrix) {
  if (!matrix || !['a','b','c','d','e','f'].every(key => Number.isFinite(matrix[key]))) return 0;
  const det = matrix.a * matrix.d - matrix.b * matrix.c;
  return Number.isFinite(det) ? det : 0;
}

export function screenToDrawing(matrix, clientX, clientY) {
  const det = determinant(matrix);
  if (!det || !Number.isFinite(clientX) || !Number.isFinite(clientY)) return null;
  const x = clientX - matrix.e, y = clientY - matrix.f;
  const point = [(matrix.d*x - matrix.c*y)/det, (matrix.a*y - matrix.b*x)/det];
  return point.every(Number.isFinite) ? point : null;
}

// The edit view preserves aspect ratio, so both drawing axes share this scale.
export function drawingUnitsPerPixel(matrix) {
  const det = determinant(matrix);
  if (!det) return 0;
  const units = Math.hypot(matrix.d, matrix.b) / Math.abs(det);
  return Number.isFinite(units) ? units : 0;
}
