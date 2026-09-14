import type { NextRequest } from 'next/server'
import { forwardToMepProject } from '@/lib/mep-project'
import { guardSceneApiRequest } from '@/lib/scene-api-security'

export const dynamic = 'force-dynamic'

// 검토 목록(검토 사유 · 붙일 벽이 없는 개구부 · 끊긴 이음 · Pascal 로 못 보낸 부재).
export async function GET(request: NextRequest) {
  const guard = guardSceneApiRequest(request, { skipRateLimit: true })
  if (guard) return guard
  return forwardToMepProject('/pascal/review', { method: 'GET' })
}
