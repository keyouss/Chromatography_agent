import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import { defineComponent, ref } from 'vue'
import HplcSingleForm from '@/modules/prediction/forms/basic/HplcSingleForm.vue'
import type { HplcFormValue } from '@/modules/prediction/forms/basic/form-types'

const columns = [{ value: 'chiral-column-a', label: 'Chiral column A' }, { value: 'chiral-column-b', label: 'Chiral column B' }]
const validValue: HplcFormValue = { column_ids: ['chiral-column-a'], isopropanol_fraction: 0.35, flow_rate_ml_min: 1.2 }

describe('HPLC single form submission gates', () => {
  it('does not echo restored checkbox drafts indefinitely through the parent workspace', async () => {
    let updates = 0
    const host = mount(defineComponent({
      components: { HplcSingleForm },
      setup() {
        const draft = ref(validValue)
        return { columns, draft, update(value: HplcFormValue) { updates += 1; draft.value = value } }
      },
      template: '<HplcSingleForm :columns="columns" :initial-value="draft" @update:value="update" />',
    }))
    await host.get('input[value="chiral-column-b"]').setValue(true)
    expect(updates).toBe(1)
    expect(host.getComponent(HplcSingleForm).vm.$.exposed?.getValue().column_ids).toEqual(['chiral-column-a', 'chiral-column-b'])
    host.unmount()
  })

  it('disables submission for external gates without showing a submitting caption', async () => {
    const wrapper = mount(HplcSingleForm, { props: { columns, initialValue: validValue, disabled: true } })
    const button = wrapper.get('[data-testid="hplc-submit"]')

    expect((button.element as HTMLButtonElement).disabled).toBe(true)
    expect(button.text()).toBe('开始 HPLC 预测')
    await button.trigger('click')
    expect(wrapper.emitted('submit')).toBeUndefined()
  })

  it('shows submitting only while an HPLC submission is active', () => {
    const wrapper = mount(HplcSingleForm, { props: { columns, initialValue: validValue, busy: true } })
    const button = wrapper.get('[data-testid="hplc-submit"]')

    expect((button.element as HTMLButtonElement).disabled).toBe(true)
    expect(button.text()).toBe('提交中…')
  })

  it('emits the selected conditions when enabled', async () => {
    const wrapper = mount(HplcSingleForm, { props: { columns, initialValue: validValue } })
    const button = wrapper.get('[data-testid="hplc-submit"]')

    expect((button.element as HTMLButtonElement).disabled).toBe(false)
    await button.trigger('click')
    expect(wrapper.emitted('submit')?.[0]?.[0]).toEqual(validValue)
  })

  it('toggles native checkbox selections by ordinary clicks, preserves them while locked, and exposes draft updates', async () => {
    const wrapper = mount(HplcSingleForm, { props: { columns, initialValue: validValue } })
    const first = wrapper.get('input[type="checkbox"][value="chiral-column-a"]')
    const second = wrapper.get('input[type="checkbox"][value="chiral-column-b"]')
    expect((first.element as HTMLInputElement).checked).toBe(true)
    await second.setValue(true)
    expect(wrapper.vm.$.exposed?.getValue()).toEqual({ ...validValue, column_ids: ['chiral-column-a', 'chiral-column-b'] })
    expect(wrapper.emitted('update:value')?.at(-1)?.[0]).toEqual({ ...validValue, column_ids: ['chiral-column-a', 'chiral-column-b'] })
    await first.setValue(false)
    expect(wrapper.vm.$.exposed?.getValue()).toEqual({ ...validValue, column_ids: ['chiral-column-b'] })

    await wrapper.setProps({ busy: true })
    expect((wrapper.get('input[type="checkbox"][value="chiral-column-b"]').element as HTMLInputElement).matches(':disabled')).toBe(true)
    expect(wrapper.vm.$.exposed?.getValue()).toEqual({ ...validValue, column_ids: ['chiral-column-b'] })
  })
})
