import { loadPlugin, nodeRegistry, type Plugin } from '@pascal-app/core'
import { mepRunDefinition } from './definition'
import { DuctRunNode, MEP_RUN_KINDS, PipeRunNode, TrayRunNode } from './schema'

/** 씬 그래프 `installedPlugins` 에 실리는 id — 다리(`pascal_bridge.py`)와 같은 값이어야 한다. */
export const MEP_PLUGIN_ID = 'mep-parser:mep'

export const mepPlugin: Plugin = {
  id: MEP_PLUGIN_ID,
  apiVersion: 1,
  nodes: [
    mepRunDefinition(MEP_RUN_KINDS.pipe, PipeRunNode, '배관', 'lucide:cylinder',
      'MEP-Parser 배관 경로 — 실제 외경(mm), 계약 v3 3D 경로.'),
    mepRunDefinition(MEP_RUN_KINDS.duct, DuctRunNode, '덕트', 'lucide:wind',
      'MEP-Parser 덕트 경로 — 사각·원형 단면(mm), 공통 마이터 링.'),
    mepRunDefinition(MEP_RUN_KINDS.tray, TrayRunNode, '트레이', 'lucide:rows-3',
      'MEP-Parser 케이블트레이 경로 — 사각 단면(mm).'),
  ],
}

let loading: Promise<void> | null = null

/**
 * 우리 설비 부재 종류를 **씬을 불러오기 전에** 한 번만 등록한다. 적재는 등록된 종류의 스키마로
 * 노드를 검사해 통과한 것만 올리므로, 늦게 등록하면 설비가 모르는 노드로 남는다.
 * `registerNode` 는 같은 종류 재등록을 막으므로 두 번 부르지 않는다.
 */
export function ensureMepPlugin(): Promise<void> {
  if (nodeRegistry.has(MEP_RUN_KINDS.duct)) return Promise.resolve()
  loading ??= loadPlugin(mepPlugin)
  return loading
}
