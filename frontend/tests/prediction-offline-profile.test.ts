import { createPinia } from 'pinia'
import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, describe, expect, it, vi } from 'vitest'

vi.mock('@/app/generated/profile', () => ({
  activeProfile: {
    id: 'sfc-only-test', modules: ['prediction'], chromatographyTypes: ['sfc'],
    predictionTools: { sfc: ['single_column', 'full_column'] }, utilityFeatures: [], assistantModes: [],
  },
}))

import PredictionView from '@/modules/prediction/PredictionView.vue'

function envelope(data: unknown) {
  return new Response(JSON.stringify({ data, warnings: [], request_id: 'offline-profile-test' }), { status: 200, headers: { 'content-type': 'application/json' } })
}

afterEach(() => vi.unstubAllGlobals())

describe('offline profile navigation', () => {
  it('shows only locally built SFC when remote metadata lists other types', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
      const url = String(input)
      if (url.endsWith('/system/capabilities') || url.endsWith('/system/version')) throw new TypeError('Failed to fetch')
      if (url.endsWith('/system/access-context')) throw new TypeError('Failed to fetch')
      if (url.endsWith('/prediction/types')) return envelope([
        { id: 'tlc', display_name: 'TLC remote', tools: ['single'], health: { status: 'READY' } },
        { id: 'cc', display_name: 'CC remote', tools: ['single'], health: { status: 'READY' } },
        { id: 'sfc', display_name: 'SFC remote', tools: ['single_column'], health: { status: 'READY' } },
      ])
      throw new Error(`Unexpected request: ${url}`)
    })
    vi.stubGlobal('fetch', fetchMock)
    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/prediction/:type', component: PredictionView, meta: { chromatographicType: 'sfc' } }] })
    await router.push('/prediction/sfc')
    await router.isReady()
    const wrapper = mount(PredictionView, { global: { plugins: [createPinia(), router] } })
    await flushPromises()

    expect(wrapper.findAll('[role="tab"]').map((tab) => tab.text())).toEqual(['SFC remote'])
    expect(wrapper.find('[data-testid="prediction-offline-form"]').exists()).toBe(true)
    expect(fetchMock.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(false)
    wrapper.unmount()
  })
})
