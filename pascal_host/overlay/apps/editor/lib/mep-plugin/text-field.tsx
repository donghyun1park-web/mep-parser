'use client'

import { useEffect, useState } from 'react'
import type { MepRunNode } from './schema'

type FieldProps = { node: MepRunNode; onUpdate: (patch: Partial<MepRunNode>) => void }

// 속성 창에 글자 필드 종류가 없어(number/enum/boolean…) 계통·재질은 직접 그린다.
// 계통 이름은 현장마다 다르다(SA·RA·OA·EA·heating …) — 목록으로 묶지 않는다.
function makeTextField(key: 'system' | 'material', label: string, placeholder: string) {
  return function MepTextField({ node, onUpdate }: FieldProps) {
    const current = node[key] ?? ''
    const [value, setValue] = useState(current)
    useEffect(() => setValue(current), [current])
    return (
      <label className="flex items-center justify-between gap-2 px-2 py-1 text-xs" translate="no">
        <span className="text-muted-foreground">{label}</span>
        <input
          className="w-28 rounded border border-border bg-background px-1.5 py-0.5 text-right"
          onBlur={() => {
            const next = value.trim()
            if (next !== current) onUpdate({ [key]: next } as Partial<MepRunNode>)
          }}
          onChange={(event) => setValue(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter') event.currentTarget.blur()
          }}
          placeholder={placeholder}
          value={value}
        />
      </label>
    )
  }
}

export const SystemField = makeTextField('system', '계통', 'SA · RA · heating')
export const MaterialField = makeTextField('material', '재질', 'PB · 강관')
