import { BaseNode } from '@pascal-app/core'
import { z } from 'zod'

export const MEP_RUN_KINDS = {
  pipe: 'mep-parser:pipe',
  duct: 'mep-parser:duct',
  tray: 'mep-parser:tray',
} as const
export type MepRunKind = (typeof MEP_RUN_KINDS)[keyof typeof MEP_RUN_KINDS]

/**
 * 우리 설비 경로(배관·덕트·트레이). 치수는 **mm**, 경로는 Pascal 레벨 로컬 m(Y-up).
 *
 * Pascal 기본 duct/pipe-segment 는 미국 주택 규격(인치·범위 제한)이라 PB 외경 15.9mm 나
 * 높이 54mm 덕트가 들어가지 않는다 — 계약 v3 단면을 그대로 담는 전용 노드다. 저장 기준은
 * 우리 저장소이고 다리(`pascal_bridge.py`)가 이 필드를 읽고 쓴다. 씬 적재 때 스키마에 없는
 * 키는 떨어져 나가므로 다리가 쓰는 필드는 **전부** 여기 선언한다.
 */
export function mepRunSchema<K extends MepRunKind>(kind: K) {
  return BaseNode.extend({
    id: z.string(),
    type: z.literal(kind).default(kind),
    path: z.array(z.tuple([z.number(), z.number(), z.number()])).min(2),
    shape: z.enum(['round', 'rect']).default(kind === MEP_RUN_KINDS.pipe ? 'round' : 'rect'),
    diameterMm: z.number().positive().optional(),
    widthMm: z.number().positive().optional(),
    heightMm: z.number().positive().optional(),
    sectionRoll: z.number().default(0),
    system: z.string().default(''),
    material: z.string().default(''),
  })
}

export const PipeRunNode = mepRunSchema(MEP_RUN_KINDS.pipe)
export const DuctRunNode = mepRunSchema(MEP_RUN_KINDS.duct)
export const TrayRunNode = mepRunSchema(MEP_RUN_KINDS.tray)

export type MepRunNode =
  | z.infer<typeof PipeRunNode>
  | z.infer<typeof DuctRunNode>
  | z.infer<typeof TrayRunNode>
