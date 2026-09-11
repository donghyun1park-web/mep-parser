import type { NextRequest } from 'next/server'
import { forwardToMepProject } from '@/lib/mep-project'
import { guardSceneApiRequest } from '@/lib/scene-api-security'

export const dynamic = 'force-dynamic'

// 프로젝트 → Pascal 씬 스냅샷(revision · snapshot_sha256 · scene · report).
// Pascal 자신의 씬 API 와 같은 가드(출처 검사 · 루프백 또는 토큰)를 거친다.
export async function GET(request: NextRequest) {
  const guard = guardSceneApiRequest(request, { skipRateLimit: true })
  if (guard) return guard
  return forwardToMepProject('/pascal/snapshot', { method: 'GET' })
}
