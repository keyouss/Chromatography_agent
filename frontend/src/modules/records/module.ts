import type { FrontendModule } from '@/app/module'
import PredictionRecordsView from './PredictionRecordsView.vue'

export const recordsModule: FrontendModule = {
  id: 'records',
  navigation: [{ id: 'data', label: '数据与知识中心', module: 'records', childModules: ['records', 'datasets', 'knowledge'], path: '/data', component: PredictionRecordsView, icon: '◫', order: 20 }],
  routes: [{ path: '/data', name: 'data', component: PredictionRecordsView, meta: { module: 'records' } }],
}
