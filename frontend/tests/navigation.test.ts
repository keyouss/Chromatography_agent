import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { activeProfile } from '@/app/generated/profile'
import { generatedBuildId, navigationRegistry, routeRegistry } from '@/app/generated/registry'
import { navigationForModule } from '@/core/navigation'
import { DEFAULT_KETCHER_URL, resolvedKetcherUrl, sameOriginKetcherUrl } from '@/core/structure/ketcher'
import { firstAvailablePredictionType, unavailableCapabilityReason } from '@/shared/prediction/store'
import App from '@/App.vue'

vi.mock('@/stores/app', async () => {
  const { reactive } = await import('vue')
  return { useAppStore: () => reactive({
    load: vi.fn(), state: 'ready', activeProfile: { id: 'universal-v1' },
    navigationCollapsed: false, mobileNavigationOpen: false,
    toggleNavigation() { this.navigationCollapsed = !this.navigationCollapsed },
  }) }
})

describe('compile profile navigation registry', () => {
  it('contains only declared profile modules and keeps the assistant last', () => {
    expect(generatedBuildId).toBe(activeProfile.id)
    expect(navigationRegistry.every((item) => activeProfile.modules.includes(item.module))).toBe(true)
    const expectedLabels: string[] = []
    if (activeProfile.modules.includes('prediction')) expectedLabels.push('色谱预测')
    if (activeProfile.modules.includes('records') || activeProfile.modules.includes('datasets') || activeProfile.modules.includes('knowledge')) expectedLabels.push('数据与知识中心')
    if (activeProfile.modules.includes('utilities')) expectedLabels.push('实用工具')
    if (activeProfile.modules.includes('training')) expectedLabels.push('模型训练')
    if (activeProfile.modules.includes('model_center')) expectedLabels.push('模型中心')
    if (activeProfile.modules.includes('assistant')) expectedLabels.push('色谱智能助手')
    expect(navigationRegistry.map((item) => item.label)).toEqual(expect.arrayContaining(expectedLabels))
    expect(navigationRegistry.some((item) => item.label === '预测记录')).toBe(false)
    const dataCenter = navigationRegistry.find((item) => item.id === 'data')
    if (dataCenter) {
      expect(dataCenter.childModules).toEqual(expect.arrayContaining(['records', 'datasets', 'knowledge']))
      expect(navigationForModule('knowledge')).toContain(dataCenter)
    }
    const assistant = navigationRegistry.find((item) => item.id === 'assistant')
    const businessItems = navigationRegistry.filter((item) => item.id !== 'settings').sort((left, right) => left.order - right.order)
    if (assistant) expect(businessItems.at(-1)).toBe(assistant)
    const settings = navigationRegistry.find((item) => item.id === 'settings')
    if (settings) expect(settings.order).toBeGreaterThan(assistant?.order ?? 0)
  })
  it('does not register Coding, Workspace or Sandbox routes', () => {
    expect(navigationRegistry.some((item) => /coding|workspace|sandbox/i.test(item.path))).toBe(false)
  })
  it('keeps prediction routes bound to their selected chromatography type', () => {
    const predictionPaths = routeRegistry.map((route) => route.path)
    if (activeProfile.modules.includes('prediction')) {
      expect(predictionPaths).toContain('/prediction')
      for (const type of activeProfile.chromatographyTypes) expect(predictionPaths).toContain(`/prediction/${type}`)
      if (activeProfile.predictionTools.tlc?.includes('reverse_solvent')) expect(predictionPaths).toContain('/prediction/tlc/reverse')
    } else expect(predictionPaths).not.toContain('/prediction')
    if (activeProfile.utilityFeatures.includes('tlc_reverse_solvent')) expect(predictionPaths).toContain('/utilities/tlc-reverse')
    else expect(predictionPaths).not.toContain('/utilities/tlc-reverse')
  })
  it('selects the first ready type instead of the first unavailable API row', () => {
    const types = [
      { id: 'cc', health: { status: 'UNAVAILABLE' as const, reason: 'MODEL_VERIFICATION_REQUIRED', checked_at: '' } },
      { id: 'tlc', health: { status: 'READY' as const, reason: null, checked_at: '' } },
    ]
    expect(firstAvailablePredictionType(types)).toBe('tlc')
    expect(unavailableCapabilityReason(types[0].health)).toBe('MODEL_VERIFICATION_REQUIRED')
  })
  it('only accepts same-origin relative Ketcher paths', () => {
    expect(DEFAULT_KETCHER_URL).toBe('/ketcher/index.html')
    expect(resolvedKetcherUrl(undefined)).toBe('/ketcher/index.html')
    expect(resolvedKetcherUrl('https://example.invalid/ketcher')).toBe('/ketcher/index.html')
    expect(sameOriginKetcherUrl('/ketcher/index.html')).toBe('/ketcher/index.html')
    expect(sameOriginKetcherUrl('https://example.invalid/ketcher')).toBe('')
    expect(sameOriginKetcherUrl('//example.invalid/ketcher')).toBe('')
    expect(sameOriginKetcherUrl('\\\\example.invalid\\ketcher')).toBe('')
  })
  it('uses a full-width mobile content area with an accessible drawer', () => {
    const styles = readFileSync(resolve(process.cwd(), 'src/styles.css'), 'utf8')
    expect(styles).toMatch(/\.sidebar\s*\{[^}]*position:\s*fixed[^}]*inset:\s*26px auto 26px 26px[^}]*overflow-y:\s*auto/)
    expect(styles).toMatch(/\.main-content\s*\{[^}]*min-width:\s*0/)
    expect(styles).toMatch(/\.grid\s*>\s*\*\s*\{[^}]*min-width:\s*0/)
    expect(styles).toMatch(/@media\s*\(max-width:\s*800px\)[\s\S]*?\.sidebar\.mobile-open\s*\{[^}]*transform:\s*translateX\(0\)/)
    expect(styles).toMatch(/@media\s*\(max-width:\s*800px\)[\s\S]*?\.main-content\s*\{[^}]*width:\s*100%[^}]*margin-left:\s*0/)
    expect(styles).not.toMatch(/\.sidebar\s*\{[^}]*width:\s*76px/)
    expect(styles).toMatch(/\.app-shell\s*\{[^}]*margin:\s*26px[^}]*border-radius:\s*26px[^}]*box-shadow:\s*var\(--shadow\)/)
    expect(styles).toMatch(/@media\s*\(max-width:\s*520px\)[\s\S]*?\.app-shell\s*\{[^}]*margin:\s*6px/)
    expect(styles).toMatch(/\.chromatography-tabs\s*\{[^}]*max-width:\s*100%/)
    expect(styles).toMatch(/\.chromatography-tabs\s+button\.active\s*\{[^}]*background:\s*var\(--primary-deep\)/)
    expect(styles).toMatch(/\.chromatography-tabs\s+button:hover:not\(\.active\)/)
    expect(styles).toMatch(/\.chromatography-tabs\s+button:focus-visible/)
    expect(styles).not.toMatch(/body\s*\{[^}]*min-width:\s*1280px/)
    const app = readFileSync(resolve(process.cwd(), 'src/App.vue'), 'utf8')
    expect(app).toMatch(/aria-controls="business-navigation"[^>]*aria-label=/)
    expect(app).toMatch(/:inert="mobileViewport && !mobileNavOpen"/)
  })
  it('uses a full-width title band while keeping page content bounded and responsive', () => {
    const styles = readFileSync(resolve(process.cwd(), 'src/styles.css'), 'utf8')
    expect(styles).toMatch(/\.view\s*\{[^}]*width:\s*100%[^}]*max-width:\s*none[^}]*padding:\s*0\s+clamp\(18px,\s*4vw,\s*42px\)/)
    expect(styles).toMatch(/\.view\s*>\s*:not\(\.page-header\)\s*\{[^}]*max-width:\s*1200px/)
    expect(styles).toMatch(/\.page-header\s*\{[^}]*margin:\s*0\s+calc\(-1\s*\*\s*clamp\(18px,\s*4vw,\s*42px\)\)[^}]*background:\s*linear-gradient\(120deg/)
    expect(styles).toMatch(/\.page-header\s+h1\s*\{[^}]*margin:\s*0\s+0\s+4px/)
    expect(styles).toMatch(/\.ketcher-slot\s+iframe\s*\{[^}]*width:\s*100%/)
  })
  it('keeps utility subtabs capability-gated and separate from a white panel', () => {
    const view = readFileSync(resolve(process.cwd(), 'src/views/StructureView.vue'), 'utf8')
    expect(view).toMatch(/<nav class="utility-nav" aria-label="实用工具入口">/)
    expect(view).toMatch(/v-if="activeProfile\.utilityFeatures\.includes\('tlc_reverse_solvent'\)\s*&&\s*activeProfile\.chromatographyTypes\.includes\('tlc'\)"/)
    expect(view).not.toMatch(/<nav class="panel utility-nav"/)
    expect(view).toMatch(/\.utility-nav\s+a\.router-link-active\s*\{[^}]*background:var\(--primary-deep\)/)
  })
})

