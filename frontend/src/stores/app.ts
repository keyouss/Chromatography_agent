import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { apiClient, ApiClientError } from '@/core/api/client'
import type { AccessContextView, Capabilities, VersionInfo } from '@/core/types'
import { requestAccessContext } from '@/core/project-context'
import { activeProfile } from '@/app/generated/profile'

export type LoadState = 'idle' | 'loading' | 'ready' | 'error'
export const useAppStore = defineStore('app', () => {
  const capabilities = ref<Capabilities | null>(null); const version = ref<VersionInfo | null>(null); const accessContext = ref<AccessContextView | null>(null)
  const state = ref<LoadState>('idle'); const error = ref<string | null>(null)
  const navigationCollapsed = ref(false)
  const mobileNavigationOpen = ref(false)
  let loadFlight: Promise<void> | null = null
  function toggleNavigation() {
    if (typeof window !== 'undefined' && window.matchMedia('(max-width: 800px)').matches) mobileNavigationOpen.value = !mobileNavigationOpen.value
    else navigationCollapsed.value = !navigationCollapsed.value
  }
  const profileMismatch = computed(() => Boolean(capabilities.value && capabilities.value.build_id !== activeProfile.id))
  function load() {
    if (loadFlight) return loadFlight
    state.value = 'loading'; error.value = null
    const getBootstrap = async <T>(path: string): Promise<T> => {
      const controller = new AbortController()
      const timeout = setTimeout(() => controller.abort(), 3000)
      try { return await apiClient.get<T>(path, { signal: controller.signal }) }
      catch (reason) {
        if (controller.signal.aborted) throw new ApiClientError(0, 'BOOTSTRAP_TIMEOUT', '启动信息读取超时；本地页面仍可浏览。')
        throw reason
      } finally { clearTimeout(timeout) }
    }
    loadFlight = (async () => {
      const results = await Promise.allSettled([
        getBootstrap<Capabilities>('/system/capabilities'), getBootstrap<VersionInfo>('/system/version'), requestAccessContext<AccessContextView>(),
      ])
      const [capabilityResult, versionResult, accessResult] = results
      if (capabilityResult.status === 'fulfilled') capabilities.value = capabilityResult.value
      if (versionResult.status === 'fulfilled') version.value = versionResult.value
      if (accessResult.status === 'fulfilled') accessContext.value = accessResult.value
      const failed = results.find((result): result is PromiseRejectedResult => result.status === 'rejected')
      if (failed) { state.value = 'error'; error.value = failed.reason instanceof ApiClientError ? `${failed.reason.code}: ${failed.reason.message}` : '运行信息暂不可用；本地页面仍可浏览。' }
      else state.value = 'ready'
    })().finally(() => { loadFlight = null })
    return loadFlight
  }
  return { capabilities, version, accessContext, state, error, profileMismatch, load, activeProfile, navigationCollapsed, mobileNavigationOpen, toggleNavigation }
})
