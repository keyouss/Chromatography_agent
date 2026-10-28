import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { AccessContextView } from '@/core/types'
import { requestAccessContext } from '@/core/project-context'
import AcceptanceTlcBatchPage from '../candidates/tlc-batch-revision20261008-01/runtime/AcceptanceTlcBatchPage.vue'

vi.mock('@/core/project-context', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/core/project-context')>()
  return { ...actual, requestAccessContext: vi.fn() }
})

vi.mock('../candidates/tlc-batch-revision20261008-01/src/modules/prediction/forms/tlc/TlcBatchWorkspace.vue', () => ({
  default: {
    props: ['projectId', 'canReadBatch', 'canUploadBatch', 'canCreateBatch', 'canCancelBatch', 'permissionRevision'],
    template: '<div data-testid="workspace" :data-project-id="projectId" :data-can-read="String(canReadBatch)" :data-can-upload="String(canUploadBatch)" :data-can-create="String(canCreateBatch)" :data-can-cancel="String(canCancelBatch)" />',
  },
}))

function accessContext(input: Partial<AccessContextView> = {}): AccessContextView {
  return {
    actor_id: 'actor-1', actor: { id: 'actor-1', policy_version: 'policy-1' }, auth_mode: 'deployment_default',
    is_multi_user_authenticated: false, permissions: [], allowed_actions: [], project_selection: 'resolved',
    default_project_id: 'project-1', accessible_projects: [{ id: 'project-1', name: '项目甲' }], ...input,
  } as AccessContextView
}

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej })
  return { promise, resolve, reject }
}

afterEach(() => { vi.restoreAllMocks(); vi.mocked(requestAccessContext).mockReset() })

