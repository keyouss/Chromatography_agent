import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import OfflinePredictionForm from '@/modules/prediction/forms/basic/OfflinePredictionForm.vue'

afterEach(() => vi.restoreAllMocks())

describe('offline prediction preview', () => {
  it('keeps local SMILES visible after the connection returns without sending a request', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch')
    const wrapper = mount(OfflinePredictionForm, { props: { type: 'hplc', offline: true } })
    const smiles = wrapper.get('[data-testid="prediction-offline-smiles"]')

    await smiles.setValue('C[C@H](N)C(=O)O')
    await wrapper.setProps({ offline: false })
    await flushPromises()

    expect(wrapper.get('[data-testid="prediction-offline-form"]').text()).toContain('本地草稿')
    expect((wrapper.get('[data-testid="prediction-offline-smiles"]').element as HTMLTextAreaElement).value).toBe('C[C@H](N)C(=O)O')
    expect(wrapper.get('[data-testid="prediction-offline-schema"]').text()).toContain('均未知')
    expect(fetchMock).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('keeps the primary draft visible if the secondary pair input is cleared', async () => {
    const wrapper = mount(OfflinePredictionForm, { props: { type: 'cc', offline: true } })
    await wrapper.get('[data-testid="prediction-offline-smiles"]').setValue('CCO')
    await wrapper.get('[data-testid="prediction-offline-smiles-secondary"]').setValue('CC')
    await wrapper.get('[data-testid="prediction-offline-smiles-secondary"]').setValue('')
    await wrapper.setProps({ offline: false })

    expect(wrapper.find('[data-testid="prediction-offline-form"]').exists()).toBe(true)
    expect((wrapper.get('[data-testid="prediction-offline-smiles"]').element as HTMLTextAreaElement).value).toBe('CCO')
    wrapper.unmount()
  })
})
