import type { FrontendModule } from '@/app/module'
import PredictionView from './PredictionView.vue'

export const predictionModule: FrontendModule = {
  id: 'prediction',
  navigation: [{ id: 'prediction', label: '色谱预测', module: 'prediction', path: '/prediction', component: PredictionView, icon: '⌁', order: 10 }],
  routes: [
    { path: '/prediction', name: 'prediction', component: PredictionView, meta: { module: 'prediction' } },
    ...(['cc', 'hplc', 'gc', 'sfc'] as const).map((chromatographicType) => ({ path: `/prediction/${chromatographicType}`, name: `prediction-${chromatographicType}`, component: PredictionView, meta: { module: 'prediction', chromatographicType } })),
  ],
}