describe('TLC batch candidate standalone entry', () => {
  it('keeps workspace closed until a user selects an accessible project', async () => {
    const requestMock = vi.mocked(requestAccessContext)
    requestMock.mockResolvedValueOnce(accessContext({
      project_selection: 'needs_selection', default_project_id: null,
      accessible_projects: [{ id: 'private-a', name: '公开名称甲' }, { id: 'private-b', name: '公开名称乙' }],
    }))
    const wrapper = mount(AcceptanceTlcBatchPage)
    await flushPromises()
    expect(wrapper.findAll('[data-testid="workspace"]')).toHaveLength(0)
    expect(wrapper.findAll('[data-testid="project-selection-required"]')).toHaveLength(1)
    expect(wrapper.text()).toContain('公开名称甲')
    expect(wrapper.text()).toContain('公开名称乙')
    expect(wrapper.text()).not.toContain('private-a')
    await wrapper.get('[data-testid="project-select"]').setValue('private-b')
    expect(wrapper.get('[data-testid="workspace"]').attributes('data-project-id')).toBe('private-b')
    expect(requestMock).toHaveBeenCalledTimes(1)
    wrapper.unmount()
  })

  it('mounts only for accessible projects and passes exact permission scopes', async () => {
    const actions = ['batch:read', 'artifact:read', 'artifact:write', 'batch:create', 'batch:write', 'batch:validate', 'batch:submit', 'prediction:create']
    const requestMock = vi.mocked(requestAccessContext)
    requestMock.mockResolvedValueOnce(accessContext({ allowed_actions: actions }))
    const wrapper = mount(AcceptanceTlcBatchPage)
    await flushPromises()
    const workspace = wrapper.get('[data-testid="workspace"]')
    expect(workspace.attributes()).toMatchObject({
      'data-project-id': 'project-1', 'data-can-read': 'true', 'data-can-upload': 'true',
      'data-can-create': 'true', 'data-can-cancel': 'false',
    })
    expect(requestMock).toHaveBeenCalledTimes(1)
    wrapper.unmount()
  })

  it('does not restore actions from permissions when allowed_actions excludes them', async () => {
    const requestMock = vi.mocked(requestAccessContext)
    requestMock.mockResolvedValueOnce(accessContext({ permissions: ['*'], allowed_actions: [] }))
    const wrapper = mount(AcceptanceTlcBatchPage)
    await flushPromises()
    expect(wrapper.get('[data-testid="workspace"]').attributes()).toMatchObject({
      'data-can-read': 'false', 'data-can-upload': 'false', 'data-can-create': 'false', 'data-can-cancel': 'false',
    })
    expect(wrapper.get('[data-testid="batch-read-permission-required"]').text()).toContain('读取批次或读取关联文件')
    expect(wrapper.text()).not.toContain('batch:read')
    expect(wrapper.text()).not.toContain('artifact:read')
    expect(requestMock).toHaveBeenCalledTimes(1)
    wrapper.unmount()
  })

  it('grants all four actions from allowed_actions wildcard without permission entries', async () => {
    const requestMock = vi.mocked(requestAccessContext)
    requestMock.mockResolvedValueOnce(accessContext({ permissions: [], allowed_actions: ['*'] }))
    const wrapper = mount(AcceptanceTlcBatchPage)
    await flushPromises()
    expect(wrapper.get('[data-testid="workspace"]').attributes()).toMatchObject({
      'data-can-read': 'true', 'data-can-upload': 'true', 'data-can-create': 'true', 'data-can-cancel': 'true',
    })
    wrapper.unmount()
  })

  it('does not publish a pending access response after the entry unmounts', async () => {
    const pending = deferred<AccessContextView>()
    vi.mocked(requestAccessContext).mockReturnValueOnce(pending.promise)
    const wrapper = mount(AcceptanceTlcBatchPage)
    const setupState = (wrapper.vm.$ as unknown as {
      setupState: { loadState: string; context: unknown }
    }).setupState
    expect(setupState.loadState).toBe('loading')
    expect(setupState.context).toBeNull()
    wrapper.unmount()

    pending.resolve(accessContext({ default_project_id: 'late-project', accessible_projects: [{ id: 'late-project', name: '迟到项目' }] }))
    await flushPromises()
    expect(setupState.loadState).toBe('loading')
    expect(setupState.context).toBeNull()
  })

  it('does not mount for an inaccessible project and shows an access error', async () => {
    const requestMock = vi.mocked(requestAccessContext)
    requestMock.mockResolvedValueOnce(accessContext({
      project_selection: 'no_accessible_projects', default_project_id: null, accessible_projects: [],
    }))
    const wrapper = mount(AcceptanceTlcBatchPage)
    await flushPromises()
    expect(wrapper.findAll('[data-testid="workspace"]')).toHaveLength(0)
    expect(wrapper.findAll('[data-testid="no-accessible-project"]')).toHaveLength(1)
    expect(requestMock).toHaveBeenCalledTimes(1)
    wrapper.unmount()
  })

  it('ignores an older access response after refresh and retries a failed context read', async () => {
    const first = deferred<AccessContextView>()
    const second = deferred<AccessContextView>()
    const requestMock = vi.mocked(requestAccessContext)
    requestMock.mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise)
    const wrapper = mount(AcceptanceTlcBatchPage)
    await wrapper.get('[data-testid="refresh-access-context"]').trigger('click')
    expect(wrapper.findAll('[data-testid="workspace"]')).toHaveLength(0)
    second.resolve(accessContext({ default_project_id: 'current-project', accessible_projects: [{ id: 'current-project', name: '当前项目' }] }))
    await flushPromises()
    expect(wrapper.get('[data-testid="workspace"]').attributes('data-project-id')).toBe('current-project')
    first.resolve(accessContext({ default_project_id: 'stale-project', accessible_projects: [{ id: 'stale-project', name: '旧项目' }] }))
    await flushPromises()
    expect(wrapper.get('[data-testid="workspace"]').attributes('data-project-id')).toBe('current-project')
    expect(requestMock).toHaveBeenCalledTimes(2)
    wrapper.unmount()

    requestMock.mockClear()
    requestMock.mockRejectedValueOnce(new Error('访问信息暂不可用')).mockResolvedValueOnce(accessContext())
    const retryWrapper = mount(AcceptanceTlcBatchPage)
    await flushPromises()
    expect(retryWrapper.get('[data-testid="access-context-error"]').text()).toContain('访问信息暂不可用')
    await retryWrapper.get('[data-testid="retry-access-context"]').trigger('click')
    await flushPromises()
    expect(retryWrapper.findAll('[data-testid="access-context-error"]')).toHaveLength(0)
    expect(retryWrapper.findAll('[data-testid="workspace"]')).toHaveLength(1)
    expect(requestMock).toHaveBeenCalledTimes(2)
    retryWrapper.unmount()
  })
})
