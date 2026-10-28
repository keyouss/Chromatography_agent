import { describe, expect, it, vi } from 'vitest'
import { ketcherApiFromFrame, subscribeToKetcherChanges, type KetcherApi } from '@/core/structure/frameadapter'

function frameWith(api: KetcherApi, src = '/ketcher/index.html') {
  const frame = document.createElement('iframe')
  frame.src = src
  Object.defineProperty(frame, 'contentWindow', { configurable: true, value: { ketcher: api } })
  return frame
}

describe('same-origin Ketcher frame adapter', () => {
  it('uses standalone getSmiles/setMolecule and subscribes to the editor change event', async () => {
    let changed: (() => void) | undefined
    const api: KetcherApi = {
      editor: {
        subscribe: vi.fn((_event, handler) => { changed = handler; return handler }),
        unsubscribe: vi.fn(),
      },
      getSmiles: vi.fn().mockResolvedValue('C[C@H](O)'),
      setMolecule: vi.fn().mockResolvedValue(undefined),
    }
    const frame = frameWith(api)
    const resolved = ketcherApiFromFrame(frame)!
    const onChange = vi.fn()
    const unsubscribe = subscribeToKetcherChanges(resolved, onChange)

    expect(await resolved.getSmiles()).toBe('C[C@H](O)')
    await resolved.setMolecule('C[C@@H](O)')
    changed?.()
    unsubscribe()

    expect(api.getSmiles).toHaveBeenCalledOnce()
    expect(api.setMolecule).toHaveBeenCalledWith('C[C@@H](O)')
    expect(api.editor.subscribe).toHaveBeenCalledWith('change', expect.any(Function))
    expect(onChange).toHaveBeenCalledOnce()
    expect(api.editor.unsubscribe).toHaveBeenCalledWith('change', changed)
  })

  it('refuses cross-origin frames and safely handles a missing Ketcher API', () => {
    const crossOrigin = document.createElement('iframe')
    crossOrigin.src = 'https://example.invalid/ketcher/index.html'
    expect(ketcherApiFromFrame(crossOrigin)).toBeNull()
    expect(ketcherApiFromFrame(document.createElement('iframe'))).toBeNull()
  })
})
