import { flushPromises, mount } from '@vue/test-utils'
import { defineComponent, h, provide } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import SharedKetcherCanvas from '@/components/SharedKetcherCanvas.vue'
import StructureInput from '@/components/StructureInput.vue'
import StructureWorkspace from '@/components/StructureWorkspace.vue'
import { createStructureWorkspaceContext, structureWorkspaceKey } from '@/core/structure/workspace'
import type { KetcherApi } from '@/core/structure/frameadapter'

function envelope(data: unknown, status = 200) {
  return new Response(JSON.stringify({ data, request_id: 'shared-canvas-test', warnings: [] }), {
    status,
    headers: { 'content-type': 'application/json' },
  })
}

function structure(value: string) {
  return {
    id: 'structure-canvas', project_id: 'project-1', canonical_smiles: value, schema_version: '1.0',
    snapshot_hash: 'a'.repeat(64), created_at: '2026-10-20T00:00:00Z', is_valid: true,
    has_stereochemistry: value.includes('@'), stereochemistry_status: value.includes('@') ? 'defined' : 'not_applicable',
    original_input: { input_type: 'smiles', value }, stereochemistry_warnings: [],
  }
}

function createKetcher(getSmiles: () => string | Promise<string>) {
  let onChange: (() => void) | undefined
  const api: KetcherApi = {
    editor: {
      subscribe: vi.fn((_event, handler) => { onChange = handler; return handler }),
      unsubscribe: vi.fn(),
    },
    getSmiles: vi.fn(getSmiles),
    setMolecule: vi.fn().mockResolvedValue(undefined),
  }
  return { api, change: () => onChange?.() }
}

function attachKetcher(wrapper: ReturnType<typeof mount>, api: KetcherApi) {
  const frame = wrapper.get('iframe').element as HTMLIFrameElement
  const frameWindow = new EventTarget() as unknown as Window & { ketcher?: KetcherApi }
  frameWindow.ketcher = api
  Object.defineProperty(frame, 'contentWindow', { configurable: true, value: frameWindow })
  return wrapper.get('iframe').trigger('load')
}

function installFrameWindow(wrapper: ReturnType<typeof mount>) {
  const frame = wrapper.get('iframe').element as HTMLIFrameElement
  const frameWindow = new EventTarget() as unknown as Window & { ketcher?: KetcherApi }
  Object.defineProperty(frame, 'contentWindow', { configurable: true, value: frameWindow })
  return frameWindow
}

