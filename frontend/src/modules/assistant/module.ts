import type { FrontendModule } from '@/app/module'
import AssistantView from './AssistantView.vue'

export const assistantModule: FrontendModule = {
  id: 'assistant',
  navigation: [{ id: 'assistant', label: '色谱智能助手', module: 'assistant', path: '/assistant', component: AssistantView, icon: '✦', order: 70 }],
  routes: [{ path: '/assistant', name: 'assistant', component: AssistantView, meta: { module: 'assistant' } }],
}
