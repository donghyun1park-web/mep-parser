// 계약 v3 사각 단면 규약의 TS 사본 — `geom_contract.section_axes` · `rect_parts` 와 **같은 식**이다.
// 좌표는 Pascal 레벨 로컬 m(Y-up). Pascal `rectSectionAxes`(UP = +Y)를 그대로 쓰는데, 다리의
// 축 맞바꿈 (x,y,z)→(x,z,y) 은 거울상이라 파이썬 링을 맞바꾼 것과 좌표가 정확히 같다
// (tests/test_pascal_host.py 가 두 구현을 좌표로 대조한다). 규칙은 파이썬 쪽에서만 바꾼다.
export type V3 = [number, number, number]

const EPS = 1e-9
// 길이 허용치(m). 파이썬은 mm 에서 1e-6 을 쓴다 — 같은 자리다.
const LEN_TOL = 1e-9

const sub = (a: V3, b: V3): V3 => [a[0] - b[0], a[1] - b[1], a[2] - b[2]]
const add = (a: V3, b: V3): V3 => [a[0] + b[0], a[1] + b[1], a[2] + b[2]]
const mul = (a: V3, s: number): V3 => [a[0] * s, a[1] * s, a[2] * s]
const dot = (a: V3, b: V3): number => a[0] * b[0] + a[1] * b[1] + a[2] * b[2]
const cross = (a: V3, b: V3): V3 => [
  a[1] * b[2] - a[2] * b[1],
  a[2] * b[0] - a[0] * b[2],
  a[0] * b[1] - a[1] * b[0],
]
const norm = (a: V3): number => Math.sqrt(dot(a, a))
const dist = (a: V3, b: V3): number => norm(sub(a, b))

function unit(a: V3): V3 {
  const n = norm(a)
  if (n < EPS) throw new Error('zero-length direction')
  return [a[0] / n, a[1] / n, a[2] / n]
}

/** Pascal `rectSectionAxes`: roll 0 에서 폭은 수평(UP × dir), 연직 구간은 world X. */
export function sectionAxes(direction: V3, roll = 0): { width: V3; height: V3 } {
  const d = unit(direction)
  let x = cross([0, 1, 0], d)
  x = norm(x) < 1e-4 ? [1, 0, 0] : unit(x)
  const z = unit(cross(x, d))
  const c = Math.cos(roll)
  const s = Math.sin(roll)
  return { width: add(mul(x, c), mul(z, s)), height: add(mul(x, -s), mul(z, c)) }
}

function rotateMin(v: V3, a: V3, b: V3): V3 {
  const k0 = cross(a, b)
  const s = norm(k0)
  const c = dot(a, b)
  if (s < 1e-12) {
    if (c > 0) return [v[0], v[1], v[2]]
    throw new Error('route reverses direction (180 degree turn)')
  }
  const k = mul(k0, 1 / s)
  return add(add(mul(v, c), mul(cross(k, v), s)), mul(k, dot(k, v) * (1 - c)))
}

function dedup(points: ReadonlyArray<readonly [number, number, number]>): V3[] {
  const out: V3[] = []
  for (const p of points) {
    const q: V3 = [p[0], p[1], p[2]]
    const last = out[out.length - 1]
    if (!last || dist(last, q) > LEN_TOL) out.push(q)
  }
  return out
}

const SIGNS = [
  [-1, -1],
  [1, -1],
  [1, 1],
  [-1, 1],
] as const

function corners(p: V3, w: V3, h: V3, hw: number, hh: number): V3[] {
  return SIGNS.map(([sx, sy]) => add(p, add(mul(w, sx * hw), mul(h, sy * hh))))
}

function ringList(pts: V3[], width: number, height: number, roll: number, closed: boolean): V3[][] {
  const n = pts.length
  const segs = closed ? n : n - 1
  const dirs: V3[] = []
  for (let i = 0; i < segs; i++) dirs.push(unit(sub(pts[(i + 1) % n]!, pts[i]!)))
  const hw = width / 2
  const hh = height / 2
  const mitre = (p: V3, a: V3, b: V3, w: V3, h: V3, along: V3): V3[] => {
    const m = add(a, b)
    if (norm(m) < 1e-9) throw new Error('route reverses direction (180 degree turn)')
    const nrm = unit(m)
    return corners(p, w, h, hw, hh).map((c) => sub(c, mul(along, dot(sub(c, p), nrm) / dot(along, nrm))))
  }
  const first = sectionAxes(dirs[0]!, roll)
  let w = first.width
  let h = first.height
  const rings: V3[][] = [
    closed ? mitre(pts[0]!, dirs[segs - 1]!, dirs[0]!, w, h, dirs[0]!) : corners(pts[0]!, w, h, hw, hh),
  ]
  for (let i = 1; i < (closed ? n : n - 1); i++) {
    const a = dirs[i - 1]!
    const b = dirs[i]!
    rings.push(mitre(pts[i]!, a, b, w, h, a))
    w = rotateMin(w, a, b)
    h = rotateMin(h, a, b)
  }
  if (closed) {
    w = rotateMin(w, dirs[segs - 1]!, dirs[0]!)
    h = rotateMin(h, dirs[segs - 1]!, dirs[0]!)
    if (dist(w, first.width) > 1e-6 || dist(h, first.height) > 1e-6) {
      throw new Error('closed rectangular route twists around its loop')
    }
    rings.push(rings[0]!.map((p) => [p[0], p[1], p[2]] as V3))
  } else {
    rings.push(corners(pts[n - 1]!, w, h, hw, hh))
  }
  return rings
}

