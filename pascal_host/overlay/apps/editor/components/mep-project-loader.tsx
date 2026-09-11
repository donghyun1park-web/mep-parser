'use client'

// MEP-Parser 프로젝트 ↔ Pascal 편집기. `SceneLoader` 와 같은 자리(onLoad/onSave)에
// 끼우되, 저장 대상이 Pascal 의 씬 DB 가 아니라 **MEP-Parser 프로젝트 저장소**다.
//
// - 불러오기: `/api/mep/snapshot` → revision · snapshot_sha256 · 씬
// - 저장(자동 저장, 1초 디바운스): `/api/mep/apply` 에 씬 + 기준 revision + 스냅샷
//   해시 + 작업 ID. 저장소가 원본과 대조해 변경 명령으로 저장한다.
// - 저장이 끝나면 돌려받은 스냅샷으로 **화면을 갈아 끼운다.** 이동한 부재는 새 EID·
//   새 노드 id 를 받으므로, 옛 씬으로 이어서 저장하면 방금 만든 복사본을 지운다.
import {
  applySceneGraphToEditor,
  Editor,
  type SceneGraph,
  type SidebarTab,
} from '@pascal-app/editor'
import { Hammer, Layers, Settings } from 'lucide-react'
import Image from 'next/image'
import { useCallback, useRef, useState } from 'react'
import { countGraphNodes, isEmptyGraphOverwrite } from '@/lib/empty-graph-guard'
import { BuildTab } from './build-tab'

// `SceneLoader` 와 같은 사이드바(장면 트리 · 작도 · 설정). 넘기지 않으면 편집기가
// 플러그인 탭만 띄워 **장면 트리가 없다** — 벽 안에 묻힌 부재를 고를 길이 없어진다.
// (`scene-loader.tsx` 의 SIDEBAR_TABS 를 옮긴 것. 그쪽이 내보내지 않아 복사한다.)
const tabIcon = (src: string) => (
  <Image alt="" className="h-8 w-8 object-contain" height={32} src={src} width={32} />
)
const SIDEBAR_TABS: (SidebarTab & { component: React.ComponentType })[] = [
  {
    id: 'site',
    label: 'Scene',
    component: () => null, // 기본 SitePanel 이 그린다
    mobileDefaultSnap: 0.5,
    mobileIcon: <Layers className="h-5 w-5" />,
    icon: tabIcon('/icons/scene.webp'),
  },
  {
    id: 'build',
    label: 'Build',
    component: BuildTab,
    mobileDefaultSnap: 0.5,
    mobileIcon: <Hammer className="h-5 w-5" />,
    icon: tabIcon('/icons/build.webp'),
  },
  {
    id: 'settings',
    label: 'Settings',
    component: () => null,
    mobileDefaultSnap: 0.5,
    mobileIcon: <Settings className="h-5 w-5" />,
    icon: tabIcon('/icons/settings.webp'),
  },
]

interface MepSnapshot {
  project_id: string
  revision: number
  snapshot_sha256: string
  scene: SceneGraph
  report?: { unconvertible?: unknown[]; foreign?: Record<string, number> }
}

type Status =
  | { kind: 'loading' }
  | { kind: 'saving' }
  | { kind: 'saved'; revision: number }
  | { kind: 'conflict' }
  | { kind: 'error'; message: string }

