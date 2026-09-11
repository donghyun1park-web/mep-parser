import type { NextRequest } from 'next/server'
import { forwardToMepProject } from '@/lib/mep-project'
import { guardSceneApiRequest } from '@/lib/scene-api-security'

export const dynamic = 'force-dynamic'

// 편집한 씬 → 프로젝트 저장소. 저장소가 원본과 대조해 **변경 명령**으로 저장한다
// (전체 덮어쓰기 아님). 요청 본문은 손대지 않고 넘긴다 — revision · 스냅샷 해시 ·
// 작업 ID 검사는 저장소 쪽 한 곳에만 있다.
export async function POST(request: NextRequest) {
  const guard = guardSceneApiRequest(request, { skipRateLimit: true })
  if (guard) return guard
  return forwardToMepProject('/pascal/apply', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: await request.text(),
  })
}
