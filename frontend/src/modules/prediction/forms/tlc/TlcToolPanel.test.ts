import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import TlcToolPanel from './TlcToolPanel.vue'
import tlcToolPanelSource from '@/shared/tlc/TlcToolPanel.vue?raw'
import { firstTlcReverseRatioStep } from './types'

const solventOptions = [
  { id: 'hexane', label: 'Alpha' },
  { id: 'ethyl_acetate', label: 'Beta' },
  { id: 'methanol', label: 'Gamma' },
]

const reverseRange = {
  target_rf: { min: 0.1, max: 0.9 },
  ratio_step: { min: 0.05, max: 0.1, min_inclusive: true, max_inclusive: true },
  max_candidates: 12,
}

function panel(props: Record<string, unknown> = {}) {
  return mount(TlcToolPanel, {
    props: {
      solventOptions,
      candidateSystems: [{ id: 'system.alpha', label: 'Alpha / Beta', solvent_ids: ['hexane', 'ethyl_acetate'] }],
      reverseRange,
      structureRef: 'str_tlc_1',
      ...props,
    },
    global: { stubs: { TlcCurveExplorer: true } },
  })
}

describe('TlcToolPanel', () => {
  it('uses injected solvent labels and emits the generated TlcInput', async () => {
    const wrapper = panel()
    expect(wrapper.text()).toContain('Alpha')
    expect(wrapper.text()).toContain('Beta')
    expect(wrapper.text()).not.toContain('正己烷')

    await wrapper.get('button.primary').trigger('click')
    expect(wrapper.emitted('submit-single')?.[0]).toEqual([{
      solvent_components: [
        { solvent_id: 'hexane', fraction: 0.5 },
        { solvent_id: 'ethyl_acetate', fraction: 0.5 },
      ],
    }])
  })

  it('does not emit when component fractions are invalid', async () => {
    const wrapper = panel()
    const fractionInput = wrapper.findAll('input[type="text"]')[0]
    await fractionInput.setValue('0.2')
    await wrapper.get('button.primary').trigger('click')
    expect(wrapper.emitted('submit-single')).toBeUndefined()
    expect(wrapper.text()).toContain('比例之和必须为 1')
  })

  it('keeps the structure-ref requirement unless the parent marks raw input ready', async () => {
    const blocked = panel({ structureRef: null })
    expect(blocked.get('button.primary').attributes('disabled')).toBeDefined()
    expect(blocked.text()).toContain('请先在结构入口输入分子')

    const rawReady = panel({ structureRef: null, structureInputReady: true })
    expect(rawReady.text()).toContain('点击预测时自动校验')
    expect(rawReady.text()).not.toContain('str_tlc_1')
    expect(rawReady.get('button.primary').attributes('disabled')).toBeUndefined()
    await rawReady.get('button.primary').trigger('click')
    expect(rawReady.emitted('submit-single')).toHaveLength(1)
  })

  it('emits reverse fields using the injected candidate range', async () => {
    const wrapper = panel({ modelValue: 'reverse_solvent' })
    await wrapper.get('input[type="checkbox"]').setValue(true)
    const buttons = wrapper.findAll('button.primary')
    await buttons[0]!.trigger('click')
    expect(wrapper.emitted('submit-reverse')?.[0]).toEqual([{
      target_rf: { min: 0.1, max: 0.9 },
      candidate_system_ids: ['system.alpha'],
      ratio_step: 0.05,
      max_candidates: 12,
    }])
  })

  it('initializes the reverse step from capability and rejects zero', async () => {
    const capabilityRange = { min: 1, max: 10, min_inclusive: true, max_inclusive: true }
    expect(firstTlcReverseRatioStep(capabilityRange)).toBe(1)
    const wrapper = panel({ modelValue: 'reverse_solvent', reverseRange: { ...reverseRange, ratio_step: capabilityRange } })
    const ratioInput = wrapper.findAll('input[type="number"]')[2]!
    expect((ratioInput.element as HTMLInputElement).value).toBe('1')
    await wrapper.get('input[type="checkbox"]').setValue(true)
    await ratioInput.setValue('0')
    await wrapper.get('button.primary').trigger('click')
    expect(wrapper.emitted('submit-reverse')).toBeUndefined()
  })

  it('initializes a positive default when capability excludes zero', async () => {
    const capabilityRange = { min: 0, max: 1, min_inclusive: false, max_inclusive: true }
    expect(firstTlcReverseRatioStep(capabilityRange)).toBe(0.5)
    const wrapper = panel({ modelValue: 'reverse_solvent', reverseRange: { ...reverseRange, ratio_step: capabilityRange } })
    const ratioInput = wrapper.findAll('input[type="number"]')[2]!
    expect((ratioInput.element as HTMLInputElement).value).toBe('0.5')
    await wrapper.get('input[type="checkbox"]').setValue(true)
    await wrapper.get('button.primary').trigger('click')
    expect(wrapper.emitted('submit-reverse')?.[0]?.[0]).toMatchObject({ ratio_step: 0.5 })
  })


  it('accepts an internal ratio step inside the capability range', async () => {
    const wrapper = panel({ modelValue: 'reverse_solvent', reverseRange: { ...reverseRange, ratio_step: { min: 0.05, max: 0.1 } } })
    await wrapper.get('input[type="checkbox"]').setValue(true)
    await wrapper.findAll('input[type="number"]')[2]!.setValue('0.075')
    await wrapper.get('button.primary').trigger('click')
    expect(wrapper.emitted('submit-reverse')?.[0]?.[0]).toMatchObject({ ratio_step: 0.075 })
  })

  it('disables reverse submission when capability is missing', () => {
    const wrapper = panel({
      modelValue: 'reverse_solvent',
      reverseRange: { ...reverseRange, available: false, unavailable_reason: '服务端未返回 TLC 反推能力。' },
    })
    expect(wrapper.get('button.primary').attributes('disabled')).toBeDefined()
    expect(wrapper.text()).toContain('服务端未返回 TLC 反推能力')
  })

  it('keeps batch and comparison emits at their contract boundary', async () => {
    const batch = panel({ modelValue: 'batch', batchArtifactId: 'art_tlc_batch', batchTemplateVersion: 'tlc-batch-1' })
    await batch.get('button.primary').trigger('click')
    expect(batch.emitted('submit-batch')?.[0]).toEqual([{
      input_artifact_id: 'art_tlc_batch',
      common_input: { solvent_components: [
        { solvent_id: 'hexane', fraction: 0.5 },
        { solvent_id: 'ethyl_acetate', fraction: 0.5 },
      ] },
    }])

    const comparison = panel({ modelValue: 'condition_compare' })
    await comparison.get('button.secondary').trigger('click')
    await comparison.get('button.primary').trigger('click')
    expect(comparison.emitted('submit-comparison')?.[0]).toEqual([[{
      solvent_components: [
        { solvent_id: 'hexane', fraction: 0.5 },
        { solvent_id: 'ethyl_acetate', fraction: 0.5 },
      ],
    }]])
    expect(comparison.text()).not.toContain('比较结果')
    expect(comparison.find('.result-card').exists()).toBe(false)
  })

  it('hides the internal tool navigation when the parent owns navigation', () => {
    expect(panel({ modelValue: 'condition_compare', hideNavigation: true }).find('nav.tool-tabs').exists()).toBe(false)
    expect(panel({ modelValue: 'condition_compare' }).find('nav.tool-tabs').exists()).toBe(true)
  })

  it('allows solvent rows to shrink and stack at narrow widths', () => {
    expect(tlcToolPanelSource).toContain('min-width:0')
    expect(tlcToolPanelSource).toContain('box-sizing:border-box;width:100%')
    expect(tlcToolPanelSource).toContain('@media(max-width:520px){.component-row{grid-template-columns:minmax(0,1fr) minmax(0,1fr)}')
  })

  it('shows empty and partial-failure curve states without inventing points', async () => {
    const empty = panel({ modelValue: 'reverse_solvent' })
    expect(empty.text()).toContain('暂无有效曲线点')

    const partial = panel({
      modelValue: 'reverse_solvent',
      points: [
        { x: 0, ratio: 0, rf: 0.2 },
        { x: 1, ratio: 1, rf: null },
      ],
    })
    expect(partial.text()).toContain('扫描点失败')

    const unreachable = panel({
      modelValue: 'reverse_solvent',
      reverseRange: { ...reverseRange, target_rf: { min: 0.8, max: 0.9 } },
      points: [
        { x: 0, ratio: 0, rf: 0.2 },
        { x: 1, ratio: 1, rf: 0.3 },
      ],
    })
    expect(unreachable.text()).toContain('目标 Rf 不在当前有效曲线可达范围内')
  })
})