describe('mobile app navigation drawer', () => {
  afterEach(() => { vi.unstubAllGlobals() })

  it('collapses desktop navigation while retaining labelled destinations', async () => {
    vi.stubGlobal('matchMedia', vi.fn(() => ({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() })))
    const router = createRouter({ history: createMemoryHistory(), routes: [
      { path: '/', component: { template: '<h1>首页</h1>' } },
      ...navigationRegistry.map((item) => ({ path: item.path, component: { template: '<h1>页面</h1>' } })),
    ] })
    await router.push('/')
    await router.isReady()
    const wrapper = mount(App, { global: { plugins: [router] } })
    await wrapper.get('.desktop-nav-trigger').trigger('click')
    expect(wrapper.get('.app-shell').classes()).toContain('navigation-collapsed')
    expect(wrapper.get('.desktop-nav-trigger').attributes('aria-expanded')).toBe('false')
    expect(wrapper.get('#business-navigation a').attributes('aria-label')).toBeTruthy()
    await wrapper.get('.desktop-nav-trigger').trigger('click')
    expect(wrapper.get('.app-shell').classes()).not.toContain('navigation-collapsed')
    wrapper.unmount()
  })

  it('traps focus while open and closes accessibly on Escape, backdrop, and route change', async () => {
    const mediaQuery = { matches: true, addEventListener: vi.fn(), removeEventListener: vi.fn() } as unknown as MediaQueryList
    vi.stubGlobal('matchMedia', vi.fn(() => mediaQuery))
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/', component: { template: '<h1>首页</h1>' } },
        ...navigationRegistry.map((item) => ({ path: item.path, component: { template: '<h1>页面</h1>' }, meta: { module: item.module } })),
      ],
    })
    await router.push('/')
    await router.isReady()
    const wrapper = mount(App, { attachTo: document.body, global: { plugins: [router] } })
    const menu = wrapper.get('.mobile-nav-trigger')
    const sidebar = wrapper.get('#business-navigation')

    await menu.trigger('click')
    await wrapper.vm.$nextTick()
    expect(menu.attributes('aria-expanded')).toBe('true')
    expect(sidebar.attributes('aria-modal')).toBe('true')
    const links = sidebar.findAll('a')
    expect(document.activeElement).toBe(links[0].element)
    links[links.length - 1].element.focus()
    links[links.length - 1].element.dispatchEvent(new KeyboardEvent('keydown', { key: 'Tab', bubbles: true }))
    expect(document.activeElement).toBe(sidebar.get('.mobile-nav-close').element)

    links[0].element.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    await wrapper.vm.$nextTick()
    expect(menu.attributes('aria-expanded')).toBe('false')
    expect(document.activeElement).toBe(menu.element)

    await menu.trigger('click')
    await wrapper.find('.mobile-nav-backdrop').trigger('click')
    await wrapper.vm.$nextTick()
    expect(menu.attributes('aria-expanded')).toBe('false')
    expect(document.activeElement).toBe(menu.element)

    await menu.trigger('click')
    await sidebar.get('a[href="/settings"]').trigger('click')
    await flushPromises()
    expect(menu.attributes('aria-expanded')).toBe('false')
    expect(document.activeElement).toBe(wrapper.get('#main-content').element)

    wrapper.unmount()
  })
})
