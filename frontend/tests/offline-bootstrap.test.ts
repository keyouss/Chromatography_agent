import { afterEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useAppStore } from '@/stores/app'

function envelope(data: unknown) {
  return new Response(JSON.stringify({ data, warnings: [], request_id: 'offline-bootstrap' }), { status: 200, headers: { 'content-type': 'application/json' } })
}

describe('offline bootstrap retry', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('keeps the local app responsive on a failed bootstrap and deduplicates a successful retry', async () => {
    setActivePinia(createPinia())
    let online = false
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input)
      if (!online) throw new TypeError('Failed to fetch')
      if (url.endsWith('/system/capabilities')) return envelope({ build_id: 'local-profile', capabilities: [] })
      if (url.endsWith('/system/version')) return envelope({ build_id: 'local-profile' })
      if (url.endsWith('/system/access-context')) return envelope({ accessible_projects: [], project_selection: 'no_accessible_projects' })
      throw new Error(`Unexpected bootstrap request: ${url}`)
    })
    vi.stubGlobal('fetch', fetchMock)
    const app = useAppStore()

    await app.load()
    expect(app.state).toBe('error')
    expect(fetchMock).toHaveBeenCalledTimes(3)

    online = true
    const firstRetry = app.load()
    const duplicateRetry = app.load()
    // Pinia wraps action promises; request count below verifies the shared flight.
    await Promise.all([firstRetry, duplicateRetry])
    expect(app.state).toBe('ready')
    expect(fetchMock).toHaveBeenCalledTimes(6)
  })
})
