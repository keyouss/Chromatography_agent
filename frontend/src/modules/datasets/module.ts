import type { FrontendModule } from '@/app/module'
import DataCenterView from './DataCenterView.vue'

export const datasetsModule: FrontendModule = {
  id: 'datasets',
  navigation: [],
  routes: [{ path: '/data/datasets', name: 'datasets', component: DataCenterView, meta: { module: 'datasets' } }],
}
