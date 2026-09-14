import type { FloorplanGeometry, FloorplanPoint, GeometryContext } from '@pascal-app/core'
import { mepSystemColor } from './geometry'
import type { MepRunNode } from './schema'

const BODY = '#9ca3af'

/** 평면 폭(m) — 원형은 지름, 사각은 폭. 실제 치수로 그린다. */
export function mepRunPlanWidth(node: MepRunNode): number {
  return (node.shape === 'round' ? (node.diameterMm ?? 20) : (node.widthMm ?? 200)) / 1000
}

/**
 * 평면 표시: 실제 폭의 몸체 + 계통색 점선 중심선. 연직 구간은 평면에서 한 점으로 접히므로
 * 연속 중복 점을 버리고, 순수 입상관은 원으로 그린다. 선택하면 경로 점마다 끌기 손잡이.
 */
export function buildMepRunFloorplan(node: MepRunNode, ctx: GeometryContext): FloorplanGeometry | null {
  if (!node.path || node.path.length < 2) return null
  const points: FloorplanPoint[] = []
  const indexMap: number[] = []
  node.path.forEach(([x, , z], i) => {
    const prev = points[points.length - 1]
    if (prev && Math.abs(prev[0] - x) < 1e-6 && Math.abs(prev[1] - z) < 1e-6) return
    points.push([x, z])
    indexMap.push(i)
  })
  const view = ctx.viewState
  const selected = !!view?.selected
  const highlighted = selected || !!view?.highlighted
  const chrome = highlighted && view?.palette ? view.palette.selectedStroke : null
  const color = mepSystemColor(node)
  const width = mepRunPlanWidth(node)

  if (points.length < 2) {
    const p = points[0]!
    return {
      kind: 'group',
      children: [
        { kind: 'circle', cx: p[0], cy: p[1], r: Math.max(width / 2, 0.01), fill: BODY, stroke: chrome ?? color, strokeWidth: 0.02, opacity: 0.9 },
      ],
    }
  }
  const children: FloorplanGeometry[] = [
    {
      kind: 'polyline',
      points,
      stroke: chrome ?? BODY,
      strokeWidth: width,
      strokeLinecap: 'round',
      strokeLinejoin: 'round',
      opacity: highlighted ? 0.95 : 0.8,
    },
    {
      kind: 'polyline',
      points,
      stroke: color,
      strokeWidth: 1.5,
      vectorEffect: 'non-scaling-stroke',
      strokeDasharray: '5 4',
      strokeLinecap: 'round',
      strokeLinejoin: 'round',
      opacity: 0.9,
    },
  ]
  if (selected) {
    points.forEach((point, k) => {
      children.push({
        kind: 'endpoint-handle',
        point,
        state: 'idle',
        affordance: 'move-path-point',
        payload: { pointIndex: indexMap[k]! },
      })
    })
  }
  return { kind: 'group', children }
}
