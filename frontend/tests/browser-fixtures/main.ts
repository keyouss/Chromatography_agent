import { navigationDiagnostics, observerDiagnostics } from './diagnostics-bootstrap'
import { createApp } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { useAppStore } from '@/stores/app'
import { usePredictionStore } from '@/shared/prediction/store'
import FixtureApp from './FixtureApp.vue'
import { installSyntheticFetch, releaseSlowModelA, setFixtureMode } from './synthetic-fetch'

type FixtureMode = 'many-ready' | 'no-ready' | 'scope-mismatch'
type FixtureApi = { setMode: (mode: FixtureMode) => void; releaseSlowModelA: typeof releaseSlowModelA; navigation: typeof navigationDiagnostics; observer: typeof observerDiagnostics }

declare global {
  interface Window { browserFixture: FixtureApi }
}

let mounted: ReturnType<typeof createApp> | null = null
let currentMode: FixtureMode = 'many-ready'

function makeRouter() {
  return createRouter({
    history: createMemoryHistory(),
    scrollBehavior: (_to, _from, savedPosition) => savedPosition ?? false,
    routes: [{ path: '/prediction/sfc', component: FixtureApp, meta: { chromatographicType: 'sfc' } }],
  })
}

async function mountFixture(mode: FixtureMode) {
  currentMode = mode
  setFixtureMode(mode)
  const pinia = createPinia()
  setActivePinia(pinia)
  const appStore = useAppStore(pinia)
  appStore.accessContext = {
    actor: { id: 'SYNTHETIC_ACTOR', auth_mode: 'deployment_default', policy_version: 'SYNTHETIC_POLICY' },
    actor_id: 'SYNTHETIC_ACTOR', auth_mode: 'deployment_default', project_selection: 'resolved',
    default_project_id: 'SYNTHETIC_PROJECT', accessible_projects: [{ id: 'SYNTHETIC_PROJECT', name: 'SYNTHETIC_PROJECT' }],
    allowed_actions: ['*'], permissions: ['*'], is_multi_user_authenticated: false,
  } as never
  appStore.state = 'ready'
  const predictionStore = usePredictionStore(pinia)
  predictionStore.selectedType = 'sfc'
  const router = makeRouter()
  await router.push('/prediction/sfc')
  await router.isReady()
  mounted = createApp(FixtureApp, { mode, switchMode: (next: FixtureMode) => void remount(next), releaseSlowModelA }).use(pinia).use(router)
  mounted.mount('#app')
}

async function remount(mode: FixtureMode) {
  releaseSlowModelA()
  mounted?.unmount()
  mounted = null
  await mountFixture(mode)
}

installSyntheticFetch()
window.browserFixture = { setMode: (mode) => void remount(mode), releaseSlowModelA, navigation: navigationDiagnostics, observer: observerDiagnostics }
void mountFixture(currentMode)
