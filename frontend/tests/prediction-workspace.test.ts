import { beforeEach, describe, expect, it } from 'vitest'
import { emptyPredictionWorkspace, predictionWorkspaceKey, readPredictionWorkspace, writePredictionWorkspace } from '@/shared/prediction/workspace'

let values: Map<string, string>
let storageBlocked = false
let storage: {
  getItem(key: string): string | null
  setItem(key: string, value: string): void
  removeItem(key: string): void
}
function createStorage() {
  return {
  getItem(key: string) { if (storageBlocked) throw new DOMException('blocked', 'SecurityError'); return values.get(key) ?? null },
  setItem(key: string, value: string) { if (storageBlocked) throw new DOMException('blocked', 'SecurityError'); values.set(key, value) },
  removeItem(key: string) { values.delete(key) },
  }
}

describe('prediction workspace persistence', () => {
  beforeEach(() => {
    values = new Map()
    storageBlocked = false
    storage = createStorage()
    Object.defineProperty(window, 'localStorage', { configurable: true, value: storage })
    Object.defineProperty(globalThis, 'localStorage', { configurable: true, value: storage })
  })

  it('isolates the same project and type by actor and tool', () => {
    const singleKey = predictionWorkspaceKey('actor-a', 'project-a', 'tlc', 'single')
    const reverseKey = predictionWorkspaceKey('actor-a', 'project-a', 'tlc', 'reverse_solvent')
    const otherActorKey = predictionWorkspaceKey('actor-b', 'project-a', 'tlc', 'single')
    writePredictionWorkspace(singleKey, { ...emptyPredictionWorkspace('actor-a', 'project-a', 'tlc', 'single'), smiles: ['CCO'] })
    writePredictionWorkspace(reverseKey, { ...emptyPredictionWorkspace('actor-a', 'project-a', 'tlc', 'reverse_solvent'), smiles: ['CCC'] })
    writePredictionWorkspace(otherActorKey, { ...emptyPredictionWorkspace('actor-b', 'project-a', 'tlc', 'single'), smiles: ['CO'] })

    expect(readPredictionWorkspace(singleKey)?.smiles).toEqual(['CCO'])
    expect(readPredictionWorkspace(reverseKey)?.smiles).toEqual(['CCC'])
    expect(readPredictionWorkspace(otherActorKey)?.smiles).toEqual(['CO'])
  })

  it('keeps a memory copy when local storage throws', () => {
    const key = predictionWorkspaceKey('actor-storage', 'project-a', 'gc', 'single')
    const value = { ...emptyPredictionWorkspace('actor-storage', 'project-a', 'gc', 'single'), form: { seriesText: 'not-a-number' } }
    storageBlocked = true

    expect(() => writePredictionWorkspace(key, value)).not.toThrow()
    expect(readPredictionWorkspace(key)).toEqual(value)
  })

  it('rejects a stored object whose identity does not match its tool key', () => {
    const key = predictionWorkspaceKey('actor-corrupt', 'project-a', 'tlc', 'single')
    storage.setItem(key, JSON.stringify({ ...emptyPredictionWorkspace('actor-corrupt', 'project-a', 'tlc', 'single'), tool: 'reverse_solvent' }))

    expect(readPredictionWorkspace(key)).toBeNull()
  })

  it('stores structure text but does not add a normalized structure reference', () => {
    const key = predictionWorkspaceKey('actor-a', 'project-a', 'hplc', 'single')
    writePredictionWorkspace(key, { ...emptyPredictionWorkspace('actor-a', 'project-a', 'hplc', 'single'), smiles: ['C[C@H](O)C'] })

    const serialized = storage.getItem(key) ?? ''
    expect(readPredictionWorkspace(key)?.smiles).toEqual(['C[C@H](O)C'])
    expect(serialized).not.toContain('structureId')
    expect(serialized).not.toContain('structure_ref')
  })
})
