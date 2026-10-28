import type { FrontendModuleExtension } from '@/app/module'
import BatchPredictionPage from './BatchPredictionPage.vue'

function batchExtension(type: string): FrontendModuleExtension {
  return {
    id: `prediction.${type}.batch`,
    parent: 'prediction',
    navigation: [],
    routes: [{
      path: `/prediction/${type}/batch`,
      name: `prediction-${type}-batch`,
      component: BatchPredictionPage,
      meta: { module: 'prediction', chromatographicType: type, predictionTool: 'batch' },
    }],
  }
}

export const predictionBatchTlcExtension = batchExtension('tlc')
export const predictionBatchCcExtension = batchExtension('cc')
export const predictionBatchHplcExtension = batchExtension('hplc')
export const predictionBatchGcExtension = batchExtension('gc')
export const predictionBatchSfcExtension = batchExtension('sfc')
