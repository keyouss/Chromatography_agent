import { describe, expect, it } from 'vitest'
import { firstTlcReverseRatioStep, isTlcReverseRangeValid, isTlcReverseRatioStepAllowed, mapTlcReverseRange, type TlcReverseRange } from '@/shared/tlc/types'

const validRange: TlcReverseRange = {
  target_rf: { min: 0, max: 1 },
  ratio_step: { min: 0, max: 1 },
  max_candidates: 200,
}

describe('TLC reverse capability validation', () => {
  it('accepts the full declared domain while keeping actual ratio steps positive', () => {
    expect(isTlcReverseRangeValid(validRange)).toBe(true)
    expect(firstTlcReverseRatioStep(validRange.ratio_step)).toBe(0.5)
    expect(isTlcReverseRatioStepAllowed(0, validRange.ratio_step)).toBe(false)
    expect(isTlcReverseRatioStepAllowed(0.5, validRange.ratio_step)).toBe(true)
    expect(isTlcReverseRatioStepAllowed(1, validRange.ratio_step)).toBe(true)
    expect(isTlcReverseRatioStepAllowed(1.01, validRange.ratio_step)).toBe(false)
  })

  it.each([
    ['Rf below domain', { ...validRange, target_rf: { min: -0.01, max: 0.5 } }],
    ['Rf above domain', { ...validRange, target_rf: { min: 0.5, max: 1.01 } }],
    ['reversed Rf bounds', { ...validRange, target_rf: { min: 0.8, max: 0.2 } }],
    ['negative ratio bound', { ...validRange, ratio_step: { min: -0.1, max: 0.5 } }],
    ['ratio bound above domain', { ...validRange, ratio_step: { min: 0.1, max: 1.01 } }],
    ['reversed ratio bounds', { ...validRange, ratio_step: { min: 0.8, max: 0.2 } }],
    ['zero-only ratio capability', { ...validRange, ratio_step: { min: 0, max: 0 } }],
    ['open singleton ratio capability', { ...validRange, ratio_step: { min: 0.5, max: 0.5, min_inclusive: false, max_inclusive: true } }],
    ['fractional candidate limit', { ...validRange, max_candidates: 1.5 }],
    ['candidate limit above contract', { ...validRange, max_candidates: 201 }],
  ] as const)('rejects invalid metadata: %s', (_label, range) => {
    expect(isTlcReverseRangeValid(range)).toBe(false)
  })

  it('keeps an inclusive singleton step and legal open-bound interior steps', () => {
    const singleton = { min: 0.5, max: 0.5 }
    expect(isTlcReverseRatioStepAllowed(0.5, singleton)).toBe(true)
    expect(firstTlcReverseRatioStep(singleton)).toBe(0.5)

    const openRange = { min: 0, max: 0.5, min_inclusive: false, max_inclusive: false }
    expect(isTlcReverseRangeValid({ ...validRange, ratio_step: openRange })).toBe(true)
    expect(firstTlcReverseRatioStep(openRange)).toBe(0.25)
    expect(isTlcReverseRatioStepAllowed(0, openRange)).toBe(false)
    expect(isTlcReverseRatioStepAllowed(0.5, openRange)).toBe(false)
  })

  it('marks malformed server capabilities unavailable for both shared entry points', () => {
    expect(mapTlcReverseRange(validRange)).toMatchObject({ available: true })
    expect(mapTlcReverseRange({ ...validRange, available: false, unavailable_reason: '模型暂不可用。' })).toMatchObject({
      available: false,
      unavailable_reason: '模型暂不可用。',
    })
    expect(mapTlcReverseRange({ ...validRange, max_candidates: 201 })).toMatchObject({
      available: false,
      unavailable_reason: '服务端返回的 TLC 反推能力范围无效。',
    })
    expect(mapTlcReverseRange(undefined)).toMatchObject({
      available: false,
      unavailable_reason: '服务端未返回 TLC 反推能力。',
    })
  })
})
