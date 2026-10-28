import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import StructureInput from '@/components/StructureInput.vue'

function envelope(data: unknown, status = 200) {
  return new Response(JSON.stringify({ data, request_id: 'structure-input-test', warnings: [] }), {
    status,
    headers: { 'content-type': 'application/json' },
  })
}

function structure(projectId: string, value: string, id = 'structure-1') {
  return {
    id, project_id: projectId, canonical_smiles: `canonical:${value}`, schema_version: '1.0',
    snapshot_hash: 'a'.repeat(64), created_at: '2026-10-07T00:00:00Z', is_valid: true,
    has_stereochemistry: false, stereochemistry_status: 'not_applicable',
    original_input: { input_type: 'smiles', value }, stereochemistry_warnings: [],
  }
}

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((done) => { resolve = done })
  return { promise, resolve }
}

describe('StructureInput normalization binding', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn())
  })

  it('keeps completed normalization through a temporary submission lock', async () => {
    vi.mocked(fetch).mockResolvedValue(envelope(structure('project-1', 'CO')))
    const wrapper = mount(StructureInput, { props: { projectId: 'project-1' } })
    await wrapper.get('textarea').setValue('CO')
    await wrapper.get('.primary').trigger('click')
    await flushPromises()
    const invalidations = wrapper.emitted('invalidated')?.length
    await wrapper.setProps({ disabled: true })
    expect(wrapper.get('textarea').attributes('disabled')).toBeDefined()
    await wrapper.setProps({ disabled: false })
    expect(wrapper.get('.result-box code').text()).toBe('canonical:CO')
    expect(wrapper.emitted('invalidated')).toHaveLength(invalidations!)
    await wrapper.get('textarea').setValue('CCO')
    expect(wrapper.find('.result-box').exists()).toBe(false)
    wrapper.unmount()
  })

  it('still rejects normalization pending when the input becomes disabled', async () => {
    const pending = deferred<Response>()
    vi.mocked(fetch).mockReturnValue(pending.promise)
    const wrapper = mount(StructureInput, { props: { projectId: 'project-1' } })
    await wrapper.get('textarea').setValue('CO')
    await wrapper.get('.primary').trigger('click')
    await wrapper.setProps({ disabled: true })
    await wrapper.setProps({ disabled: false })
    pending.resolve(envelope(structure('project-1', 'CO')))
    await flushPromises()
    expect(wrapper.emitted('normalized')).toBeUndefined()
    expect(wrapper.find('.result-box').exists()).toBe(false)
    wrapper.unmount()
  })

  it('invalidates a normalized result as soon as its input is edited', async () => {
    vi.mocked(fetch).mockResolvedValueOnce(envelope(structure('project-1', 'C'))).mockResolvedValueOnce(envelope(structure('project-1', 'CC', 'structure-2')))
    const wrapper = mount(StructureInput, { props: { projectId: 'project-1' } })
    const textarea = wrapper.get('textarea')
    await textarea.setValue('C')
    await wrapper.get('.primary').trigger('click')
    await flushPromises()
    expect(wrapper.emitted('normalized')).toHaveLength(1)
    expect(wrapper.get('.result-box code').text()).toBe('canonical:C')

    await textarea.setValue('CC')
    expect(wrapper.find('.result-box').exists()).toBe(false)
    expect(wrapper.emitted('invalidated')).toHaveLength(3)

    await wrapper.get('.primary').trigger('click')
    await flushPromises()
    expect(wrapper.emitted('normalized')).toHaveLength(2)
    expect(wrapper.get('.result-box code').text()).toBe('canonical:CC')
    wrapper.unmount()
  })

  it('ignores a response for text edited while normalization is pending', async () => {
    const pending = deferred<Response>()
    vi.mocked(fetch).mockReturnValue(pending.promise)
    const wrapper = mount(StructureInput, { props: { projectId: 'project-1' } })
    const textarea = wrapper.get('textarea')
    await textarea.setValue('C')
    await wrapper.get('.primary').trigger('click')
    await textarea.setValue('CC')
    pending.resolve(envelope(structure('project-1', 'C')))
    await flushPromises()
    expect(wrapper.emitted('normalized')).toBeUndefined()
    expect(wrapper.emitted('normalized-with-raw')).toBeUndefined()
    expect(wrapper.find('.result-box').exists()).toBe(false)
    expect(wrapper.get('.status-dot').classes()).toContain('idle')
    wrapper.unmount()
  })

  it('ignores pending responses after project change or component unmount', async () => {
    const first = deferred<Response>()
    const second = deferred<Response>()
    vi.mocked(fetch).mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise)
    const wrapper = mount(StructureInput, { props: { projectId: 'project-1' } })
    await wrapper.get('textarea').setValue('C')
    await wrapper.get('.primary').trigger('click')
    await wrapper.setProps({ projectId: 'project-2' })
    first.resolve(envelope(structure('project-1', 'C')))
    await flushPromises()
    expect(wrapper.emitted('normalized')).toBeUndefined()
    expect(wrapper.emitted('normalized-with-raw')).toBeUndefined()

    await wrapper.get('textarea').setValue('CC')
    await wrapper.get('.primary').trigger('click')
    wrapper.unmount()
    second.resolve(envelope(structure('project-2', 'CC')))
    await flushPromises()
    expect(wrapper.emitted('normalized')).toBeUndefined()
    expect(wrapper.emitted('normalized-with-raw')).toBeUndefined()
  })

  it('clears the prior result when retrying and restores validity only after the new response', async () => {
    const failure = deferred<Response>()
    vi.mocked(fetch).mockResolvedValueOnce(envelope(structure('project-1', 'C'))).mockReturnValueOnce(failure.promise)
    const wrapper = mount(StructureInput, { props: { projectId: 'project-1' } })
    await wrapper.get('textarea').setValue('C')
    await wrapper.get('.primary').trigger('click')
    await flushPromises()
    expect(wrapper.emitted('normalized')).toHaveLength(1)
    expect(wrapper.emitted('normalized-with-raw')).toHaveLength(1)

    await wrapper.get('.primary').trigger('click')
    expect(wrapper.find('.result-box').exists()).toBe(false)
    failure.resolve(envelope({ code: 'NORMALIZE_FAILED', message: 'failed' }, 422))
    await flushPromises()
    expect(wrapper.emitted('normalized')).toHaveLength(1)
    expect(wrapper.emitted('normalized-with-raw')).toHaveLength(1)
    expect(wrapper.find('.result-box').exists()).toBe(false)
    expect(wrapper.get('.error-text').text()).toContain('请求失败')
    wrapper.unmount()
  })

  it('does not accept a normalization response for a different project', async () => {
    vi.mocked(fetch).mockResolvedValue(envelope(structure('other-project', 'C')))
    const wrapper = mount(StructureInput, { props: { projectId: 'project-1' } })
    await wrapper.get('textarea').setValue('C')
    await wrapper.get('.primary').trigger('click')
    await flushPromises()
    expect(wrapper.emitted('normalized')).toBeUndefined()
    expect(wrapper.get('.error-text').text()).toContain('不属于当前项目')
    wrapper.unmount()
  })

  it('emits the exact request SMILES with the returned structure, independent of server original_input', async () => {
    const returned = { ...structure('project-1', 'server-original'), original_input: { input_type: 'smiles', value: 'server-original' } }
    vi.mocked(fetch).mockResolvedValueOnce(envelope(returned))
    const eventOrder: string[] = []
    const wrapper = mount(StructureInput, { props: {
      projectId: 'project-1',
      onNormalized: () => eventOrder.push('normalized'),
      onNormalizedWithRaw: () => eventOrder.push('normalized-with-raw'),
    } })
    const rawSmiles = ' C[C@H](O) '
    await wrapper.get('textarea').setValue(rawSmiles)
    await wrapper.get('.primary').trigger('click')
    await flushPromises()

    const request = vi.mocked(fetch).mock.calls[0]![1]
    expect(JSON.parse(String(request?.body)).value).toBe(rawSmiles)
    expect(wrapper.emitted('normalized')?.[0]).toEqual([returned])
    expect(wrapper.emitted('normalized-with-raw')?.[0]).toEqual([{ rawSmiles, structure: returned }])
    expect(eventOrder).toEqual(['normalized', 'normalized-with-raw'])
    wrapper.unmount()
  })

  it('uses a unique textarea id and fills the supplied example without normalizing it', async () => {
    const wrapper = mount({
      components: { StructureInput },
      template: '<div><StructureInput project-id="project-1" label="对映体一" example-smiles="C[C@H](O)" /><StructureInput project-id="project-1" label="对映体二" example-smiles="C[C@@H](O)" /></div>',
    })
    const inputs = wrapper.findAll('textarea')
    expect(inputs.map((input) => input.attributes('id'))).toHaveLength(2)
    expect(new Set(inputs.map((input) => (input.element as HTMLTextAreaElement).id)), inputs.map((input) => (input.element as HTMLTextAreaElement).id).join(',')).toHaveProperty('size', 2)
    expect(wrapper.text()).toContain('对映体一')
    expect(wrapper.text()).toContain('对映体二')

    await wrapper.findAll('[data-testid="fill-example"]')[1]!.trigger('click')
    expect((inputs[1]!.element as HTMLTextAreaElement).value).toBe('C[C@@H](O)')
    expect(vi.mocked(fetch)).not.toHaveBeenCalled()
    expect(wrapper.findAll('.result-box')).toHaveLength(0)
    wrapper.unmount()
  })
})