export function MepProjectLoader() {
  const base = useRef<MepSnapshot | null>(null)
  const serverNodeCount = useRef(0)
  const [status, setStatus] = useState<Status>({ kind: 'loading' })
  const [report, setReport] = useState<MepSnapshot['report']>()

  const adopt = useCallback((snap: MepSnapshot, applyToEditor: boolean) => {
    base.current = snap
    serverNodeCount.current = countGraphNodes(snap.scene)
    setReport(snap.report)
    setStatus({ kind: 'saved', revision: snap.revision })
    // 갈아 끼운 씬은 자동 저장을 한 번 더 부른다(메아리). 거르지 않는다 — 편집기가
    // 노드마다 기본값을 채워 넣어 우리 씬과 서명이 같아질 수 없고(실측: 매번 전송),
    // 저장소가 명령 0개로 `no_changes` 를 돌려주며 revision 을 올리지 않는다.
    if (applyToEditor) applySceneGraphToEditor(snap.scene)
  }, [])

  const handleLoad = useCallback(async () => {
    const response = await fetch('/api/mep/snapshot', { cache: 'no-store' })
    if (!response.ok) {
      setStatus({ kind: 'error', message: `프로젝트를 불러오지 못했습니다 (${response.status})` })
      return null
    }
    const snap = (await response.json()) as MepSnapshot
    adopt(snap, false)
    return snap.scene
  }, [adopt])

  const handleSave = useCallback(
    async (graph: SceneGraph) => {
      const current = base.current
      if (!current) return
      // 빈 씬으로 덮어쓰기 방지 — 로드 중인 빈 스토어가 저장되면 전부 삭제로 읽힌다.
      if (isEmptyGraphOverwrite(countGraphNodes(graph), serverNodeCount.current)) {
        setStatus({ kind: 'error', message: '빈 씬으로 덮어쓰려는 저장을 막았습니다' })
        return
      }
      setStatus({ kind: 'saving' })
      let response: Response
      try {
        response = await fetch('/api/mep/apply', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            scene: graph,
            expected_revision: current.revision,
            project_id: current.project_id,
            snapshot_sha256: current.snapshot_sha256,
            op_id: crypto.randomUUID(),
          }),
        })
      } catch (error) {
        setStatus({ kind: 'error', message: error instanceof Error ? error.message : '저장 실패' })
        return
      }
      const body = (await response.json().catch(() => ({}))) as {
        error?: string
        snapshot?: MepSnapshot
      }
      if (response.status === 409) {
        setStatus({ kind: 'conflict' })
        return
      }
      if (!response.ok) {
        setStatus({ kind: 'error', message: body.error ?? `저장 실패 (${response.status})` })
        return
      }
      // revision 이 오른 스냅샷만 갈아 끼운다. 같은 revision 을 다시 끼우면 그 메아리
      // 저장이 또 스냅샷을 받아 끼우기를 부른다 — 끝나지 않는 저장 고리가 된다.
      if (body.snapshot && body.snapshot.revision !== current.revision) adopt(body.snapshot, true)
      else setStatus({ kind: 'saved', revision: current.revision })
    },
    [adopt],
  )

  const unconvertible = report?.unconvertible?.length ?? 0
  const foreign = Object.values(report?.foreign ?? {}).reduce((a, b) => a + b, 0)
  const label =
    status.kind === 'loading'
      ? '프로젝트 불러오는 중'
      : status.kind === 'saving'
        ? '저장 중'
        : status.kind === 'saved'
          ? `저장됨 · r${status.revision}`
          : status.kind === 'conflict'
            ? '다른 곳에서 먼저 저장했습니다 — 새로고침하세요'
            : status.message

  return (
    <div className="relative h-screen w-screen">
      {/* translate="no": Pascal 화면이 영어라 브라우저가 자동 번역을 켜는데, 번역기가 이
          텍스트 노드를 갈아 끼우면 React 가 바꾼 revision 이 **화면에 안 나타난다**(실측:
          저장소는 r3 인데 표시는 r2 에 멈춤). 상태 표시는 번역 대상에서 뺀다. */}
      <div
        className="pointer-events-none absolute top-4 left-1/2 z-50 -translate-x-1/2 rounded-md border border-border bg-background/90 px-3 py-1.5 text-xs shadow-sm backdrop-blur"
        data-revision={status.kind === 'saved' ? status.revision : undefined}
        data-status={status.kind}
        data-testid="mep-status"
        translate="no"
      >
        MEP-Parser · {label}
        {unconvertible > 0 && ` · Pascal 에 못 보낸 부재 ${unconvertible}`}
        {foreign > 0 && ` · 표시 안 되는 설비 ${foreign}`}
      </div>
      <Editor
        layoutVersion="v2"
        onLoad={handleLoad}
        onSave={handleSave}
        projectId="mep-parser"
        sidebarTabs={SIDEBAR_TABS}
      />
    </div>
  )
}
