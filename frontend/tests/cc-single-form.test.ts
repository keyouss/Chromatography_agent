import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import CcSingleForm from '@/modules/prediction/forms/basic/CcSingleForm.vue'
import { ccFormCapabilityFromSchema, ccFormValueError, defaultCcFormValue } from '@/modules/prediction/forms/basic/form-types'

const schema = {
  properties: {
    input: {
      properties: {
        column_spec_id: { type: 'string', enum: ['silica-4g'] },
        eluent_system_id: { type: 'string', enum: ['cc4g-pe-ea-000-100-v1'] },
        loading_solvent_id: { type: 'string', enum: ['cc4g-loading-dcm-v1'] },
        sample_mass_mg: { type: 'number', minimum: 30, maximum: 327.3, unit: 'mg' },
        loading_solvent_volume_ul: { type: 'number', minimum: 200, maximum: 2000, unit: 'uL' },
        flow_rate_ml_min: { type: 'number', minimum: 5, maximum: 10, unit: 'mL/min' },
      },
    },
  },
}

const constraints = {
  column_spec_ids: ['silica-4g'],
  eluent_system_ids: ['cc4g-pe-ea-000-100-v1'],
  loading_solvent_ids: ['cc4g-loading-dcm-v1'],
  numeric_input_ranges: {
    sample_mass_mg: { minimum: 30, maximum: 327.3, unit: 'mg' },
    loading_solvent_volume_ul: { minimum: 200, maximum: 2000, unit: 'uL' },
    flow_rate_ml_min: { minimum: 5, maximum: 10, unit: 'mL/min' },
  },
}

