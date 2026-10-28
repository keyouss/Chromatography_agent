import type { FrontendModule } from '@/app/module'
import SettingsView from '@/views/SettingsView.vue'

export const settingsModule: FrontendModule = {
  id: 'settings',
  navigation: [{ id: 'settings', label: '系统设置', module: 'settings', path: '/settings', component: SettingsView, icon: '⚙', order: 100 }],
  routes: [{ path: '/settings', name: 'settings', component: SettingsView, meta: { module: 'settings' } }],
}
