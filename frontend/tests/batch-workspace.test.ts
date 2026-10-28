import { beforeEach, describe, expect, it, vi } from 'vitest'
import { batchWorkspaceKey, readBatchWorkspace, writeBatchWorkspace, type BatchWorkspace } from '../src/shared/batch/batch-workspace'

beforeEach(() => {
  vi.restoreAllMocks()
  const values = new Map<string, string>()
  Object.defineProperty(globalThis, 'localStorage', { configurable: true, value: {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => { values.set(key, value) },
    removeItem: (key: string) => { values.delete(key) },
  } })
})

describe('batch workspace persistence', () => {
  it('isolates actor, project, type, and tool keys while restoring only local draft pointers', () => {
    const key = batchWorkspaceKey('actor-a', 'project-a', 'gc', 'batch')
    const value: BatchWorkspace = {
      version: 1,
      actorId: 'actor-a',
      projectId: 'project-a',
      type: 'gc',
      tool: 'batch',
      formDraft: { name: 'editable input', resource_budget: { max_evaluations: 12 } },
      draftId: 'draft-1',
      acceptedId: 'batch-1',
      artifactId: 'artifact-1',
      submissionLocked: false,
    }

    writeBatchWorkspace(key, value)

    expect(readBatchWorkspace(key)).toEqual(value)
    expect(readBatchWorkspace(batchWorkspaceKey('actor-b', 'project-a', 'gc', 'batch'))).toBeNull()
    expect(readBatchWorkspace(batchWorkspaceKey('actor-a', 'project-b', 'gc', 'batch'))).toBeNull()
    expect(readBatchWorkspace(batchWorkspaceKey('actor-a', 'project-a', 'cc', 'batch'))).toBeNull()
    const stored = JSON.parse(localStorage.getItem(key)!) as Record<string, unknown>
    expect(stored).not.toHaveProperty('result')
    expect(stored).not.toHaveProperty('validation')
  })

  it('keeps the workspace in memory when local storage writes fail', () => {
    const key = batchWorkspaceKey('actor-fallback', 'project-a', 'tlc', 'batch')
    const workspace: BatchWorkspace = {
      version: 1, actorId: 'actor-fallback', projectId: 'project-a', type: 'tlc', tool: 'batch',
      formDraft: { rows: [{ smiles: 'CCO' }] }, submissionLocked: true,
    }
    vi.spyOn(localStorage, 'setItem').mockImplementation(() => { throw new Error('storage disabled') })

    expect(() => writeBatchWorkspace(key, workspace)).not.toThrow()
    expect(readBatchWorkspace(key)).toEqual(workspace)
  })

  it('rejects mismatched keys and clears the pointer without any network work', () => {
    const key = batchWorkspaceKey('actor-a', 'project-a', 'sfc', 'batch')
    const wrongOwner: BatchWorkspace = {
      version: 1, actorId: 'actor-b', projectId: 'project-a', type: 'sfc', tool: 'batch', draftId: 'draft-1',
    }
    expect(() => writeBatchWorkspace(key, wrongOwner)).toThrow('键不匹配')

    writeBatchWorkspace(key, { ...wrongOwner, actorId: 'actor-a' })
    writeBatchWorkspace(key, null)
    expect(readBatchWorkspace(key)).toBeNull()
  })
})