describe('CC capability-backed single form', () => {
  it('reads nested schema candidates and compares candidate sets independent of declared order', () => {
    const expandedSchema = structuredClone(schema)
    expandedSchema.properties.input.properties.column_spec_id.enum = ['silica-4g', 'silica-8g']
    const reversedConstraints = {
      ...constraints,
      column_spec_ids: ['silica-8g', 'silica-4g'],
    }
    const capability = ccFormCapabilityFromSchema(expandedSchema, reversedConstraints)
    expect(capability?.columns.map((item) => item.value)).toEqual(['silica-4g', 'silica-8g'])
  })

  it('preserves nested schema candidates and numeric bounds from matching constraints', () => {
    const reversedConstraints = {
      ...constraints,
      column_spec_ids: ['silica-4g'],
      eluent_system_ids: ['cc4g-pe-ea-000-100-v1'],
      loading_solvent_ids: ['cc4g-loading-dcm-v1'],
    }
    const capability = ccFormCapabilityFromSchema(schema, reversedConstraints)
    expect(capability?.columns).toEqual([{ value: 'silica-4g', label: 'silica-4g' }])
    expect(capability?.ranges).toEqual({
      sample_mass_mg: { minimum: 30, maximum: 327.3, unit: 'mg' },
      loading_solvent_volume_ul: { minimum: 200, maximum: 2000, unit: 'uL' },
      flow_rate_ml_min: { minimum: 5, maximum: 10, unit: 'mL/min' },
    })
    expect(defaultCcFormValue(capability)).toEqual({
      column_spec_id: 'silica-4g', eluent_system_id: 'cc4g-pe-ea-000-100-v1', loading_solvent_id: 'cc4g-loading-dcm-v1',
      sample_mass_mg: 30, loading_solvent_volume_ul: 200, flow_rate_ml_min: 5,
    })
  })

  it('fails closed for missing nested schema, mismatched candidates, ranges, or units', () => {
    expect(ccFormCapabilityFromSchema({ properties: { column_spec_id: { enum: ['silica-4g'] } } }, constraints)).toBeNull()
    expect(ccFormCapabilityFromSchema(schema, { ...constraints, column_spec_ids: ['other-column'] })).toBeNull()
    expect(ccFormCapabilityFromSchema(schema, {
      ...constraints,
      numeric_input_ranges: { ...constraints.numeric_input_ranges, sample_mass_mg: { minimum: 31, maximum: 327.3, unit: 'mg' } },
    })).toBeNull()
    const wrongUnitSchema = structuredClone(schema)
    wrongUnitSchema.properties.input.properties.sample_mass_mg.unit = 'g'
    expect(ccFormCapabilityFromSchema(wrongUnitSchema, constraints)).toBeNull()
    const sameWrongUnits = {
      ...constraints,
      numeric_input_ranges: { ...constraints.numeric_input_ranges, sample_mass_mg: { minimum: 30, maximum: 327.3, unit: 'g' } },
    }
    expect(ccFormCapabilityFromSchema(wrongUnitSchema, sameWrongUnits)).toBeNull()
  })

  it('keeps an out-of-range replay value unchanged, reports it, and disables submit until corrected', async () => {
    const capability = ccFormCapabilityFromSchema(schema, constraints)!
    const original = {
      column_spec_id: 'silica-4g', eluent_system_id: 'cc4g-pe-ea-000-100-v1', loading_solvent_id: 'cc4g-loading-dcm-v1',
      sample_mass_mg: 10, loading_solvent_volume_ul: 100, flow_rate_ml_min: 10,
    }
    expect(ccFormValueError(original, capability)).toContain('sample_mass_mg')
    const wrapper = mount(CcSingleForm, { props: { capability, initialValue: original } })
    expect((wrapper.get('[data-testid="cc-sample-mass"]').element as HTMLInputElement).value).toBe('10')
    expect((wrapper.get('[data-testid="cc-loading-volume"]').element as HTMLInputElement).value).toBe('100')
    expect(wrapper.get('[data-testid="cc-sample-mass"]').attributes('min')).toBe('30')
    expect(wrapper.get('[data-testid="cc-loading-volume"]').attributes('max')).toBe('2000')
    expect(wrapper.get('[data-testid="cc-input-validation-error"]').text()).toContain('sample_mass_mg')
    expect((wrapper.get('[data-testid="cc-submit"]').element as HTMLButtonElement).disabled).toBe(true)

    await wrapper.get('[data-testid="cc-sample-mass"]').setValue(100)
    await wrapper.get('[data-testid="cc-loading-volume"]').setValue(300)
    expect(wrapper.find('[data-testid="cc-input-validation-error"]').exists()).toBe(false)
    expect((wrapper.get('[data-testid="cc-submit"]').element as HTMLButtonElement).disabled).toBe(false)
  })

  it('blocks a candidate that has expired from the current service capability', () => {
    const capability = ccFormCapabilityFromSchema(schema, constraints)!
    const error = ccFormValueError({ ...defaultCcFormValue(capability), column_spec_id: 'retired-column' }, capability)
    expect(error).toContain('柱规格不在当前服务端能力范围内')
  })

  it('exposes a typed editable value and emits update:value for workspace draft storage', async () => {
    const capability = ccFormCapabilityFromSchema(schema, constraints)!
    const wrapper = mount(CcSingleForm, { props: { capability } })
    await wrapper.get('[data-testid="cc-sample-mass"]').setValue(100)
    const value = wrapper.vm.$.exposed?.getValue()
    expect(value).toMatchObject({ sample_mass_mg: 100, column_spec_id: 'silica-4g' })
    expect(wrapper.emitted('update:value')?.at(-1)?.[0]).toMatchObject(value)
  })

  it('keeps disabled gates separate from an active submission caption', () => {
    const capability = ccFormCapabilityFromSchema(schema, constraints)!
    const disabled = mount(CcSingleForm, { props: { capability, disabled: true } })
    expect((disabled.get('[data-testid="cc-submit"]').element as HTMLButtonElement).disabled).toBe(true)
    expect(disabled.get('[data-testid="cc-submit"]').text()).toBe('开始 CC 预测')

    const submitting = mount(CcSingleForm, { props: { capability, busy: true } })
    expect((submitting.get('[data-testid="cc-submit"]').element as HTMLButtonElement).disabled).toBe(true)
    expect(submitting.get('[data-testid="cc-submit"]').text()).toBe('提交中…')
  })
})
