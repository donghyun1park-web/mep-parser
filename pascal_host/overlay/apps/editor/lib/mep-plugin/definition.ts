import type { AnyNodeDefinition, NodePort } from '@pascal-app/core'
import type { ZodObject } from 'zod'
import { createMepPathPointAffordance } from './affordance'
import { buildMepRunFloorplan } from './floorplan'
import { buildMepRunGeometry } from './geometry'
import { MEP_RUN_KINDS, type MepRunKind, type MepRunNode } from './schema'
import { MaterialField, SystemField } from './text-field'

const MM_PER_IN = 25.4

function outward(a: readonly number[], b: readonly number[]): [number, number, number] {
  const d: [number, number, number] = [a[0]! - b[0]!, a[1]! - b[1]!, a[2]! - b[2]!]
  const len = Math.hypot(d[0], d[1], d[2])
  return len < 1e-9 ? [1, 0, 0] : [d[0] / len, d[1] / len, d[2] / len]
}

/** 열린 끝 연결점. Pascal 포트 지름은 인치(사각은 면적 등가 원) — 표시·스냅용이고 저장값이 아니다. */
function runPorts(node: MepRunNode): NodePort[] {
  const path = node.path
  if (!path || path.length < 2) return []
  const mm =
    node.shape === 'round'
      ? (node.diameterMm ?? 20)
      : 2 * Math.sqrt(((node.widthMm ?? 200) * (node.heightMm ?? 100)) / Math.PI)
  const diameter = mm / MM_PER_IN
  const first = path[0]!
  const last = path[path.length - 1]!
  return [
    { id: 'start', position: first, direction: outward(first, path[1]!), diameter, system: node.system },
    { id: 'end', position: last, direction: outward(last, path[path.length - 2]!), diameter, system: node.system },
  ]
}

const isRound = (n: MepRunNode) => n.shape === 'round'

function parametrics(kind: MepRunKind) {
  return {
    groups: [
      {
        label: '단면',
        fields: [
          ...(kind === MEP_RUN_KINDS.pipe
            ? []
            : [{ key: 'shape', label: '모양', kind: 'enum', options: ['round', 'rect'], display: 'segmented' }]),
          { key: 'diameterMm', label: '지름', kind: 'number', unit: 'mm', min: 1, max: 3000, step: 0.1, visibleIf: isRound },
          { key: 'widthMm', label: '폭', kind: 'number', unit: 'mm', min: 1, max: 5000, step: 1, visibleIf: (n: MepRunNode) => !isRound(n) },
          { key: 'heightMm', label: '높이', kind: 'number', unit: 'mm', min: 1, max: 5000, step: 1, visibleIf: (n: MepRunNode) => !isRound(n) },
          {
            key: 'sectionRoll',
            label: '단면 회전',
            kind: 'number',
            unit: 'rad',
            min: -Math.PI,
            max: Math.PI,
            step: Math.PI / 4,
            visibleIf: (n: MepRunNode) => !isRound(n),
          },
        ],
      },
      {
        label: '계통·재질',
        fields: [
          { key: 'system', kind: 'custom', component: SystemField },
          { key: 'material', kind: 'custom', component: MaterialField },
        ],
      },
    ],
    // 모양을 바꿨는데 새 모양의 치수가 비어 있으면 **보이는 값**을 옮겨 채운다 — 빈 단면으로 두지 않는다.
    derive: (next: MepRunNode, patch: Partial<MepRunNode>) => {
      if (patch.shape === 'round' && next.diameterMm == null) return { diameterMm: next.widthMm ?? 100 }
      if (patch.shape === 'rect' && (next.widthMm == null || next.heightMm == null)) {
        return { widthMm: next.widthMm ?? next.diameterMm ?? 200, heightMm: next.heightMm ?? next.diameterMm ?? 100 }
      }
      return {}
    },
  }
}

export function mepRunDefinition(
  kind: MepRunKind,
  schema: ZodObject,
  label: string,
  icon: string,
  description: string,
): AnyNodeDefinition {
  return {
    kind,
    schemaVersion: 1,
    schema,
    category: 'utility',
    distributionRole: 'run',
    defaults: () => ({
      object: 'node',
      parentId: null,
      visible: true,
      metadata: {},
      path: [
        [0, 2.6, 0],
        [1, 2.6, 0],
      ],
      shape: kind === MEP_RUN_KINDS.pipe ? 'round' : 'rect',
      sectionRoll: 0,
      system: '',
      material: '',
    }),
    capabilities: { selectable: { hitVolume: 'bbox' }, deletable: true },
    parametrics: parametrics(kind),
    geometry: buildMepRunGeometry,
    geometryKey: (n: MepRunNode) =>
      JSON.stringify([n.path, n.shape, n.diameterMm, n.widthMm, n.heightMm, n.sectionRoll, n.system]),
    floorplan: buildMepRunFloorplan,
    floorplanAffordances: { 'move-path-point': createMepPathPointAffordance() },
    ports: runPorts,
    presentation: {
      label,
      description,
      icon: { kind: 'iconify', name: icon },
      paletteSection: 'structure',
      // 새 경로는 Pascal 기본 도구로 그린다 — 저장하면 다리가 우리 레코드로 바꾼다.
      hidden: true,
    },
    mcp: { description },
  } as unknown as AnyNodeDefinition
}
