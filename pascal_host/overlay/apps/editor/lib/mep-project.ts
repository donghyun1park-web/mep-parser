// MEP-Parser 프로젝트 저장소로 넘기는 **서버 측** 프록시.
//
// 저장 기준은 MEP-Parser 의 프로젝트 저장소 하나다 — Pascal 은 편집 화면만 맡는다.
// 저장소 서버(ProjectServer)는 127.0.0.1 임의 포트에서 Bearer 토큰으로 지켜지고,
// 그 토큰은 이 Next 서버 프로세스의 환경변수에만 있다. 브라우저는 같은 출처의
// `/api/mep/*` 만 부르므로 토큰이 브라우저로 **가지 않는다**(`NEXT_PUBLIC_` 금지).
import { NextResponse } from 'next/server'

type MepPath = '/pascal/snapshot' | '/pascal/apply'

export async function forwardToMepProject(path: MepPath, init: RequestInit): Promise<NextResponse> {
  const base = process.env.MEP_PROJECT_URL
  const token = process.env.MEP_PROJECT_TOKEN
  if (!base || !token) {
    return NextResponse.json({ error: 'mep_project_not_configured' }, { status: 503 })
  }
  const headers = new Headers(init.headers)
  headers.set('Authorization', `Bearer ${token}`)
  let response: Response
  try {
    response = await fetch(base + path, { ...init, headers, cache: 'no-store' })
  } catch {
    return NextResponse.json({ error: 'mep_project_unreachable' }, { status: 502 })
  }
  // 상태 코드를 그대로 넘긴다 — 409(낡은 revision·스냅샷)는 화면이 새로고침을 권해야 한다.
  return new NextResponse(await response.text(), {
    status: response.status,
    headers: {
      'Content-Type': response.headers.get('Content-Type') ?? 'application/json',
      'Cache-Control': 'no-store',
    },
  })
}
