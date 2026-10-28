import type { FrontendModule } from '@/app/module'
import KnowledgeView from './KnowledgeView.vue'

export const knowledgeModule: FrontendModule = { id: 'knowledge', navigation: [], routes: [{ path: '/data/knowledge', name: 'knowledge', component: KnowledgeView, meta: { module: 'knowledge' } }] }
