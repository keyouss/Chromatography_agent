import { defineComponent, h } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import type { Structure } from '@/core/types'
import SfcSearchForm from '@/modules/prediction/forms/sfc/SfcSearchForm.vue'
import { SFC_CANDIDATE_KEYS } from '@/modules/prediction/forms/sfc/types'
import type { SfcSearchBindingOptions, SfcSearchModelChoice, SfcSearchToolChoice } from '@/modules/prediction/forms/sfc/SfcSearchForm.types'

let normalizedId = 0
const StructureInputStub = defineComponent({
  name: 'StructureInput',
  props: { projectId: { type: String, required: true }, disabled: { type: Boolean, default: false } },
  emits: ['normalized', 'invalidated'],
  setup(props, { emit }) {
    const id = `structure-${++normalizedId}`
    return () => h('button', {
      type: 'button', 'data-testid': `normalize-${id}`, disabled: props.disabled,
      onClick: () => emit('normalized', { id, project_id: props.projectId, canonical_smiles: 'CC', stereochemistry_status: 'not_applicable' } as Structure),
    }, 'Normalize test structure')
  },
})

const projectId = 'priority-local'
const singleTool: SfcSearchToolChoice = { id: 'tool-single', label: '单柱搜索', health: 'READY', requiresColumn: true }
const fullTool: SfcSearchToolChoice = { id: 'tool-full', label: '全柱搜索', health: 'READY', requiresColumn: false }
const modelA: SfcSearchModelChoice = {
  id: 'model-a', version: 'version-a', label: 'Model A', health: 'READY', business_scope_id: 'business-a', threshold: 1.2, model_scope: 'single_column',
  group_id: 'group-a', group_label: 'Group A', stationary_phase: 'phase-a', mode: 'coated', allowed_columns: ['column-a'],
}
const modelB: SfcSearchModelChoice = {
  id: 'model-b', version: 'version-b', label: 'Model B', health: 'READY', business_scope_id: 'business-b', threshold: 1.5, model_scope: 'single_column',
  group_id: 'group-b', group_label: 'Group B', stationary_phase: 'phase-b', mode: 'coated', allowed_columns: ['column-b'],
}
const axes = SFC_CANDIDATE_KEYS.map((key) => ({ key, label: `Server ${key}`, unit: key === 'flow_rate_ml_min' ? 'mL/min' : null, values: key === 'column' ? ['column-a', 'column-b'] : [`${key}-observed-value`] }))

function optionsFor(model: SfcSearchModelChoice, toolId = singleTool.id): SfcSearchBindingOptions {
  return {
    projectId, toolId, modelId: model.id, modelVersion: model.version, business_scope_id: model.business_scope_id, model_scope: model.model_scope!, group_id: model.model_scope === 'single_column' ? model.group_id ?? null : null, threshold: model.threshold!,
    scopeId: 'options-only-scope-reference', axes,
    limits: {
      candidateLimit: 17, topKMinimum: 1, topKMaximum: 200, modifierMinimum: 1, modifierMaximum: 20,
      componentFractionMinimum: 0, componentFractionMaximum: 1, totalFractionMinimum: 0, totalFractionMaximum: 1,
    },
  }
}

function mountForm(overrides: Record<string, unknown> = {}) {
  return mount(SfcSearchForm, {
    props: {
      projectId, tools: [singleTool, fullTool], selectedToolId: singleTool.id,
      models: [modelA, modelB], selectedModel: modelA, optionsState: 'ready', options: optionsFor(modelA),
      ...overrides,
    },
    global: { stubs: { StructureInput: StructureInputStub } },
  })
}

async function chooseAxis(wrapper: ReturnType<typeof mount>, key: string, indexes: string[]) {
  const select = wrapper.get(`[data-testid="axis-${key}"]`)
  if ((select.element as HTMLSelectElement).disabled) return
  for (const index of indexes) {
    await select.setValue(index)
    await wrapper.get(`[data-testid="axis-add-${key}"]`).trigger('click')
  }
}

