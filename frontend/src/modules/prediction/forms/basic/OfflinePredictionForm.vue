<script setup lang="ts">
import { computed, reactive } from 'vue'
import StructureInput from '@/components/StructureInput.vue'

const props = defineProps<{ type: string; offline: boolean }>()
const drafts = reactive([false, false])
const labels: Record<string, string> = { tlc: '薄层色谱', cc: '柱层析', hplc: '手性 HPLC', gc: '气相色谱', sfc: '超临界流体色谱' }
const pairRequired = computed(() => ['cc', 'hplc', 'sfc'].includes(props.type))
const visible = computed(() => props.offline || drafts.some(Boolean))
function markDraft(index: number, value: string) { drafts[index] = Boolean(value) }
</script>

<template>
  <section v-if="visible" class="panel offline-prediction" data-testid="prediction-offline-form">
    <div class="section-heading"><div><span class="eyebrow">LOCAL PAGE PREVIEW</span><h2>{{ labels[type] ?? type.toUpperCase() }}{{ offline ? ' · 离线浏览' : ' · 本地草稿' }}</h2></div></div>
    <p v-if="offline" class="warning-text">页面来自当前已构建 profile。离线时只保留本地文本；没有项目上下文、服务端 schema、模型候选或预测服务。</p>
    <p v-else class="muted">连接已恢复。离线 SMILES 仍保留在本页，可复制后自行填入在线表单；不会自动规范化或提交。</p>
    <div class="grid" :class="{ 'two-column': pairRequired }">
      <StructureInput project-id="" :offline-mode="true" target-id="offline-primary" :label="pairRequired ? '离线结构 1 · SMILES' : '离线分子结构 · SMILES'" @draft-change="markDraft(0, $event)" />
      <StructureInput v-show="pairRequired" project-id="" :offline-mode="true" target-id="offline-secondary" label="离线结构 2 · SMILES" @draft-change="markDraft(1, $event)" />
    </div>
    <div class="offline-schema" data-testid="prediction-offline-schema">
      <strong>模型与输入条件</strong>
      <p>能力 schema 未读取；模型、条件字段、选项和范围均未知。这里不展示候选值。</p>
    </div>
    <p v-if="offline" class="muted">结构规范化、模型选择、提交和结果读取均不可用。</p>
  </section>
</template>

<style scoped>
.offline-prediction { display: grid; gap: 12px }
.offline-prediction .section-heading { margin-bottom: 0 }
.offline-schema { padding: 12px 14px; border: 1px dashed var(--border-color, #d5dbe3); border-radius: 8px; background: var(--surface-muted, #f7f8fa) }
.offline-schema p { margin: 5px 0 0 }
</style>
