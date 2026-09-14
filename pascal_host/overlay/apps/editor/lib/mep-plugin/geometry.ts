import type { GeometryContext } from '@pascal-app/core'
import {
  BufferGeometry,
  CylinderGeometry,
  Float32BufferAttribute,
  Group,
  Mesh,
  MeshStandardMaterial,
  SphereGeometry,
  Vector3,
} from 'three'
import type { MepRunNode } from './schema'
import { rectParts, sweepTriangles } from './section'

// 계통 색. 도면 관례를 따르되 모르는 계통은 카테고리 기본색 — 색으로 계통을 **정하지는** 않는다.
const SYSTEM_COLORS: Record<string, string> = {
  SA: '#4f86c6',
  RA: '#d4825a',
  OA: '#43a047',
  EA: '#8d8d8d',
  SUPPLY: '#4f86c6',
  RETURN: '#d4825a',
  HEATING: '#e53935',
}
const CATEGORY_COLORS: Record<string, string> = {
  'mep-parser:pipe': '#c0392b',
  'mep-parser:duct': '#7f8c8d',
  'mep-parser:tray': '#8e7cc3',
}

export function mepSystemColor(node: MepRunNode): string {
  const key = (node.system ?? '').trim().toUpperCase()
  return SYSTEM_COLORS[key] ?? CATEGORY_COLORS[node.type] ?? '#7f8c8d'
}

const UP = new Vector3(0, 1, 0)

/**
 * 경로 → 메시. 원형 단면은 구간마다 원통 + 꺾인 점에 구, 사각 단면은 계약 v3 공통 마이터 링
 * (`section.ts` = 파이썬 `rect_parts`) — FreeCAD·Blender 가 같은 링으로 만든다.
 */
export function buildMepRunGeometry(node: MepRunNode, _ctx?: GeometryContext): Group {
  const group = new Group()
  const path = node.path
  if (!path || path.length < 2) return group
  const material = new MeshStandardMaterial({ color: mepSystemColor(node), roughness: 0.55, metalness: 0.15 })

  if (node.shape === 'round') {
    const radius = Math.max((node.diameterMm ?? 20) / 2000, 0.0005)
    const pts = path.map(([x, y, z]) => new Vector3(x, y, z))
    for (let i = 0; i < pts.length - 1; i++) {
      const a = pts[i]!
      const dir = new Vector3().subVectors(pts[i + 1]!, a)
      const length = dir.length()
      if (length < 1e-6) continue
      const mesh = new Mesh(new CylinderGeometry(radius, radius, length, 20, 1, false), material)
      mesh.position.copy(a).addScaledVector(dir, 0.5)
      mesh.quaternion.setFromUnitVectors(UP, dir.normalize())
      mesh.name = `mep-run-section-${i}`
      group.add(mesh)
    }
    for (let i = 1; i < pts.length - 1; i++) {
      const joint = new Mesh(new SphereGeometry(radius, 20, 10), material)
      joint.position.copy(pts[i]!)
      joint.name = `mep-run-joint-${i}`
      group.add(joint)
    }
    return group
  }

  const width = Math.max((node.widthMm ?? 200) / 1000, 0.0005)
  const height = Math.max((node.heightMm ?? 100) / 1000, 0.0005)
  try {
    for (const rings of rectParts(path, width, height, node.sectionRoll ?? 0)) {
      const geom = new BufferGeometry()
      geom.setAttribute('position', new Float32BufferAttribute(sweepTriangles(rings), 3))
      geom.computeVertexNormals()
      const mesh = new Mesh(geom, material)
      mesh.name = 'mep-run-rect'
      group.add(mesh)
    }
  } catch {
    // U자 반전 등 형상을 만들 수 없는 경로 — 비워 둔다. 저장 게이트(V010)가 경로를 따로 막는다.
  }
  return group
}
