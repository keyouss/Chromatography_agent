export interface AssistantTextDelta {
  message_id: string
  block_index: number
  delta: string
  offset: number
}

export interface AssistantTextProjection {
  message_id: string
  block_index: number
  text: string
  codePointLength: number
  revision: number
}

export interface AssistantTextDeltaResult {
  accepted: boolean
  needsSnapshot: boolean
  projection: AssistantTextProjection | null
}

export function codePointLength(value: string): number {
  return Array.from(value).length
}

export function assistantTextProjectionFromSnapshot(messageId: string, blockIndex: number, text: string, revision = 0): AssistantTextProjection {
  return { message_id: messageId, block_index: blockIndex, text, codePointLength: codePointLength(text), revision }
}

export function applyAssistantTextDelta(
  current: AssistantTextProjection | null,
  input: AssistantTextDelta,
): AssistantTextDeltaResult {
  const invalid = !input || typeof input.message_id !== 'string' || !input.message_id ||
    !Number.isSafeInteger(input.block_index) || input.block_index < 0 ||
    typeof input.delta !== 'string' || !Number.isSafeInteger(input.offset) || input.offset < 0
  if (invalid) return { accepted: false, needsSnapshot: true, projection: current }
  if (current && (current.message_id !== input.message_id || current.block_index !== input.block_index)) {
    return { accepted: false, needsSnapshot: true, projection: current }
  }

  const previous = current ? Array.from(current.text) : []
  const incoming = Array.from(input.delta)
  if (!current && input.offset !== 0) return { accepted: false, needsSnapshot: true, projection: null }
  if (input.offset > previous.length) return { accepted: false, needsSnapshot: true, projection: current }

  const overlap = Math.min(previous.length - input.offset, incoming.length)
  for (let index = 0; index < overlap; index += 1) {
    if (previous[input.offset + index] !== incoming[index]) return { accepted: false, needsSnapshot: true, projection: current }
  }
  if (overlap === incoming.length) return { accepted: false, needsSnapshot: false, projection: current }

  const appended = incoming.slice(overlap).join('')
  const text = (current?.text ?? '') + appended
  const projection: AssistantTextProjection = {
    message_id: input.message_id,
    block_index: input.block_index,
    text,
    codePointLength: previous.length + incoming.length - overlap,
    revision: (current?.revision ?? 0) + 1,
  }
  return { accepted: true, needsSnapshot: false, projection }
}
