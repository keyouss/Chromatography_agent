import type { FrontendModuleExtension } from '@/app/module'
import PredictionView from './PredictionView.vue'

// TLC is an explicit universal-v1 extension. The profile assembler only imports this file when TLC is selected.
export const predictionTlcExtension: FrontendModuleExtension = {
  id: 'prediction.tlc',
  parent: 'prediction',
  navigation: [],
  routes: [{ path: '/prediction/tlc', name: 'prediction-tlc', component: PredictionView, meta: { module: 'prediction', chromatographicType: 'tlc' } }],
}
