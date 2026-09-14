import type { NextRequest } from 'next/server'
import { forwardToMepProject } from '@/lib/mep-project'
import { guardSceneApiRequest } from '@/lib/scene-api-security'

export const dynamic = 'force-dynamic'

// 원본 DXF 선(층별 SVG, 북쪽이 위) — 스냅샷의 guide 노드가 이 주소를 밑그림으로 쓴다.
export async function GET(request: NextRequest) {
  const guard = guardSceneApiRequest(request, { skipRateLimit: true })
  if (guard) return guard
  const floor = request.nextUrl.searchParams.get('floor') ?? ''
  return forwardToMepProject(`/pascal/source.svg?floor=${encodeURIComponent(floor)}`, { method: 'GET' })
}
