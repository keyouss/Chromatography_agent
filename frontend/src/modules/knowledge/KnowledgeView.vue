<script setup lang="ts">
import { onMounted, ref } from 'vue'
import ConfirmationCard from '@/components/ConfirmationCard.vue'
import { useConfirmationStore } from '@/stores/confirmations'
import { useKnowledgeStore } from './store'

const store = useKnowledgeStore()
const confirmations = useConfirmationStore()
const projectId = ref('')
const query = ref('')
const topK = ref(8)
const artifactId = ref(''); const title = ref(''); const sourceDescription = ref(''); const sourceType = ref<'internal' | 'paper' | 'standard' | 'other'>('internal'); const selectedDocumentId = ref<string | null>(null)
const busy = ref(false)
const formError = ref<string | null>(null)

async function search() {
  if (!projectId.value || !query.value) return
  busy.value = true; formError.value = null
  try { await store.searchKnowledge({ project_id: projectId.value, query: query.value, top_k: topK.value }) }
  catch (reason) { formError.value = reason instanceof Error ? reason.message : '知识检索失败' }
  finally { busy.value = false }
}
onMounted(() => undefined)
const selectedDocument = () => store.documents.find((item) => item.id === selectedDocumentId.value) ?? null
function locatorText(locator: unknown) { if (!locator || typeof locator !== 'object') return String(locator ?? '无定位信息'); const value = locator as Record<string, unknown>; return [value.kind, value.page, value.paragraph, value.line, value.offset].filter((part) => part !== undefined && part !== null).join(' · ') || '有定位信息' }
async function createDocument() { if (!projectId.value || !artifactId.value || !title.value || !sourceDescription.value) return; try { const result = await store.createDocument({ project_id: projectId.value, artifact_id: artifactId.value, title: title.value, source_description: sourceDescription.value, source_type: sourceType.value }); selectedDocumentId.value = result.data.id } catch (reason) { formError.value = reason instanceof Error ? reason.message : '文档登记失败' } }
async function indexDocument() { const doc = selectedDocument(); if (!doc) return; try { await store.indexDocument(doc.id, projectId.value, doc.etag); await store.listDocuments(projectId.value) } catch (reason) { formError.value = reason instanceof Error ? reason.message : '文档索引失败' } }
async function loadCitation(id: string) { try { await store.getCitation(id, projectId.value) } catch (reason) { formError.value = reason instanceof Error ? reason.message : '引用详情暂不可用' } }
async function archiveDocument() { const doc = selectedDocument(); if (!doc || !confirmations.confirmation || !confirmations.isFor({ id: doc.id, type: 'knowledge_document' }, 'resource.archive')) return; try { await store.archiveDocument(doc.id, projectId.value, confirmations.confirmation.id); await store.listDocuments(projectId.value) } catch (reason) { formError.value = reason instanceof Error ? reason.message : '文档归档失败' } }
</script>
<template>
  <div class="view">
    <header class="page-header"><span class="eyebrow">KNOWLEDGE · FROZEN M3/M4 API</span><h1>知识检索与引用</h1><p>检索结果和引用均来自服务端；后端不可用时保留真实错误，不展示静态成功数据。</p></header>
    <section class="panel"><h2>检索</h2><div class="grid two-column"><label>项目 ID<input v-model="projectId" /></label><label>Top K<input v-model.number="topK" type="number" min="1" max="50" /></label></div><label class="wide-field">查询<textarea v-model="query" rows="3" /></label><button class="primary" :disabled="busy || !projectId || !query" @click="search">{{ busy ? '检索中…' : '调用服务端检索' }}</button><p v-if="formError" class="error-text">{{ formError }}</p></section>
    <section class="panel"><h2>检索结果</h2><div v-if="store.state === 'loading'" class="placeholder">正在读取服务端数据…</div><div v-else-if="store.state === 'error'" class="error-text">{{ store.error }}</div><div v-else-if="!store.search?.results?.length" class="placeholder">尚无服务端返回的引用。</div><div v-else class="event-list"><article v-for="item in store.search.results" :key="item.citation_id"><strong>{{ item.title || item.citation_id }}</strong><span class="muted">{{ item.citation_id }} · {{ locatorText(item.locator) }}</span><p>{{ item.snippet }}</p><button class="secondary" @click="loadCitation(item.citation_id)">读取引用详情</button></article></div><pre v-if="store.selectedCitation" class="code-block">{{ JSON.stringify(store.selectedCitation, null, 2) }}</pre></section>
    <section class="panel"><h2>知识文档</h2><button class="secondary" :disabled="!projectId || store.state === 'loading'" @click="store.listDocuments(projectId)">读取项目文档</button><div v-if="!store.documents.length" class="placeholder">服务端未返回知识文档。</div><div v-else class="event-list"><button v-for="doc in store.documents" :key="doc.id" class="secondary" @click="selectedDocumentId = doc.id">{{ doc.title }} · {{ doc.id }} · {{ doc.status }} · revision {{ doc.revision }}</button></div></section>
    <section class="panel"><h2>登记知识文档</h2><div class="grid two-column"><label>Artifact ID<input v-model="artifactId" /></label><label>标题<input v-model="title" /></label><label>来源描述<input v-model="sourceDescription" /></label><label>来源类型<select v-model="sourceType"><option value="internal">内部</option><option value="paper">论文</option><option value="standard">标准</option><option value="other">其他</option></select></label></div><button class="secondary" :disabled="!projectId || !artifactId || !title || !sourceDescription" @click="createDocument">登记服务端文档</button></section>
    <section v-if="selectedDocument()" class="panel"><h2>文档操作</h2><p class="muted">{{ selectedDocument()!.id }} · {{ selectedDocument()!.status }} · ETag {{ selectedDocument()!.etag }}</p><button class="secondary" @click="indexDocument">索引文档（空 body + If-Match）</button><ConfirmationCard :resource-ref="{ id: selectedDocument()!.id, type: 'knowledge_document' }" action="resource.archive" /><button class="primary" :disabled="!confirmations.isFor({ id: selectedDocument()!.id, type: 'knowledge_document' }, 'resource.archive')" @click="archiveDocument">归档（需确认）</button></section>
  </div>
</template>
