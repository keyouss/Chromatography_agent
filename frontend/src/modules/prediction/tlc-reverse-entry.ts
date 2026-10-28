import type { FrontendModuleExtension } from '@/app/module'
import TlcReverseWorkspace from '@/shared/tlc-reverse/TlcReverseWorkspace.vue'

export const predictionTlcReverseExtension: FrontendModuleExtension = {
  id: 'prediction.tlc.reverse-solvent',
  parent: 'prediction',
  navigation: [],
  routes: [{ path: '/prediction/tlc/reverse', name: 'prediction-tlc-reverse-solvent', component: TlcReverseWorkspace, props: { entry: 'prediction' }, meta: { module: 'prediction', chromatographicType: 'tlc', predictionTool: 'reverse_solvent' } }],
}
