<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { RouterLink, RouterView, useRoute } from 'vue-router'
import { navigationRegistry } from '@/app/generated/registry'
import { useAppStore } from '@/stores/app'
const app = useAppStore()
const route = useRoute()
const mobileViewport = ref(false)
const mobileNavOpen = computed({ get: () => Boolean(app.mobileNavigationOpen), set: (value: boolean) => { app.mobileNavigationOpen = value } })
const menuButton = ref<HTMLButtonElement | null>(null)
const sidebar = ref<HTMLElement | null>(null)
const mainContent = ref<HTMLElement | null>(null)
let mobileMediaQuery: MediaQueryList | null = null
const assistantWorkspace = computed(() => route.meta.module === 'assistant')
const activeLabel = computed(() => {
  const moduleId = typeof route.meta.module === 'string' ? route.meta.module : null
  const parent = navigationRegistry.find((item) => item.path === route.path || item.id === moduleId || item.module === moduleId || item.childModules?.some((child) => child === moduleId))?.label || (route.path === '/' ? '首页' : '工作台')
  const type = typeof route.meta.chromatographicType === 'string' ? route.meta.chromatographicType.toUpperCase() : ''
  return type ? `${parent} · ${type}` : parent
})
function onMobileViewportChange(event: MediaQueryListEvent) {
  mobileViewport.value = event.matches
  if (!event.matches) mobileNavOpen.value = false
}
function focusMenuButton() { void nextTick(() => (menuButton.value ?? document.querySelector<HTMLElement>('[data-platform-navigation-trigger]'))?.focus({ preventScroll: true })) }
function openMobileNavigation() {
  mobileNavOpen.value = true
  void nextTick(() => sidebar.value?.querySelector<HTMLElement>('.nav-item')?.focus({ preventScroll: true }))
}
function closeMobileNavigation(returnFocus = false) {
  mobileNavOpen.value = false
  if (returnFocus) focusMenuButton()
}
watch(mobileNavOpen, async (open) => {
  if (open && mobileViewport.value) {
    await nextTick()
    sidebar.value?.querySelector<HTMLElement>('.nav-item')?.focus({ preventScroll: true })
  }
})
function handleNavigationKeydown(event: KeyboardEvent) {
  if (!mobileViewport.value || !mobileNavOpen.value) return
  if (event.key === 'Escape') {
    event.preventDefault()
    closeMobileNavigation(true)
    return
  }
  if (event.key !== 'Tab') return
  const focusable = sidebar.value?.querySelectorAll<HTMLElement>('a[href], button:not([disabled])')
  if (!focusable?.length) return
  const first = focusable[0]
  const last = focusable[focusable.length - 1]
  if (event.shiftKey && (document.activeElement === first || !sidebar.value?.contains(document.activeElement))) {
    event.preventDefault()
    last.focus({ preventScroll: true })
  } else if (!event.shiftKey && (document.activeElement === last || !sidebar.value?.contains(document.activeElement))) {
    event.preventDefault()
    first.focus({ preventScroll: true })
  }
}
watch(() => route.fullPath, async () => {
  if (!mobileNavOpen.value) return
  mobileNavOpen.value = false
  await nextTick()
  mainContent.value?.focus({ preventScroll: true })
})
onMounted(() => {
  void app.load()
  mobileMediaQuery = window.matchMedia('(max-width: 800px)')
  mobileViewport.value = mobileMediaQuery.matches
  mobileMediaQuery.addEventListener('change', onMobileViewportChange)
})
onBeforeUnmount(() => mobileMediaQuery?.removeEventListener('change', onMobileViewportChange))
</script>

