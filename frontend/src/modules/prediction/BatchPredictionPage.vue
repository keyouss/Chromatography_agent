<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import { activeProfile } from '@/app/generated/profile'
import { projectSelectionState, resolveProjectId } from '@/core/project-context'
import { useAppStore } from '@/stores/app'
import BatchWorkspace from '@/shared/batch/BatchWorkspace.vue'

type ChromatographyType = 'tlc' | 'cc' | 'hplc' | 'gc' | 'sfc'
const labels: Record<ChromatographyType, string> = {
  tlc: '薄层色谱', cc: '柱层析', hplc: '手性 HPLC', gc: '气相色谱', sfc: '超临界流体色谱',
}

const app = useAppStore()
const route = useRoute()
const router = useRouter()
const selectedProjectId = ref('')
const availableProjects = computed(() => app.accessContext?.accessible_projects ?? [])
const actorId = computed(() => app.accessContext?.actor_id ?? '')
const routeType = computed(() => typeof route.meta.chromatographicType === 'string' ? route.meta.chromatographicType : '')
const initialBatchId = computed(() => typeof route.query.batch_id === 'string' ? route.query.batch_id : '')
const chromatographyType = computed(() => routeType.value as ChromatographyType)
const projectId = computed(() => resolveProjectId(app.accessContext, selectedProjectId.value) ?? '')
const projectState = computed(() => projectSelectionState(app.accessContext, selectedProjectId.value))
const batchEnabled = computed(() => activeProfile.modules.includes('prediction')
  && activeProfile.chromatographyTypes.includes(routeType.value)
  && (activeProfile.predictionTools[routeType.value] ?? []).includes('batch'))
const allowedActions = computed(() => app.accessContext?.allowed_actions ?? [])
const permits = (...actions: string[]) => allowedActions.value.includes('*') || actions.every((action) => allowedActions.value.includes(action))
const canReadBatch = computed(() => app.state === 'ready' && permits('batch:read', 'artifact:read'))
const canUploadBatch = computed(() => app.state === 'ready' && permits('artifact:read', 'artifact:write'))
const canCreateBatch = computed(() => app.state === 'ready' && permits('batch:create', 'batch:write', 'batch:validate', 'batch:submit', 'prediction:create'))
const canCancelBatch = computed(() => app.state === 'ready' && permits('batch:cancel'))
const permissionRevision = computed(() => JSON.stringify([
  [...(app.accessContext?.permissions ?? [])].sort(),
  [...allowedActions.value].sort(),
]))
const policyRevision = computed(() => app.accessContext?.actor.policy_version ?? '')
const workspaceKey = computed(() => JSON.stringify([actorId.value, projectId.value, routeType.value, initialBatchId.value, permissionRevision.value, policyRevision.value]))
const toolLabel = computed(() => labels[chromatographyType.value] ?? routeType.value.toUpperCase())
const returnLocation = computed(() => {
  const projectQuery = projectId.value ? { project_id: projectId.value } : {}
  if (routeType.value === 'sfc' && (route.query.from_tool === 'single_column' || route.query.from_tool === 'full_column')) {
    return { path: `/prediction/${routeType.value}`, query: { ...projectQuery, tool: route.query.from_tool } }
  }
  return { path: `/prediction/${routeType.value}`, query: projectQuery }
})

watch([availableProjects, () => app.accessContext, () => route.query.project_id], ([projects, context, queryProject]) => {
  const ids = projects.map((project) => project.id)
  const querySelection = typeof queryProject === 'string' && ids.includes(queryProject) ? queryProject : ''
  if (querySelection) selectedProjectId.value = querySelection
  else if (!ids.includes(selectedProjectId.value)) selectedProjectId.value = resolveProjectId(context, null) ?? ''
}, { immediate: true })

function selectProject(value: string) {
  selectedProjectId.value = availableProjects.value.some((project) => project.id === value) ? value : ''
  const query = { ...route.query }
  if (selectedProjectId.value) query.project_id = selectedProjectId.value
  else delete query.project_id
  void router.replace({ query })
}
</script>

<template>
  <div class="view batch-prediction-page">
    <header class="page-heading">
      <div>
        <span class="eyebrow">{{ routeType.toUpperCase() }} · BATCH</span>
        <h1>{{ toolLabel }}批量预测</h1>
        <p>批量任务会按行校验并在确认后提交；刷新页面只恢复工作区指针，不会自动提交。</p>
      </div>
      <RouterLink class="secondary back-link" :to="returnLocation">返回单次预测</RouterLink>
    </header>

    <section v-if="availableProjects.length" class="panel project-selector">
      <label for="batch-project">项目</label>
      <select id="batch-project" :value="projectId" @change="selectProject(($event.target as HTMLSelectElement).value)">
        <option value="">请选择有权访问的项目</option>
        <option v-for="project in availableProjects" :key="project.id" :value="project.id">{{ project.name || project.id }}</option>
      </select>
    </section>
    <p v-if="app.state === 'idle' || app.state === 'loading'" class="panel placeholder" role="status">正在读取访问上下文…</p>
    <p v-else-if="app.state === 'error' && !app.accessContext" class="panel error-text" role="alert">访问上下文暂不可用，批量工作区等待连接后使用。</p>
    <p v-else-if="projectState === 'blocked'" class="panel error-text" role="status">当前访问上下文没有可访问项目，批量工作区不可用。</p>
    <p v-else-if="!projectId" class="panel error-text" role="status">请选择项目后再使用批量工作区。</p>
    <p v-else-if="!batchEnabled" class="panel error-text" role="status">当前构建未包含 {{ routeType.toUpperCase() }} 批量工具。</p>
    <BatchWorkspace
      v-else
      :key="workspaceKey"
      :initial-batch-id="initialBatchId"
      :actor-id="actorId"
      :project-id="projectId"
      :chromatography-type="chromatographyType"
      :can-read-batch="canReadBatch"
      :can-upload-batch="canUploadBatch"
      :can-create-batch="canCreateBatch"
      :can-cancel-batch="canCancelBatch"
      :permission-revision="permissionRevision"
      :policy-revision="policyRevision"
    />
  </div>
</template>

<style scoped>
.page-heading { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; margin-bottom: 18px }
.page-heading h1 { margin: 5px 0 8px }
.page-heading p { margin: 0; color: var(--muted, #667085) }
.back-link { flex: 0 0 auto; text-decoration: none }
.project-selector { display: flex; align-items: center; gap: 12px; margin-bottom: 16px }
.project-selector select { min-width: 220px }
@media (max-width: 640px) { .page-heading { flex-direction: column } .project-selector { align-items: stretch; flex-direction: column } }
</style>
