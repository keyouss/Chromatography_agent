import type { FrontendModule } from '@/app/module'
import StructureView from '@/views/StructureView.vue'

export const utilitiesModule: FrontendModule = {
  id: 'utilities',
  navigation: [{ id: 'utilities', label: '实用工具', module: 'utilities', path: '/structure', component: StructureView, icon: '⌬', order: 60 }],
  routes: [{ path: '/structure', name: 'utilities', component: StructureView, meta: { module: 'utilities' } }],
}
