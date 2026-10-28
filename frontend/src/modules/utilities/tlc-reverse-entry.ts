import type { FrontendModuleExtension } from '@/app/module'
import TlcReverseWorkspace from '@/shared/tlc-reverse/TlcReverseWorkspace.vue'

export const utilitiesTlcReverseExtension: FrontendModuleExtension = {
  id: 'utilities.tlc.reverse-solvent',
  parent: 'utilities',
  navigation: [],
  routes: [{ path: '/utilities/tlc-reverse', name: 'utilities-tlc-reverse-solvent', component: TlcReverseWorkspace, props: { entry: 'utilities' }, meta: { module: 'utilities', chromatographicType: 'tlc', predictionTool: 'reverse_solvent' } }],
}
