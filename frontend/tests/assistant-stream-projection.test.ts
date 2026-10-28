import { describe, expect, it } from 'vitest'
import { applyAssistantTextDelta, codePointLength } from '@/modules/assistant/stream-projection'

describe('assistant stream text projection', () => {
  it('uses Unicode code points for offsets and ignores exact replays', () => {
    const first = applyAssistantTextDelta(null, { message_id: 'message-1', block_index: 0, delta: '中🙂', offset: 0 })
    expect(first).toMatchObject({ accepted: true, needsSnapshot: false, projection: { text: '中🙂', codePointLength: 2 } })
    const replay = applyAssistantTextDelta(first.projection, { message_id: 'message-1', block_index: 0, delta: '🙂', offset: 1 })
    expect(replay).toMatchObject({ accepted: false, needsSnapshot: false, projection: first.projection })
    const overlap = applyAssistantTextDelta(first.projection, { message_id: 'message-1', block_index: 0, delta: '🙂好', offset: 1 })
    expect(overlap).toMatchObject({ accepted: true, projection: { text: '中🙂好', codePointLength: 3 } })
    expect(codePointLength('中🙂')).toBe(2)
  })

  it('requests a snapshot for gaps and conflicting replay text', () => {
    const start = applyAssistantTextDelta(null, { message_id: 'message-1', block_index: 0, delta: 'ab', offset: 0 }).projection
    expect(applyAssistantTextDelta(start, { message_id: 'message-1', block_index: 0, delta: 'd', offset: 3 })).toMatchObject({ accepted: false, needsSnapshot: true })
    expect(applyAssistantTextDelta(start, { message_id: 'message-1', block_index: 0, delta: 'x', offset: 1 })).toMatchObject({ accepted: false, needsSnapshot: true })
  })

  it('rejects malformed indexes and offsets without projecting text', () => {
    expect(applyAssistantTextDelta(null, { message_id: 'message-1', block_index: -1, delta: 'x', offset: 0 })).toMatchObject({ accepted: false, needsSnapshot: true, projection: null })
    expect(applyAssistantTextDelta(null, { message_id: 'message-1', block_index: 0, delta: 'x', offset: Number.NaN })).toMatchObject({ accepted: false, needsSnapshot: true, projection: null })
  })
})