describe('shared structure drawing workspace', () => {
  beforeEach(() => vi.stubGlobal('fetch', vi.fn()))
  afterEach(() => vi.useRealTimers())

  it('mounts one frame for all targets and applies exported raw stereochemical SMILES through server validation', async () => {
    vi.mocked(fetch).mockResolvedValue(envelope(structure('C[C@H](O)c1ccc(cc1)C#N')))
    const normalized = vi.fn()
    const normalizedWithRaw = vi.fn()
    const wrapper = mount(StructureWorkspace, {
      slots: {
        default: () => [
          h(StructureInput, { projectId: 'project-1', targetId: 'primary', label: '结构 1', onNormalized: normalized, onNormalizedWithRaw: normalizedWithRaw }),
          h(StructureInput, { projectId: 'project-1', targetId: 'secondary', label: '结构 2' }),
          h(SharedKetcherCanvas),
        ],
      },
    })
    expect(wrapper.findAll('iframe')).toHaveLength(1)
    const rawSmiles = 'C[C@H](O)c1ccc(cc1)C#N'
    const ketcher = createKetcher(() => rawSmiles)
    await attachKetcher(wrapper, ketcher.api)
    ketcher.change()
    expect(wrapper.get('[data-testid="apply-ketcher"]').attributes('disabled')).toBeUndefined()
    await flushPromises()
    expect(wrapper.get('[data-testid="apply-ketcher"]').attributes('disabled')).toBeUndefined()
    await wrapper.get('[data-testid="apply-ketcher"]').trigger('click')
    await flushPromises()

    expect(ketcher.api.getSmiles).toHaveBeenCalledOnce()
    expect((wrapper.find('textarea').element as HTMLTextAreaElement).value).toBe(rawSmiles)
    const request = vi.mocked(fetch).mock.calls[0]?.[1]
    expect(JSON.parse(String(request?.body))).toMatchObject({ value: rawSmiles, preserve_stereochemistry: true })
    expect(normalized.mock.calls[0]?.[0]).toMatchObject({ canonical_smiles: rawSmiles, stereochemistry_status: 'defined' })
    expect(normalizedWithRaw.mock.calls[0]?.[0]).toMatchObject({ rawSmiles, structure: { canonical_smiles: rawSmiles } })
    expect(wrapper.get('[data-testid="apply-ketcher"]').text()).toContain('应用绘图并校验')
    wrapper.unmount()
  })

  it('requires an explicit discard before switching away from a dirty canvas', async () => {
    const wrapper = mount(StructureWorkspace, {
      slots: { default: () => [
        h(StructureInput, { projectId: 'project-1', targetId: 'primary', label: '结构 1', exampleSmiles: 'CC' }),
        h(StructureInput, { projectId: 'project-1', targetId: 'secondary', label: '结构 2', exampleSmiles: 'CO' }),
        h(SharedKetcherCanvas),
      ] },
    })
    const ketcher = createKetcher(() => 'CC')
    await attachKetcher(wrapper, ketcher.api)
    await wrapper.findAll('textarea')[1]!.setValue('CO')
    ketcher.change()
    await flushPromises()
    const selector = wrapper.get('[data-testid="ketcher-target"]')
    await selector.setValue('secondary')
    expect((selector.element as HTMLSelectElement).value).toBe('primary')
    expect(wrapper.text()).toContain('请先应用当前绘图，或明确放弃后再切换目标。')
    await wrapper.get('[data-testid="discard-ketcher"]').trigger('click')
    expect((selector.element as HTMLSelectElement).value).toBe('secondary')
    await flushPromises()
    expect(ketcher.api.setMolecule).toHaveBeenCalledWith('CO')
    wrapper.unmount()
  })

  it('ignores exported smiles after the active target changes during export', async () => {
    let selectSecondary!: () => void
    let resolveSmiles!: (value: string) => void
    const exported = new Promise<string>((resolve) => { resolveSmiles = resolve })
    const TestHost = defineComponent({
      setup() {
        const context = createStructureWorkspaceContext()
        provide(structureWorkspaceKey, context)
        selectSecondary = () => { context.activeTargetId.value = 'secondary' }
        return () => h('div', [
          h(StructureInput, { projectId: 'project-1', targetId: 'primary', label: '结构 1' }),
          h(StructureInput, { projectId: 'project-1', targetId: 'secondary', label: '结构 2' }),
          h(SharedKetcherCanvas),
        ])
      },
    })
    const wrapper = mount(TestHost)
    const ketcher = createKetcher(() => exported)
    await attachKetcher(wrapper, ketcher.api)
    ketcher.change()
    await wrapper.get('[data-testid="apply-ketcher"]').trigger('click')
    selectSecondary()
    await flushPromises()
    resolveSmiles('C[C@H](O)')
    await flushPromises()

    expect(vi.mocked(fetch)).not.toHaveBeenCalled()
    expect(wrapper.findAll('textarea')).toHaveLength(2)
    expect((wrapper.findAll('textarea')[0]!.element as HTMLTextAreaElement).value).toBe('')
    wrapper.unmount()
  })

  it('keeps structure references invalid when backend validation rejects an exported structure', async () => {
    vi.mocked(fetch).mockResolvedValue(envelope({ code: 'INVALID_SMILES', message: 'SMILES invalid' }, 422))
    const invalidated = vi.fn()
    const normalized = vi.fn()
    const wrapper = mount(StructureWorkspace, {
      slots: { default: () => [
        h(StructureInput, { projectId: 'project-1', targetId: 'primary', label: '结构 1', onInvalidated: invalidated, onNormalized: normalized }),
        h(SharedKetcherCanvas),
      ] },
    })
    const ketcher = createKetcher(() => 'C')
    await attachKetcher(wrapper, ketcher.api)
    ketcher.change()
    await wrapper.get('[data-testid="apply-ketcher"]').trigger('click')
    await flushPromises()
    expect(vi.mocked(fetch)).toHaveBeenCalledOnce()
    expect(invalidated).toHaveBeenCalled()
    expect(normalized).not.toHaveBeenCalled()
    expect(wrapper.find('.result-box').exists()).toBe(false)
    expect(wrapper.text()).toContain('请求失败')
    wrapper.unmount()
  })

  it('clears a previously valid reference when standalone Ketcher export fails', async () => {
    vi.mocked(fetch).mockResolvedValueOnce(envelope(structure('CC')))
    const normalized = vi.fn()
    const invalidated = vi.fn()
    const wrapper = mount(StructureWorkspace, {
      slots: { default: () => [
        h(StructureInput, { projectId: 'project-1', targetId: 'primary', label: '结构 1', onNormalized: normalized, onInvalidated: invalidated }),
        h(SharedKetcherCanvas),
      ] },
    })
    await wrapper.get('textarea').setValue('CC')
    await wrapper.findAll('.primary')[0]!.trigger('click')
    await flushPromises()
    expect(normalized).toHaveBeenCalledOnce()
    expect(wrapper.find('.result-box').exists()).toBe(true)

    const ketcher = createKetcher(() => Promise.reject(new Error('standalone export failed')))
    await attachKetcher(wrapper, ketcher.api)
    ketcher.change()
    await flushPromises()
    expect(invalidated).toHaveBeenCalled()
    expect(wrapper.find('.result-box').exists()).toBe(false)
    await wrapper.get('[data-testid="apply-ketcher"]').trigger('click')
    await flushPromises()

    expect(invalidated).toHaveBeenCalled()
    expect(normalized).toHaveBeenCalledOnce()
    expect(wrapper.find('.result-box').exists()).toBe(false)
    expect(wrapper.text()).toContain('standalone export failed')
    wrapper.unmount()
  })

  it('ignores an export from the previous iframe generation after reload', async () => {
    vi.useFakeTimers()
    let resolveSmiles!: (value: string) => void
    const exported = new Promise<string>((resolve) => { resolveSmiles = resolve })
    const wrapper = mount(StructureWorkspace, {
      slots: { default: () => [
        h(StructureInput, { projectId: 'project-1', targetId: 'primary', label: '结构 1' }),
        h(SharedKetcherCanvas),
      ] },
    })
    const frameWindow = installFrameWindow(wrapper)
    const ketcher = createKetcher(() => exported)
    frameWindow.ketcher = ketcher.api
    await wrapper.get('iframe').trigger('load')
    await flushPromises()
    await wrapper.get('[data-testid="apply-ketcher"]').trigger('click')

    frameWindow.dispatchEvent(new Event('beforeunload'))
    resolveSmiles('C[C@H](O)')
    await flushPromises()

    expect(vi.mocked(fetch)).not.toHaveBeenCalled()
    expect((wrapper.get('textarea').element as HTMLTextAreaElement).value).toBe('')
    wrapper.unmount()
  })

  it('blocks export and pointer interaction for disabled targets', async () => {
    const wrapper = mount(StructureWorkspace, {
      slots: { default: () => [
        h(StructureInput, { projectId: 'project-1', targetId: 'primary', label: '结构 1', disabled: true }),
        h(SharedKetcherCanvas),
      ] },
    })
    const ketcher = createKetcher(() => 'C')
    await attachKetcher(wrapper, ketcher.api)
    const apply = wrapper.get('[data-testid="apply-ketcher"]')
    expect(apply.attributes('disabled')).toBeDefined()
    expect(wrapper.text()).toContain('当前结构输入已禁用')
    expect(vi.mocked(fetch)).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('waits for the standalone API published after the iframe load event', async () => {
    vi.useFakeTimers()
    const wrapper = mount(StructureWorkspace, {
      slots: { default: () => [
        h(StructureInput, { projectId: 'project-1', targetId: 'primary', label: '结构 1' }),
        h(SharedKetcherCanvas),
      ] },
    })
    const frameWindow = installFrameWindow(wrapper)
    await wrapper.get('iframe').trigger('load')
    expect(wrapper.get('[data-testid="apply-ketcher"]').attributes('disabled')).toBeDefined()

    const ketcher = createKetcher(() => 'CC')
    await vi.advanceTimersByTimeAsync(250)
    frameWindow.ketcher = ketcher.api
    await vi.advanceTimersByTimeAsync(50)
    await flushPromises()

    expect(ketcher.api.editor.subscribe).toHaveBeenCalledOnce()
    expect(wrapper.get('[data-testid="apply-ketcher"]').attributes('disabled')).toBeUndefined()
    wrapper.unmount()
  })

  it('cancels stale API probes on iframe reload and component unmount', async () => {
    vi.useFakeTimers()
    const wrapper = mount(StructureWorkspace, {
      slots: { default: () => [
        h(StructureInput, { projectId: 'project-1', targetId: 'primary', label: '结构 1' }),
        h(SharedKetcherCanvas),
      ] },
    })
    const frameWindow = installFrameWindow(wrapper)
    await wrapper.get('iframe').trigger('load')
    frameWindow.dispatchEvent(new Event('beforeunload'))

    const ketcher = createKetcher(() => 'CC')
    frameWindow.ketcher = ketcher.api
    await vi.advanceTimersByTimeAsync(6000)
    expect(ketcher.api.editor.subscribe).not.toHaveBeenCalled()

    await wrapper.get('iframe').trigger('load')
    await flushPromises()
    expect(ketcher.api.editor.subscribe).toHaveBeenCalledOnce()
    wrapper.unmount()
    expect(ketcher.api.editor.unsubscribe).toHaveBeenCalledOnce()
    await vi.advanceTimersByTimeAsync(6000)
    expect(ketcher.api.editor.subscribe).toHaveBeenCalledOnce()
  })
})
