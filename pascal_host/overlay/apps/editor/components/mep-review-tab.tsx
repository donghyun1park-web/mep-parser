'use client'

// 한 화면 검토: 선택한 부재의 출처·치수·높이와, 저장소가 말하는 검토 목록.
// 목록을 누르면 그 부재를 고른다(EID → 노드). 저장으로 revision 이 오르면 다시 불러온다
// (`mep:revision` 이벤트 — `mep-project-loader.tsx` 가 스냅샷을 갈아 끼울 때 보낸다).
import { useScene } from '@pascal-app/core'
import { useViewer } from '@pascal-app/viewer'
import { useCallback, useEffect, useState } from 'react'

type Item = { category: string; eid: string | null; layer?: string | null; reason: string }
type Summary = { clash_total: number; clash_assumed: number; gap_total: number; gap_assumed: number }
type Review = { revision: number; items: Item[]; unconvertible: Item[]; summary?: Summary }
type MepMeta = Record<string, unknown>
type AnyNode = { id: string; type: string; name?: string; metadata?: { mep?: MepMeta } } & Record<string, unknown>

// 출처로 보여 줄 키 — 좌표는 metadata 에 없다(다리 규약).
const META_KEYS = ['eid', 'layer', 'pairing', 'review_reason', 'system', 'material', 'nominal_size',
  'source_length_mm', 'elevation_source', 'derived_from'] as const
// 노드에서 읽는 치수·높이(m 는 mm 로 바꿔 보인다).
const SIZE_KEYS: [string, string, number][] = [
  ['thickness', '두께 mm', 1000], ['height', '높이 mm', 1000], ['widthMm', '폭 mm', 1],
  ['heightMm', '높이 mm', 1], ['diameterMm', '지름 mm', 1], ['elevation', '상단 m', 1],
]

function eidOf(node: AnyNode | undefined): string | undefined {
  const eid = node?.metadata?.mep?.eid
  return typeof eid === 'string' ? eid : undefined
}

function bannerText(summary: Summary | undefined): string {
  if (!summary || (!summary.clash_assumed && !summary.gap_assumed)) return ''
  const parts: string[] = []
  if (summary.clash_total) parts.push(`간섭 ${summary.clash_total}건 중 가정 높이 ${summary.clash_assumed}건`)
  if (summary.gap_total) parts.push(`연결 후보 ${summary.gap_total}건 중 가정 ${summary.gap_assumed}건`)
  return `${parts.join(' · ')} — 평면도에는 높이가 없습니다. 설비 설정에서 계통별 설치 높이·규격을 선언하면 줄어듭니다.`
}

export function MepReviewTab() {
  const [review, setReview] = useState<Review | null>(null)
  const [error, setError] = useState<string | null>(null)
  const selectedIds = useViewer((s) => s.selection.selectedIds)
  const nodes = useScene((s) => s.nodes) as unknown as Record<string, AnyNode>

  const load = useCallback(async () => {
    try {
      const response = await fetch('/api/mep/review', { cache: 'no-store' })
      if (!response.ok) throw new Error(`검토 목록을 불러오지 못했습니다 (${response.status})`)
      setReview((await response.json()) as Review)
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }, [])

  useEffect(() => {
    load()
    const onRevision = () => load()
    window.addEventListener('mep:revision', onRevision)
    return () => window.removeEventListener('mep:revision', onRevision)
  }, [load])

  const select = (eid: string | null) => {
    if (!eid) return
    const hit = Object.values(nodes).find((n) => eidOf(n) === eid)
    if (hit) useViewer.getState().setSelection({ selectedIds: [hit.id] as never })
  }

  const selected = selectedIds.map((id) => nodes[id]).filter((n): n is AnyNode => Boolean(n))
  return (
    <div className="flex flex-col gap-3 p-3 text-xs" translate="no">
      <section>
        <h3 className="mb-1 font-medium text-sm">선택한 부재</h3>
        {selected.length === 0 && <p className="text-muted-foreground">부재를 고르면 출처·치수가 보입니다.</p>}
        {selected.map((node) => (
          <dl className="mb-2 grid grid-cols-[auto_1fr] gap-x-2 gap-y-0.5" key={node.id}>
            <dt className="text-muted-foreground">종류</dt>
            <dd>{node.type}</dd>
            {META_KEYS.filter((k) => node.metadata?.mep?.[k] != null).map((k) => (
              <div className="contents" key={k}>
                <dt className="text-muted-foreground">{k}</dt>
                <dd className="break-all">{String(node.metadata?.mep?.[k])}</dd>
              </div>
            ))}
            {SIZE_KEYS.filter(([k]) => typeof node[k] === 'number').map(([k, label, factor]) => (
              <div className="contents" key={k}>
                <dt className="text-muted-foreground">{label}</dt>
                <dd>{Math.round((node[k] as number) * factor * 10) / 10}</dd>
              </div>
            ))}
            {!node.metadata?.mep && <dd className="col-span-2 text-amber-600">원본 출처 없음(새로 그린 부재)</dd>}
          </dl>
        ))}
      </section>
      <section>
        <div className="mb-1 flex items-center justify-between">
          <h3 className="font-medium text-sm">검토 목록{review ? ` · r${review.revision}` : ''}</h3>
          <button className="rounded border border-border px-1.5 py-0.5" onClick={load} type="button">
            새로고침
          </button>
        </div>
        {error && <p className="text-red-600">{error}</p>}
        {/* 평면도에는 높이가 없다 — 목록 전체가 가정 위에 서 있다는 사실을 줄마다의 표시로는 볼 수 없다. */}
        {bannerText(review?.summary) && (
          <p className="mb-1 border-amber-500/60 border-l-2 bg-amber-500/10 px-1.5 py-1 text-amber-200">
            {bannerText(review?.summary)}
          </p>
        )}
        {review && review.items.length === 0 && <p className="text-muted-foreground">검토할 부재가 없습니다.</p>}
        <ul className="flex flex-col gap-0.5">
          {review?.items.map((item, i) => (
            <li key={`${item.eid}-${item.reason}-${i}`}>
              <button
                className="w-full rounded px-1 py-0.5 text-left hover:bg-white/10"
                onClick={() => select(item.eid)}
                type="button"
              >
                <span className="text-muted-foreground">{item.category}</span> · {item.reason}
                {item.eid ? <span className="text-muted-foreground"> · {item.eid}</span> : null}
              </button>
            </li>
          ))}
        </ul>
      </section>
      {review && review.unconvertible.length > 0 && (
        <section>
          <h3 className="mb-1 font-medium text-sm">편집 화면에 못 보낸 부재 {review.unconvertible.length}</h3>
          <ul className="flex flex-col gap-0.5 text-muted-foreground">
            {review.unconvertible.map((item, i) => (
              <li key={`${item.eid}-${i}`}>
                {item.category} · {item.reason}
                {item.eid ? ` · ${item.eid}` : ''}
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  )
}
