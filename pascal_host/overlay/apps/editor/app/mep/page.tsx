import { MepProjectLoader } from '@/components/mep-project-loader'

export const dynamic = 'force-dynamic'

// MEP-Parser 프로젝트를 Pascal 편집기로 연다. 불러오기·저장은 전부 `/api/mep/*`.
export default function MepProjectPage() {
  return <MepProjectLoader />
}