async function fillSearchFields(wrapper: ReturnType<typeof mount>, fractions: number[]) {
  for (const key of SFC_CANDIDATE_KEYS) await chooseAxis(wrapper, key, ['0'])
  const columnSelect = wrapper.find('[data-testid="sfc-column-select"]')
  if (columnSelect.exists()) await columnSelect.setValue('column-a')
  for (let index = 0; index < fractions.length; index += 1) {
    await wrapper.get('[data-testid="add-modifier"]').trigger('click')
  }
  const normalizers = wrapper.findAll('[data-testid^="normalize-structure-"]')
  for (const normalizer of normalizers) await normalizer.trigger('click')
  for (let index = 0; index < fractions.length; index += 1) {
    await wrapper.get(`[data-testid="modifier-fraction-${index + 1}"]`).setValue(String(fractions[index]))
  }
  await wrapper.get('[data-testid="modifier-total-fraction"]').setValue('0.25')
  await wrapper.get('[data-testid="sfc-top-k"]').setValue('100')
  await flushPromises()
}

describe('SFC search form presentation boundary', () => {
  it('previews typed multi-axis chips without requiring normalized primary structures and clears stale preview on edits', async () => {
    const allModel: SfcSearchModelChoice = { ...modelA, model_scope: 'all_column', allowed_columns: null }
    const typedOptions = optionsFor(allModel, fullTool.id)
    typedOptions.axes = typedOptions.axes.map((axis) => axis.key === 'column_temperature_c' ? { ...axis, values: [0, 35] } : axis)
    const wrapper = mountForm({ selectedToolId: fullTool.id, selectedModel: allModel, models: [allModel], options: typedOptions, structuresReady: false })
    for (const key of SFC_CANDIDATE_KEYS) await chooseAxis(wrapper, key, ['0'])
    await chooseAxis(wrapper, 'column', ['1'])
    await chooseAxis(wrapper, 'column', ['1'])
    expect(wrapper.findAll('[data-testid="axis-chips-column"] li')).toHaveLength(2)
    expect((wrapper.get('[data-testid="sfc-preview-button"]').element as HTMLButtonElement).disabled).toBe(false)
    await wrapper.get('[data-testid="sfc-preview-button"]').trigger('click')
    const values = wrapper.emitted('preview')?.[0]?.[0] as Record<string, unknown[]>
    expect(values.column).toEqual(['column-a', 'column-b'])
    expect(values.column_temperature_c).toEqual([0])
    expect(values).not.toHaveProperty('threshold')
    await wrapper.get('[data-testid="axis-remove-column-1"]').trigger('click')
    expect(wrapper.findAll('[data-testid="axis-chips-column"] li')).toHaveLength(1)
    expect(wrapper.emitted('axes-change')?.length).toBeGreaterThan(0)
    wrapper.unmount()
  })

  it('offers coupled discrete expansion only when supported and carries the selected mode through preview and submit', async () => {
    const allColumnModel: SfcSearchModelChoice = {
      ...modelA, id: 'model-all', version: 'version-all', label: 'All columns', business_scope_id: 'business-all',
      model_scope: 'all_column', group_id: null, group_label: null, stationary_phase: null, mode: null, allowed_columns: null,
    }
    const coupledOptions = optionsFor(allColumnModel, fullTool.id)
    coupledOptions.supportedExpansionModes = ['observed_distinct_rows', 'coupled_discrete_conditions']
    coupledOptions.axes = coupledOptions.axes.map((axis) => axis.key === 'column_temperature_c'
      ? { ...axis, values: [0, 35] }
      : axis.key === 'flow_rate_ml_min' ? { ...axis, values: [1, 2] }
        : axis.key === 'length_mm' ? { ...axis, values: [100, 150] }
          : axis.key === 'particle_size_um' ? { ...axis, values: [2, 3] } : axis)
    const wrapper = mountForm({
      selectedToolId: fullTool.id, selectedModel: allColumnModel, models: [allColumnModel], options: coupledOptions,
    })
    await fillSearchFields(wrapper, [1])
    await chooseAxis(wrapper, 'column', ['1'])
    await chooseAxis(wrapper, 'column_temperature_c', ['1'])
    await chooseAxis(wrapper, 'flow_rate_ml_min', ['1'])
    await chooseAxis(wrapper, 'length_mm', ['1'])
    await chooseAxis(wrapper, 'particle_size_um', ['1'])
    await wrapper.get('[data-testid="sfc-expansion-mode"]').setValue('coupled_discrete_conditions')

    expect(wrapper.findAll('[data-testid="axis-chips-column"] li')).toHaveLength(2)
    expect(wrapper.findAll('[data-testid="axis-chips-column_temperature_c"] li')).toHaveLength(2)
    expect(wrapper.findAll('[data-testid="axis-chips-flow_rate_ml_min"] li')).toHaveLength(2)
    expect(wrapper.findAll('[data-testid="axis-chips-length_mm"] li')).toHaveLength(2)
    expect(wrapper.findAll('[data-testid="axis-chips-particle_size_um"] li')).toHaveLength(2)
    expect(wrapper.emitted('axes-change')?.length).toBeGreaterThan(0)
    expect((wrapper.get('[data-testid="sfc-preview-button"]').element as HTMLButtonElement).disabled).toBe(false)
    await wrapper.get('[data-testid="sfc-preview-button"]').trigger('click')
    const preview = wrapper.emitted('preview')?.[0]
    expect(preview?.[1]).toBe('coupled_discrete_conditions')
    expect((preview?.[0] as Record<string, unknown[]>).column_temperature_c).toEqual([0, 35])
    expect((preview?.[0] as Record<string, unknown[]>).column).toEqual(['column-a', 'column-b'])
    expect((preview?.[0] as Record<string, unknown[]>).length_mm).toEqual([100, 150])

    expect((wrapper.get('[data-testid="sfc-search-submit"]').element as HTMLButtonElement).disabled).toBe(false)
    await wrapper.get('[data-testid="sfc-search-form"]').trigger('submit')
    const submitted = wrapper.emitted('submit')?.[0]?.[0] as Record<string, unknown>
    expect(submitted.expansionMode).toBe('coupled_discrete_conditions')

    const nextModel: SfcSearchModelChoice = {
      ...allColumnModel, id: 'model-all-next', version: 'version-all-next', business_scope_id: 'business-all-next',
    }
    const nextModelOptions = optionsFor(nextModel, fullTool.id)
    nextModelOptions.supportedExpansionModes = coupledOptions.supportedExpansionModes
    await wrapper.setProps({ selectedModel: nextModel, models: [allColumnModel, nextModel], options: nextModelOptions })
    await flushPromises()
    expect((wrapper.get('[data-testid="sfc-expansion-mode"]').element as HTMLSelectElement).value).toBe('observed_distinct_rows')

    const unsupported = mountForm()
    expect(unsupported.find('[data-testid="sfc-expansion-mode"]').exists()).toBe(false)
    unsupported.unmount()
    wrapper.unmount()
  })

  it('retains all draft fields for equivalent model clones and unrelated model additions but invalidates changed schema', async () => {
    const wrapper = mountForm()
    await fillSearchFields(wrapper, [1])
    const newModel = { ...modelB, id: 'unrelated' }
    await wrapper.setProps({ models: [{ ...modelA }, { ...modelB }, newModel], selectedModel: { ...modelA } })
    expect(wrapper.get('[data-testid="axis-chips-column"]').text()).toContain('column-a')
    expect((wrapper.get('[data-testid="sfc-top-k"]').element as HTMLInputElement).value).toBe('100')
    expect((wrapper.get('[data-testid="modifier-total-fraction"]').element as HTMLInputElement).value).toBe('0.25')
    expect(wrapper.findAll('[data-testid^="modifier-row-"]')).toHaveLength(1)
    await wrapper.setProps({ selectedModel: { ...modelA, inputSchemaVersion: '1.0', newRequestReady: false } })
    expect((wrapper.get('[data-testid="sfc-search-submit"]').element as HTMLButtonElement).disabled).toBe(true)
    wrapper.unmount()
  })

  it('uses parent model metadata for Rs threshold/group and only allowed columns, keeping group separate', async () => {
    const wrapper = mountForm()
    expect(wrapper.get('[data-testid="sfc-rs-threshold"]').text()).toContain('Rs 分离度目标阈值：1.2')
    expect(wrapper.get('[data-testid="sfc-rs-threshold"]').text()).toContain('P(Rs≥该阈值)')
    expect(wrapper.get('[data-testid="sfc-model-group"]').text()).toContain('group-a')
    expect(wrapper.findAll('select[data-testid^="axis-"]')).toHaveLength(8)
    expect(wrapper.get('.limit-pill').text()).toContain('17')
    expect(wrapper.text()).not.toContain('候选组合数：')

    await fillSearchFields(wrapper, [1])
    expect((wrapper.get('[data-testid="sfc-search-submit"]').element as HTMLButtonElement).disabled).toBe(false)
    await wrapper.get('[data-testid="sfc-search-form"]').trigger('submit')

    const emitted = wrapper.emitted('submit')?.[0]?.[0] as Record<string, unknown>
    expect(emitted.modelId).toBe(modelA.id)
    expect(emitted.modelVersion).toBe(modelA.version)
    expect(emitted.group_id).toBe('group-a')
    expect(emitted.column_id).toBe('column-a')
    expect(emitted.group_id).not.toBe(emitted.column_id)
    expect(emitted.topK).toBe(100)
    expect(emitted).not.toHaveProperty('scopeId')
    expect(Object.keys(emitted.candidateAxes as object)).toHaveLength(8)
    expect((emitted.modifiers as Array<{ fraction: number }>)[0]?.fraction).toBe(1)
    expect(emitted.modifierTotalFraction).toBe(0.25)
    expect((emitted.candidateAxes as Record<string, unknown[]>).column).toEqual(['column-a'])
    wrapper.unmount()
  })

  it('shows unhealthy model state and cannot submit without a READY exact model/options binding', () => {
    const unavailable: SfcSearchModelChoice = { ...modelA, id: 'unverified', health: 'UNAVAILABLE', unavailableReason: 'MODEL_VERIFICATION_REQUIRED' }
    const wrapper = mountForm({ models: [unavailable], selectedModel: unavailable, options: optionsFor(modelA) })
    expect(wrapper.get('[data-testid="sfc-model-unavailable"]').text()).toContain('MODEL_VERIFICATION_REQUIRED')
    expect((wrapper.get('[data-testid="sfc-search-submit"]').element as HTMLButtonElement).disabled).toBe(true)
    expect(wrapper.find('.axes-section').exists()).toBe(false)
    wrapper.unmount()
  })

  it('disables unhealthy model options and restores them without changing the selected model', async () => {
    const unavailableModelB: SfcSearchModelChoice = {
      ...modelB, health: 'UNAVAILABLE', unavailableReason: 'MODEL_VERIFICATION_REQUIRED',
    }
    const wrapper = mountForm({ models: [modelA, unavailableModelB], selectedModel: modelA })
    const modelSelect = wrapper.get('[data-testid="sfc-model-select"]').element as HTMLSelectElement
    const optionFor = (id: string, version: string) => Array.from(modelSelect.options)
      .find((option) => option.value === JSON.stringify([id, version]))!

    expect(optionFor(modelA.id, modelA.version).disabled).toBe(false)
    expect(optionFor(modelB.id, modelB.version).disabled).toBe(true)
    expect(optionFor(modelB.id, modelB.version).textContent).toContain('MODEL_VERIFICATION_REQUIRED')

    const readyModelB: SfcSearchModelChoice = { ...unavailableModelB, health: 'READY', unavailableReason: null }
    await wrapper.setProps({ models: [modelA, readyModelB] })

    expect(optionFor(modelB.id, modelB.version).disabled).toBe(false)
    expect(modelSelect.value).toBe(JSON.stringify([modelA.id, modelA.version]))
    wrapper.unmount()
  })

  it('clears old selections on model change and stays disabled until new matching options arrive', async () => {
    const wrapper = mountForm()
    for (const key of SFC_CANDIDATE_KEYS) await chooseAxis(wrapper, key, ['0'])
    await wrapper.get('[data-testid="sfc-column-select"]').setValue('column-a')
    await wrapper.get('[data-testid="sfc-model-select"]').setValue(JSON.stringify([modelB.id, modelB.version]))
    expect(wrapper.emitted('model-change')?.[0]?.[0]).toEqual({ modelId: modelB.id, modelVersion: modelB.version })
    expect((wrapper.get('[data-testid="sfc-search-submit"]').element as HTMLButtonElement).disabled).toBe(true)

    await wrapper.setProps({ selectedModel: modelB })
    expect(wrapper.find('.axes-section').exists()).toBe(false)
    await wrapper.setProps({ options: optionsFor(modelB) })
    expect(wrapper.find('.axes-section').exists()).toBe(true)
    expect(wrapper.find('[data-testid="axis-chips-column"]').exists()).toBe(false)
    expect((wrapper.get('[data-testid="sfc-column-select"]').element as HTMLSelectElement).value).toBe('')
    expect((wrapper.get('[data-testid="sfc-search-submit"]').element as HTMLButtonElement).disabled).toBe(true)
    wrapper.unmount()
  })

  it('requires explicit normalization, emits normalized component fractions, and keeps total fraction separate', async () => {
    const wrapper = mountForm()
    await fillSearchFields(wrapper, [0.2, 0.3])
    expect((wrapper.get('[data-testid="sfc-search-submit"]').element as HTMLButtonElement).disabled).toBe(true)
    await wrapper.get('[data-testid="normalize-fractions"]').setValue(true)
    expect(wrapper.get('[data-testid="normalization-preview"]').text()).toContain('0.4 + 0.6')
    expect(wrapper.get('[data-testid="normalized-fraction-1"]').text()).toContain('0.4')
    expect(wrapper.get('[data-testid="normalized-fraction-2"]').text()).toContain('0.6')
    expect((wrapper.get('[data-testid="sfc-search-submit"]').element as HTMLButtonElement).disabled).toBe(false)
    await wrapper.get('[data-testid="sfc-search-form"]').trigger('submit')

    const emitted = wrapper.emitted('submit')?.[0]?.[0] as { modifiers: Array<{ fraction: number }>; modifierTotalFraction: number; fractionsWereNormalized: boolean }
    expect(emitted.modifiers.map((modifier) => modifier.fraction)).toEqual([0.4, 0.6])
    expect(emitted.modifiers.reduce((sum, modifier) => sum + modifier.fraction, 0)).toBe(1)
    expect(emitted.modifierTotalFraction).toBe(0.25)
    expect(emitted.fractionsWereNormalized).toBe(true)
    wrapper.unmount()
  })

  it('does not enable form controls while options are loading or belong to another project/tool/model', async () => {
    const wrapper = mountForm({ optionsState: 'loading' })
    expect(wrapper.get('[role="status"]').text()).toContain('正在读取')
    expect((wrapper.get('[data-testid="sfc-search-submit"]').element as HTMLButtonElement).disabled).toBe(true)
    await wrapper.setProps({ optionsState: 'ready', options: { ...optionsFor(modelA), projectId: 'other-project' } })
    expect(wrapper.find('.axes-section').exists()).toBe(false)
    expect((wrapper.get('[data-testid="sfc-search-submit"]').element as HTMLButtonElement).disabled).toBe(true)
    wrapper.unmount()
  })

  it('filters threshold and model group from metadata, then auto-selects the unique exact READY match', async () => {
    const wrapper = mountForm()
    await wrapper.get('[data-testid="sfc-threshold-filter"]').setValue('1.5')
    const modelsAfterThreshold = wrapper.get('[data-testid="sfc-model-select"]').findAll('option').map((option) => option.text())
    expect(modelsAfterThreshold.some((label) => label.includes('Model A'))).toBe(false)
    expect(modelsAfterThreshold.some((label) => label.includes('Model B'))).toBe(true)
    await wrapper.get('[data-testid="sfc-group-filter"]').setValue('group-b')
    expect(wrapper.emitted('model-change')?.slice(0, 2).map(([value]) => value)).toEqual([
      { modelId: modelB.id, modelVersion: modelB.version },
    ])
    await wrapper.get('[data-testid="sfc-model-select"]').setValue(JSON.stringify([modelB.id, modelB.version]))
    await wrapper.setProps({ selectedModel: modelB, options: optionsFor(modelB) })
    expect(wrapper.get('[data-testid="sfc-model-group"]').text()).toContain('group-b')
    expect(wrapper.find('.axes-section').exists()).toBe(true)
    expect((wrapper.get('[data-testid="sfc-search-submit"]').element as HTMLButtonElement).disabled).toBe(true)
    wrapper.unmount()
  })

  it('preserves a selected exact model when a changed filter still includes it', async () => {
    const wrapper = mountForm()
    await wrapper.get('[data-testid="sfc-threshold-filter"]').setValue('1.2')
    expect((wrapper.get('[data-testid="sfc-model-select"]').element as HTMLSelectElement).value).toBe(JSON.stringify([modelA.id, modelA.version]))
    expect(wrapper.emitted('model-change')).toBeUndefined()
    wrapper.unmount()
  })

  it('keeps model options aligned with the selected tool scope and requires the selected column in the server column axis', async () => {
    const wrongScope = mountForm({ selectedToolId: fullTool.id, selectedModel: null, options: null, models: [modelA] })
    expect(wrongScope.get('[data-testid="sfc-model-select"]').findAll('option')).toHaveLength(1)
    expect((wrongScope.get('[data-testid="sfc-search-submit"]').element as HTMLButtonElement).disabled).toBe(true)
    wrongScope.unmount()

    const optionsWithoutAllowedColumn = optionsFor(modelA)
    optionsWithoutAllowedColumn.axes = axes.map((axis) => axis.key === 'column' ? { ...axis, values: ['column-b'] } : axis)
    const wrapper = mountForm({ options: optionsWithoutAllowedColumn })
    expect(wrapper.get('[data-testid="sfc-column-select"]').findAll('option')).toHaveLength(1)
    expect((wrapper.get('[data-testid="sfc-search-submit"]').element as HTMLButtonElement).disabled).toBe(true)
    wrapper.unmount()
  })

  it('supports all-column search with a multi-value column axis and no single-column fields', async () => {
    const allColumnModel: SfcSearchModelChoice = {
      ...modelA, id: 'model-all', version: 'version-all', label: 'All columns', business_scope_id: 'business-all',
      model_scope: 'all_column', group_id: null, group_label: null, stationary_phase: null, mode: null, allowed_columns: null,
    }
    const wrapper = mountForm({
      selectedToolId: fullTool.id, selectedModel: allColumnModel, models: [modelA, allColumnModel],
      options: optionsFor(allColumnModel, fullTool.id),
    })
    expect(wrapper.get('[data-testid="sfc-model-select"]').findAll('option').map((option) => option.text()).join(' ')).toContain('All columns')
    for (const key of SFC_CANDIDATE_KEYS) await chooseAxis(wrapper, key, key === 'column' ? ['0', '1'] : ['0'])
    await wrapper.get('[data-testid="add-modifier"]').trigger('click')
    await wrapper.find('[data-testid^="normalize-structure-"]').trigger('click')
    await wrapper.get('[data-testid="modifier-fraction-1"]').setValue('1')
    await wrapper.get('[data-testid="modifier-total-fraction"]').setValue('0.25')
    await wrapper.get('[data-testid="sfc-top-k"]').setValue('10')
    expect((wrapper.get('[data-testid="sfc-search-submit"]').element as HTMLButtonElement).disabled).toBe(false)
    await wrapper.get('[data-testid="sfc-search-form"]').trigger('submit')
    const emitted = wrapper.emitted('submit')?.[0]?.[0] as Record<string, unknown>
    expect((emitted.candidateAxes as Record<string, unknown[]>).column).toEqual(['column-a', 'column-b'])
    expect(emitted).not.toHaveProperty('group_id')
    expect(emitted).not.toHaveProperty('column_id')
    wrapper.unmount()
  })

  it('keeps multiple axis selections after rerender and submits their values', async () => {
    const allColumnModel: SfcSearchModelChoice = {
      ...modelA, id: 'model-all', version: 'version-all', label: 'All columns', business_scope_id: 'business-all',
      model_scope: 'all_column', group_id: null, group_label: null, stationary_phase: null, mode: null, allowed_columns: null,
    }
    const wrapper = mountForm({
      selectedToolId: fullTool.id, selectedModel: allColumnModel, models: [modelA, allColumnModel],
      options: optionsFor(allColumnModel, fullTool.id),
    })
    for (const key of SFC_CANDIDATE_KEYS) await chooseAxis(wrapper, key, key === 'column' ? ['0', '1'] : ['0'])

    const columnAxis = wrapper.get('[data-testid="axis-column"]').element as HTMLSelectElement
    const selectedColumnIndexes = () => wrapper.findAll('[data-testid="axis-chips-column"] li').map((_, index) => String(index))
    expect(selectedColumnIndexes()).toEqual(['0', '1'])
    await wrapper.get('[data-testid="sfc-top-k"]').setValue('10')
    expect(selectedColumnIndexes()).toEqual(['0', '1'])

    await wrapper.get('[data-testid="add-modifier"]').trigger('click')
    await wrapper.find('[data-testid^="normalize-structure-"]').trigger('click')
    await wrapper.get('[data-testid="modifier-fraction-1"]').setValue('1')
    await wrapper.get('[data-testid="modifier-total-fraction"]').setValue('0.25')
    await wrapper.get('[data-testid="sfc-search-form"]').trigger('submit')
    const emitted = wrapper.emitted('submit')?.[0]?.[0] as Record<string, unknown>
    expect((emitted.candidateAxes as Record<string, unknown[]>).column).toEqual(['column-a', 'column-b'])
    wrapper.unmount()

    const singleColumnWrapper = mountForm()
    await singleColumnWrapper.get('[data-testid="sfc-column-select"]').setValue('column-a')
    const singleColumnAxis = singleColumnWrapper.get('[data-testid="axis-column"]').element as HTMLSelectElement
    expect(singleColumnAxis.disabled).toBe(true)
    expect(singleColumnWrapper.get('[data-testid="axis-chips-column"]').text()).toContain('column-a')
    singleColumnWrapper.unmount()
  })
})
