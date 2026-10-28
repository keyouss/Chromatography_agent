import type { FrontendModule } from '@/app/module'
import TrainingView from './TrainingView.vue'

export const trainingModule: FrontendModule = {
  id: 'training',
  navigation: [{ id: 'training', label: '模型训练', module: 'training', path: '/training', component: TrainingView, icon: '◌', order: 40 }],
  routes: [{ path: '/training', name: 'training', component: TrainingView, meta: { module: 'training' } }],
}
