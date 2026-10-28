import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import GcSingleForm from '@/modules/prediction/forms/basic/GcSingleForm.vue'
import { gcTemperatureSeriesPreset, parseGcTemperatureSeries } from '@/modules/prediction/forms/basic/gc-temperature-program'

const capability = {
  initial_temperature_c: { minimum: 30, maximum: 300 },
  final_temperature_c: { minimum: 30, maximum: 400 },
  heating_rate_c_per_min: { exclusiveMinimum: 0, maximum: 100 },
  initial_hold_min: { minimum: 0, maximum: 100, multipleOf: 1 },
  series: { minItems: 2, maxItems: 30, temperaturePoints: 30 },
}

describe('GC temperature program input', () => {
  it('rejects empty, missing and non-finite series entries instead of dropping them', () => {
    expect(parseGcTemperatureSeries('  ')).toMatchObject({ values: [], error: '请输入温度序列。' })
    expect(parseGcTemperatureSeries('50,,75').error).toContain('第 2 项为空')
    expect(parseGcTemperatureSeries('50, ,75').error).toContain('第 2 项为空')
    expect(parseGcTemperatureSeries('50\n\n75').error).toContain('为空')
    expect(parseGcTemperatureSeries('50, nope, 75').error).toContain('第 2 项不是有限数值')
    expect(parseGcTemperatureSeries('50, Infinity').error).toContain('不是有限数值')
    expect(parseGcTemperatureSeries('50, NaN').error).toContain('不是有限数值')
    expect(parseGcTemperatureSeries('50, -Infinity').error).toContain('不是有限数值')
  })

  it('parses all finite entries without imposing a global model series length', () => {
    expect(parseGcTemperatureSeries('50 75; 100,125')).toEqual({ values: [50, 75, 100, 125], error: null })
    expect(parseGcTemperatureSeries('50\r\n75')).toEqual({ values: [50, 75], error: null })
  })

  it('creates capability-length presets with distinct ramp shapes and exposes editable draft text', async () => {
    for (const preset of ['fast', 'medium', 'slow'] as const) {
      const values = gcTemperatureSeriesPreset(preset, 30)!
      expect(values).toHaveLength(30)
      expect(values[0]).toBe(40)
      expect(values.at(-1)).toBe(200)
    }
    expect(gcTemperatureSeriesPreset('fast', 7)).toHaveLength(7)
    expect(gcTemperatureSeriesPreset('slow', 2)).toBeNull()

    const wrapper = mount(GcSingleForm, { props: { capability, initialValue: { temperature_program: { mode: 'series', temperature_series_c: [40, 200] }, seriesText: '40; 200' } } })
    expect(wrapper.get('[data-testid="gc-series-input"]').element).toHaveProperty('value', '40; 200')
    expect(wrapper.vm.$.exposed?.getValue()).toEqual({ temperature_program: { mode: 'series', temperature_series_c: [40, 200] }, seriesText: '40; 200' })
    await wrapper.get('[data-testid="gc-series-input"]').setValue('41, 199')
    expect(wrapper.emitted('update:value')?.at(-1)?.[0]).toEqual({ temperature_program: { mode: 'series', temperature_series_c: [41, 199] }, seriesText: '41, 199' })

    const presetForm = mount(GcSingleForm, { props: { capability } })
    await presetForm.get('.segmented button:nth-child(2)').trigger('click')
    await presetForm.get('[data-testid="gc-preset-fast"]').trigger('click')
    expect(parseGcTemperatureSeries((presetForm.get('[data-testid="gc-series-input"]').element as HTMLTextAreaElement).value).values).toHaveLength(30)
    expect(presetForm.emitted('submit')).toBeUndefined()
  })

  it('accepts equal initial and final temperatures but rejects invalid linear numbers', async () => {
    const wrapper = mount(GcSingleForm, { props: { capability } })
    await wrapper.get('input').setValue('50')
    await wrapper.findAll('input')[1]!.setValue('50')
    expect((wrapper.get('[data-testid="gc-submit"]').element as HTMLButtonElement).disabled).toBe(false)

    await wrapper.findAll('input')[1]!.setValue('49')
    expect((wrapper.get('[data-testid="gc-submit"]').element as HTMLButtonElement).disabled).toBe(true)
    expect(wrapper.get('[data-testid="gc-linear-error"]').text()).toContain('终温须不低于初温')

    await wrapper.findAll('input')[1]!.setValue('50')
    await wrapper.findAll('input')[2]!.setValue('0')
    expect((wrapper.get('[data-testid="gc-submit"]').element as HTMLButtonElement).disabled).toBe(true)
    await wrapper.findAll('input')[2]!.setValue('10')
    await wrapper.findAll('input')[3]!.setValue('-1')
    expect((wrapper.get('[data-testid="gc-submit"]').element as HTMLButtonElement).disabled).toBe(true)
    await wrapper.findAll('input')[3]!.setValue('')
    expect((wrapper.get('[data-testid="gc-submit"]').element as HTMLButtonElement).disabled).toBe(true)
    expect(wrapper.emitted('submit')).toBeUndefined()
  })

  it('keeps disabled gates separate from an active submission caption', async () => {
    const disabled = mount(GcSingleForm, { props: { capability, disabled: true } })
    expect((disabled.get('[data-testid="gc-submit"]').element as HTMLButtonElement).disabled).toBe(true)
    expect(disabled.get('[data-testid="gc-submit"]').text()).toBe('开始 GC 预测')
    await disabled.get('[data-testid="gc-submit"]').trigger('click')
    expect(disabled.emitted('submit')).toBeUndefined()

    const submitting = mount(GcSingleForm, { props: { capability, busy: true } })
    expect((submitting.get('[data-testid="gc-submit"]').element as HTMLButtonElement).disabled).toBe(true)
    expect(submitting.get('[data-testid="gc-submit"]').text()).toBe('提交中…')
  })

  it('requires a selected-model schema and never supplies or clamps a series length', async () => {
    const missing = mount(GcSingleForm)
    expect((missing.get('[data-testid="gc-submit"]').element as HTMLButtonElement).disabled).toBe(true)
    expect(missing.find('[data-testid="gc-capability-missing"]').exists()).toBe(true)

    const wrapper = mount(GcSingleForm, { props: { capability } })
    await wrapper.get('button:nth-child(2)').trigger('click')
    expect(wrapper.get('[data-testid="gc-series-input"]').element).toHaveProperty('value', '')
    await wrapper.get('[data-testid="gc-series-input"]').setValue(Array.from({ length: 31 }, (_, index) => String(50 + index)).join(', '))
    expect(wrapper.get('[data-testid="gc-series-error"]').text()).toContain('30')
    expect((wrapper.get('[data-testid="gc-submit"]').element as HTMLButtonElement).disabled).toBe(true)
    await wrapper.get('[data-testid="gc-series-input"]').setValue('50, 75')
    expect((wrapper.get('[data-testid="gc-submit"]').element as HTMLButtonElement).disabled).toBe(false)
    expect(wrapper.emitted('submit')).toBeUndefined()
  })
})
