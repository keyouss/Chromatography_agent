import type { FrontendModule } from '@/app/module'
import ModelCenterView from './ModelCenterView.vue'

export const modelCenterModule: FrontendModule = {
  id: 'model_center',
  navigation: [{ id: 'model-center', label: '模型中心', module: 'model_center', path: '/model-center', component: ModelCenterView, icon: '◇', order: 50 }],
  routes: [{ path: '/model-center', name: 'model-center', component: ModelCenterView, meta: { module: 'model_center' } }],
}
