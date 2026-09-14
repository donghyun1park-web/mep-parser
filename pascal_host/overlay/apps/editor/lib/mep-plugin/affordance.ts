import {
  type AnyNodeId,
  type FloorplanAffordance,
  type FloorplanAffordanceSession,
  useScene,
} from '@pascal-app/core'
import { snapPointToGrid } from '@pascal-app/editor'
import type { MepRunNode } from './schema'

type Point3 = [number, number, number]

/**
 * 평면에서 경로 점 하나를 끈다 — XZ 만 옮기고 높이는 그대로(Pascal 기본 duct 점 손잡이와 같다).
 * 격자 스냅, Shift 는 스냅 없이. 저장은 편집기 자동 저장이 하고, 모양이 바뀐 경로는 다리가
 * 새 수동 레코드(delete + add)로 낸다 — 원본 EID 를 나눠 갖지 않는다.
 */
export function createMepPathPointAffordance(): FloorplanAffordance<MepRunNode> {
  return {
    start({ node, payload, gridSnapStep, sceneApi }): FloorplanAffordanceSession {
      const id = node.id as AnyNodeId
      const pointIndex = (payload as { pointIndex?: number } | null)?.pointIndex
      const initial = node.path.map((p) => [p[0], p[1], p[2]] as Point3)
      if (pointIndex === undefined || !initial[pointIndex]) {
        return { affectedIds: [id], apply() {}, canCommit: () => false }
      }
      const write = (path: Point3[]) => {
        if (sceneApi) sceneApi.update(id, { path } as never)
        else useScene.getState().updateNode(id, { path } as never)
      }
      let moved = false
      return {
        affectedIds: [id],
        apply({ planPoint, modifiers }) {
          const [x, z] = modifiers.shiftKey
            ? [planPoint[0], planPoint[1]]
            : snapPointToGrid([planPoint[0], planPoint[1]], gridSnapStep)
          write(initial.map((p, i) => (i === pointIndex ? [x, p[1], z] : p)))
          moved = true
        },
        canCommit: () => moved,
      }
    },
  }
}
