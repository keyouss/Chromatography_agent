<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import SharedKetcherCanvas from '@/components/SharedKetcherCanvas.vue'
import StructureInput from '@/components/StructureInput.vue'
import StructureWorkspace from '@/components/StructureWorkspace.vue'
import type { Structure } from '@/core/types'
import { createStructureWorkspaceContext } from '@/core/structure/workspace'

type BatchStructureType = 'tlc' | 'cc' | 'hplc' | 'gc' | 'sfc'
type PreparedValue =
  | { type: 'cc'; structures: [Structure, Structure] }
  | { type: 'gc'; structure: Structure }
  | { type: 'sfc'; structures: [Structure, Structure] }

const props = defineProps<{
  projectId: string
  type: BatchStructureType
  disabled?: boolean
}>()
const emit = defineEmits<{ prepared: [value: PreparedValue | null] }>()

const workspace = createStructureWorkspaceContext()
const first = ref<Structure | null>(null)
const second = ref<Structure | null>(null)
const copied = ref(false)
const copyError = ref('')
const visible = computed(() => props.type === 'cc' || props.type === 'gc' || props.type === 'sfc')
const pairRequired = computed(() => props.type === 'cc' || props.type === 'sfc')
const readyStructures = computed(() => {
  const firstStructure = first.value
  if (!props.projectId || !visible.value || props.disabled || !firstStructure || firstStructure.project_id !== props.projectId) return null
  if (!pairRequired.value) return props.type === 'gc' ? [firstStructure] as const : null
  const secondStructure = second.value
  if (!secondStructure || secondStructure.project_id !== props.projectId
    || (props.type === 'cc' && secondStructure.id === firstStructure.id)) return null
  return [firstStructure, secondStructure] as const
})
const csvValues = computed(() => {
  const structures = readyStructures.value
  if (!structures) return ''
  const ids = structures.map((structure) => structure.id)
  return ids.map(csvCell).join(',')
})

function csvCell(value: string) {
  return /[",\r\n]/.test(value) ? `"${value.replaceAll('"', '""')}"` : value
}

function setStructure(index: 0 | 1, structure: Structure) {
  if (props.disabled || structure.project_id !== props.projectId) return
  if (index === 0) first.value = structure
  else second.value = structure
  copied.value = false
  copyError.value = ''
  emitPrepared()
}

function invalidateStructure(index: 0 | 1) {
  if (index === 0) first.value = null
  else second.value = null
  copied.value = false
  copyError.value = ''
  emitPrepared()
}

function emitPrepared() {
  const firstStructure = first.value
  if (!props.projectId || props.disabled || !firstStructure || firstStructure.project_id !== props.projectId) { emit('prepared', null); return }
  if (props.type === 'gc') { emit('prepared', { type: 'gc', structure: firstStructure }); return }
  const secondStructure = second.value
  if ((props.type === 'cc' || props.type === 'sfc') && secondStructure?.project_id === props.projectId
    && (props.type !== 'cc' || secondStructure.id !== firstStructure.id)) {
    emit('prepared', { type: props.type, structures: [firstStructure, secondStructure] })
    return
  }
  emit('prepared', null)
}

async function copyCsvValues() {
  if (!csvValues.value) return
  try {
    await navigator.clipboard.writeText(csvValues.value)
    copied.value = true
    copyError.value = ''
  } catch {
    copied.value = false
    copyError.value = '无法访问剪贴板；请手动选中并复制上方行值。'
  }
}

watch(() => [props.projectId, props.type, props.disabled] as const, () => {
  first.value = null
  second.value = null
  copied.value = false
  copyError.value = ''
  emit('prepared', null)
})
</script>

<template>
  <section v-if="visible" class="panel batch-structure" aria-labelledby="batch-structure-title">
    <div class="section-heading">
      <div><span class="eyebrow">STRUCTURE REFERENCES</span><h3 id="batch-structure-title">准备项目结构引用</h3></div>
      <p>每个结构需由当前项目服务端规范化；这里只复制真实结构 ID，不会创建预测。</p>
    </div>
    <StructureWorkspace :context="workspace">
      <div class="structure-grid" :class="{ paired: pairRequired }">
        <StructureInput
          :key="`${projectId}:${type}:first`"
          :project-id="projectId"
          :disabled="disabled || !projectId"
          target-id="batch-structure-first"
          :label="pairRequired ? '结构 A' : '分子结构'"
          @normalized="setStructure(0, $event)"
          @invalidated="invalidateStructure(0)"
        />
        <StructureInput
          v-if="pairRequired"
          :key="`${projectId}:${type}:second`"
          :project-id="projectId"
          :disabled="disabled || !projectId"
          target-id="batch-structure-second"
          label="结构 B"
          @normalized="setStructure(1, $event)"
          @invalidated="invalidateStructure(1)"
        />
      </div>
      <SharedKetcherCanvas />
    </StructureWorkspace>
    <p v-if="type === 'cc' && first && second && first.id === second.id" class="error-text" role="alert">
      两个输入必须对应不同的项目结构。
    </p>
    <p v-if="!projectId" class="muted">选择项目后才能规范化并获取结构 ID。</p>
    <div v-if="csvValues" class="copy-row">
      <code :data-testid="`batch-structure-values-${type}`">{{ csvValues }}</code>
      <button type="button" class="secondary" :disabled="disabled" @click="copyCsvValues">{{ copied ? '已复制' : '复制 CSV 行值' }}</button>
    </div>
    <p v-if="copyError" class="error-text" role="alert">{{ copyError }}</p>
    <p v-if="pairRequired" class="muted">复制顺序对应模板中的 structure_ref_a、structure_ref_b 两列。CC 的两个结构必须不同。</p>
    <p v-else class="muted">复制值对应模板中的 structure_ref 列。</p>
  </section>
</template>

<style scoped>
.batch-structure { display: grid; gap: 12px; min-width: 0; }
.section-heading { display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; }
.section-heading h3 { margin: 2px 0 0; }
.section-heading p, .muted { margin: 0; color: var(--muted); line-height: 1.5; }
.structure-grid { display: grid; gap: 12px; min-width: 0; }
.structure-grid.paired { grid-template-columns: repeat(2, minmax(0, 1fr)); }
.copy-row { display: flex; align-items: center; justify-content: space-between; gap: 10px; min-width: 0; }
.copy-row code { overflow-wrap: anywhere; }
.error-text { margin: 0; color: #b42318; }
@media (max-width: 640px) {
  .section-heading, .copy-row { flex-direction: column; align-items: stretch; }
  .structure-grid.paired { grid-template-columns: minmax(0, 1fr); }
}
</style>