<template>
  <a class="skip-link" href="#main-content">跳转到主要内容</a>
  <div class="app-shell" :class="{ 'navigation-collapsed': app.navigationCollapsed, 'assistant-workspace': assistantWorkspace }" @keydown="handleNavigationKeydown">
    <div v-if="mobileNavOpen" class="mobile-nav-backdrop" aria-hidden="true" @click="closeMobileNavigation(true)" />
    <aside id="business-navigation" ref="sidebar" class="sidebar" :class="{ 'mobile-open': mobileNavOpen }" :inert="mobileViewport && !mobileNavOpen" :aria-hidden="mobileViewport && !mobileNavOpen" :role="mobileViewport ? 'dialog' : undefined" :aria-modal="mobileViewport && mobileNavOpen ? 'true' : undefined" aria-label="主导航">
      <div class="brand"><span class="brand-mark" aria-hidden="true">⌬</span><div><strong>CHROMA</strong><small>INTELLIGENCE PLATFORM</small></div><button class="mobile-nav-close" type="button" aria-label="关闭导航菜单" @click="closeMobileNavigation(true)">×</button></div>
      <div class="build-chip" title="当前前端构建">构建 · {{ app.activeProfile.id }}</div>
      <p class="sidebar-section-label">工作台</p>
      <nav class="main-nav" aria-label="业务导航">
        <RouterLink v-for="item in navigationRegistry.filter((item) => item.id !== 'settings')" :key="item.id" :to="item.path" class="nav-item" :aria-label="item.label" :title="item.label"><span>{{ item.icon }}</span><span class="navigation-label">{{ item.label }}</span></RouterLink>
      </nav>
      <div class="sidebar-bottom"><RouterLink v-if="navigationRegistry.some((item) => item.id === 'settings')" to="/settings" class="nav-item" aria-label="系统设置" title="系统设置"><span>⚙</span><span class="navigation-label">系统设置</span></RouterLink><div class="health-line"><span class="status-dot" :class="app.state" /><span class="navigation-label">{{ app.state === 'loading' ? '连接中' : app.state === 'error' ? '服务不可用' : '运行上下文' }}</span></div></div>
    </aside>
    <main id="main-content" ref="mainContent" class="main-content" tabindex="-1">
      <header v-if="!assistantWorkspace" class="app-topbar">
        <div class="topbar-leading"><button ref="menuButton" class="mobile-nav-trigger" type="button" :aria-expanded="mobileNavOpen" aria-controls="business-navigation" :aria-label="mobileNavOpen ? '关闭导航菜单' : '打开导航菜单'" @click="mobileNavOpen ? closeMobileNavigation() : openMobileNavigation()"><span aria-hidden="true">☰</span>菜单</button><button class="desktop-nav-trigger" type="button" :aria-expanded="!app.navigationCollapsed" aria-controls="business-navigation" :aria-label="app.navigationCollapsed ? '展开平台导航' : '折叠平台导航'" @click="app.toggleNavigation()">☰</button><div class="breadcrumb"><span>色谱智能平台</span><span aria-hidden="true">/</span><strong>{{ activeLabel }}</strong></div></div>
        <div class="topbar-meta"><span class="runtime-pill" :class="app.state"><span class="status-dot" :class="app.state" />{{ app.state === 'loading' ? '正在连接' : app.state === 'error' ? '服务不可用' : '运行上下文' }}</span><span class="build-meta">{{ app.activeProfile.id }}</span></div>
      </header>
      <section v-if="app.state === 'error'" class="runtime-offline-banner" role="status" data-testid="runtime-offline-banner">
        <span>运行信息暂不可用；已构建导航和本地页面仍可浏览。在线服务操作不可用，提交状态需以任务记录核实。</span>
        <button type="button" class="ghost" data-testid="runtime-retry" @click="app.load">重试连接</button>
      </section>
      <RouterView />
    </main>
  </div>
</template>

<style scoped>
.runtime-offline-banner { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin: 12px 22px 0; padding: 10px 14px; border: 1px solid #d9a441; border-radius: 10px; background: #fff8e8; color: #573f12; font-size: .9rem }
.runtime-offline-banner button { flex: 0 0 auto }
@media (max-width: 640px) { .runtime-offline-banner { align-items: flex-start; flex-direction: column; margin: 10px 12px 0 } }
</style>