function isClosed(pts: V3[]): boolean {
  return pts.length > 3 && dist(pts[0]!, pts[pts.length - 1]!) <= LEN_TOL
}

function invertedSegments(rings: V3[][], pts: V3[], closed: boolean): Set<number> {
  const body = closed ? pts.slice(0, -1) : pts
  const n = body.length
  const bad = new Set<number>()
  for (let i = 0; i < (closed ? n : n - 1); i++) {
    const d = unit(sub(body[(i + 1) % n]!, body[i]!))
    for (let k = 0; k < 4; k++) {
      if (dot(sub(rings[i + 1]![k]!, rings[i]![k]!), d) <= LEN_TOL) {
        bad.add(i)
        break
      }
    }
  }
  return bad
}

/** 한 조각의 링(`geom_contract.rect_rings`). */
export function rectRings(points: ReadonlyArray<readonly [number, number, number]>, width: number, height: number, roll = 0): V3[][] {
  const pts = dedup(points)
  if (pts.length < 2) return []
  const closed = isClosed(pts)
  return ringList(closed ? pts.slice(0, -1) : pts, width, height, roll, closed)
}

/** 뒤집히지 않는 조각들(`geom_contract.rect_parts`) — 짧은 급꺾임 구간은 직각 토막. */
export function rectParts(points: ReadonlyArray<readonly [number, number, number]>, width: number, height: number, roll = 0): V3[][][] {
  const pts = dedup(points)
  if (pts.length < 2) return []
  const closed = isClosed(pts)
  const whole = ringList(closed ? pts.slice(0, -1) : pts, width, height, roll, closed)
  if (invertedSegments(whole, pts, closed).size === 0) return [whole]
  const open = ringList(pts, width, height, roll, false)
  const bad = invertedSegments(open, pts, false)
  const dirs = pts.slice(1).map((p, i) => unit(sub(p, pts[i]!)))
  const frames = [sectionAxes(dirs[0]!, roll)]
  for (let i = 1; i < dirs.length; i++) {
    const prev = frames[i - 1]!
    frames.push({
      width: rotateMin(prev.width, dirs[i - 1]!, dirs[i]!),
      height: rotateMin(prev.height, dirs[i - 1]!, dirs[i]!),
    })
  }
  const hw = width / 2
  const hh = height / 2
  const cap = (p: V3, i: number) => corners(p, frames[i]!.width, frames[i]!.height, hw, hh)
  const parts: V3[][][] = []
  let cur: V3[][] | null = null
  for (let i = 0; i < dirs.length; i++) {
    if (bad.has(i)) {
      if (cur) {
        cur.push(cap(pts[i]!, i - 1))
        parts.push(cur)
        cur = null
      }
      parts.push([cap(pts[i]!, i), cap(pts[i + 1]!, i)])
      continue
    }
    if (!cur) cur = [cap(pts[i]!, i)]
    if (i === dirs.length - 1 || bad.has(i + 1)) {
      cur.push(cap(pts[i + 1]!, i))
      parts.push(cur)
      cur = null
    } else {
      cur.push(open[i + 1]!)
    }
  }
  return parts
}

/** 링 → 비색인 삼각형 좌표(평면 음영). 닫힌 표시(첫 링 == 끝 링)는 뚜껑 없이 고리로. */
export function sweepTriangles(rings: V3[][]): number[] {
  const last = rings[rings.length - 1]
  const closed =
    rings.length > 2 && !!last && rings[0]!.every((p, k) => dist(p, last[k]!) <= LEN_TOL)
  const body = closed ? rings.slice(0, -1) : rings
  const m = body.length
  const quads: V3[][] = []
  for (let i = 0; i < (closed ? m : m - 1); i++) {
    const a = body[i]!
    const b = body[(i + 1) % m]!
    for (let k = 0; k < 4; k++) {
      const k2 = (k + 1) % 4
      quads.push([a[k]!, a[k2]!, b[k2]!, b[k]!])
    }
  }
  if (!closed && m > 0) {
    const a = body[0]!
    const b = body[m - 1]!
    quads.push([a[3]!, a[2]!, a[1]!, a[0]!])
    quads.push([b[0]!, b[1]!, b[2]!, b[3]!])
  }
  let volume = 0
  for (const q of quads) {
    for (let i = 1; i < 3; i++) volume += dot(q[0]!, cross(q[i]!, q[i + 1]!)) / 6
  }
  const out: number[] = []
  for (const q of quads) {
    const tris = volume < 0 ? [[q[0]!, q[2]!, q[1]!], [q[0]!, q[3]!, q[2]!]] : [[q[0]!, q[1]!, q[2]!], [q[0]!, q[2]!, q[3]!]]
    for (const t of tris) for (const p of t) out.push(p[0], p[1], p[2])
  }
  return out
}
