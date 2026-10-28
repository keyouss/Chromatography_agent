<script setup lang="ts">
import { computed, inject, onBeforeUnmount, onMounted, provide, ref, toRef, useId, watch } from 'vue'
import { apiClient, createIdempotencyKey } from '@/core/api/client'
import type { ContractNormalizeStructureRequest } from '@/core/contract-types'
import type { Structure } from '@/core/types'
import { createStructureWorkspaceContext, structureWorkspaceKey, type StructureTargetController } from '@/core/structure/workspace'
import SharedKetcherCanvas from '@/components/SharedKetcherCanvas.vue'

const props = withDefaults(defineProps<{ projectId?: string; offlineMode?: boolean; disabled?: boolean; targetId?: string; label?: string; exampleSmiles?: string; initialSmiles?: string }>(), { projectId: '', offlineMode: false, disabled: false, initialSmiles: '' })
const emit = defineEmits<{
  normalized: [structure: Structure]
  'normalized-with-raw': [payload: { rawSmiles: string; structure: Structure }]
  invalidated: []
  'draft-change': [smiles: string]
}>()
const smiles = ref(''); const normalized = ref<Structure | null>(null); const state = ref<'idle' | 'loading' | 'ready' | 'error'>('idle'); const error = ref<string | null>(null); const copied = ref(false)
const uniqueId = useId()
const textareaId = `structure-smiles-${uniqueId}`
const generatedTargetId = `structure-${uniqueId}`
const targetId = computed(() => props.targetId?.trim() || generatedTargetId)
const targetLabel = computed(() => props.label?.trim() || '化学结构')
const parentWorkspace = inject(structureWorkspaceKey, null)
const workspace = parentWorkspace ?? createStructureWorkspaceContext()
provide(structureWorkspaceKey, workspace)
const targetDisabled = toRef(props, 'disabled')
let unregisterTarget: (() => void) | null = null
let normalizationGeneration = 0

function invalidate() {
  normalizationGeneration += 1
  normalized.value = null
  state.value = 'idle'
  error.value = null
  copied.value = false
  emit('invalidated')
}

watch(() => props.projectId, invalidate)
watch(() => props.disabled, (disabled) => {
  if (disabled && state.value === 'loading') invalidate()
})

const controller: StructureTargetController = {
  get id() { return targetId.value },
  get label() { return targetLabel.value },
  get projectId() { return props.projectId },
  disabled: targetDisabled,
  smiles,
  getSmiles: () => smiles.value,
  setSmiles(value) { smiles.value = value; invalidate(); emit('draft-change', value) },
  invalidate,
  normalize,
}

watch(() => props.initialSmiles, (value) => {
  if (typeof value === 'string' && value !== smiles.value) controller.setSmiles(value)
}, { immediate: true })

function registerTarget() {
  unregisterTarget?.()
  unregisterTarget = workspace.registerTarget(controller)
}

watch(targetId, () => { invalidate(); registerTarget() })
watch(targetLabel, registerTarget)
onMounted(registerTarget)
onBeforeUnmount(() => { normalizationGeneration += 1; unregisterTarget?.(); unregisterTarget = null })

async function normalize() {
  const value = smiles.value
  const projectId = props.projectId
  const requestTargetId = targetId.value
  if (props.offlineMode || !projectId) { invalidate(); error.value = '离线输入只保留本地 SMILES；连接恢复并取得访问上下文后才能规范化'; state.value = 'error'; return null }
  if (props.disabled) { invalidate(); error.value = '当前结构输入已禁用'; state.value = 'error'; return null }
  if (!value.trim()) { invalidate(); error.value = '请输入 SMILES'; state.value = 'error'; return null }
  const requestGeneration = ++normalizationGeneration
  normalized.value = null
  copied.value = false
  emit('invalidated')
  state.value = 'loading'; error.value = null
  try {
    const request: ContractNormalizeStructureRequest = { project_id: projectId, input_type: 'smiles', value, preserve_stereochemistry: true }
    const result = await apiClient.post<Structure>('/structures/normalize', request, { idempotencyKey: createIdempotencyKey() })
    if (normalizationGeneration !== requestGeneration || props.projectId !== projectId || targetId.value !== requestTargetId || props.disabled || smiles.value !== value) return null
    if (result.project_id !== projectId) throw new Error('服务端返回的结构不属于当前项目')
    normalized.value = result
    state.value = 'ready'
    emit('normalized', result)
    emit('normalized-with-raw', { rawSmiles: value, structure: result })
    return result
  } catch (reason) {
    if (normalizationGeneration !== requestGeneration || props.projectId !== projectId || targetId.value !== requestTargetId || props.disabled || smiles.value !== value) return null
    state.value = 'error'; error.value = reason instanceof Error ? reason.message : '规范化失败'
    return null
  }
}
async function copySmiles() { const value = normalized.value?.canonical_smiles || smiles.value; if (!value) return; await navigator.clipboard.writeText(value); copied.value = true; window.setTimeout(() => { copied.value = false }, 1400) }
function fillExample() { if (props.disabled || !props.exampleSmiles) return; smiles.value = props.exampleSmiles; invalidate(); emit('draft-change', smiles.value) }
</script>

<template>
  <section class="panel structure-input">
    <div class="panel-heading"><div><span class="eyebrow">STRUCTURE CORE</span><h2>化学结构输入</h2></div><span class="status-dot" :class="state" /></div>
    <div class="smiles-label"><label :for="textareaId">{{ label || 'SMILES（保留立体信息）' }}</label><button v-if="exampleSmiles" type="button" class="ghost" :disabled="disabled" data-testid="fill-example" @click="fillExample">填入示例分子</button></div>
    <textarea :id="textareaId" :data-testid="offlineMode ? (targetId === 'offline-secondary' ? 'prediction-offline-smiles-secondary' : 'prediction-offline-smiles') : undefined" v-model="smiles" :disabled="disabled" rows="3" placeholder="例如：C[C@H](O)c1ccc(cc1)C#N" @input="invalidate(); emit('draft-change', smiles)" />
    <div class="inline-actions"><button v-if="!offlineMode" class="primary" :disabled="disabled || !projectId || state === 'loading'" @click="normalize">{{ state === 'loading' ? '规范化中…' : projectId ? '规范化并校验' : '等待访问上下文' }}</button><button class="ghost" :disabled="!smiles && !normalized" @click="copySmiles">{{ copied ? '已复制' : '复制 SMILES' }}</button></div>
    <p v-if="offlineMode" class="muted" data-testid="offline-structure-status">离线本地输入：不会规范化、创建结构引用或调用服务端。</p>
    <p v-if="error" class="error-text">{{ error }}</p>
    <div v-if="normalized" class="result-box"><span class="eyebrow">SERVER RESULT</span><code>{{ normalized.canonical_smiles }}</code><p>结构 ID：{{ normalized.id }} · 立体信息：{{ normalized.stereochemistry_status }}</p></div>
    <SharedKetcherCanvas v-if="!parentWorkspace && !offlineMode" />
  </section>
</template>

<style scoped>
.smiles-label { display: flex; align-items: center; justify-content: space-between; gap: 10px; margin-bottom: 6px }
</style>
