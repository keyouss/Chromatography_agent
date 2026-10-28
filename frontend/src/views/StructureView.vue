<script setup lang="ts">
import { computed } from 'vue'
import { RouterLink } from 'vue-router'
import { activeProfile } from '@/app/generated/profile'
import StructureInput from '@/components/StructureInput.vue'
import { useAppStore } from '@/stores/app'
import { projectSelectionState, resolveProjectId } from '@/core/project-context'
const app = useAppStore()
const projectId = computed(() => resolveProjectId(app.accessContext, null))
const projectState = computed(() => projectSelectionState(app.accessContext, null))
const accessContextLoading = computed(() => !app.accessContext && (app.state === 'idle' || app.state === 'loading'))
const accessContextUnavailable = computed(() => !app.accessContext && app.state === 'error')
</script>

<template>
  <div class="view">
    <header class="page-header">
      <span class="eyebrow">STRUCTURE UTILITIES</span>
      <h1>实用工具</h1>
      <p>规范化、立体信息保留和绘制输入是所有业务模块共享的核心。</p>
    </header>
    <nav class="utility-nav" aria-label="实用工具入口"><RouterLink to="/structure">结构工具</RouterLink><RouterLink v-if="activeProfile.utilityFeatures.includes('tlc_reverse_solvent') && activeProfile.chromatographyTypes.includes('tlc')" to="/utilities/tlc-reverse">TLC 溶剂反推</RouterLink></nav>
    <StructureInput v-if="projectId || !app.accessContext" :project-id="projectId ?? ''" :offline-mode="!projectId" :disabled="Boolean(projectId) && app.state !== 'ready'" />
    <p v-if="accessContextLoading" class="panel placeholder" data-testid="access-context-loading" role="status" aria-live="polite">正在读取服务端访问上下文…</p>
    <p v-else-if="accessContextUnavailable" class="panel error-text" data-testid="access-context-error" role="alert">访问上下文暂不可用；可保留本地 SMILES，规范化暂不可用。请检查服务连接后重试。</p>
    <p v-else-if="projectState === 'blocked'" class="panel error-text" data-testid="no-accessible-projects" role="status">服务端没有为当前访问上下文分配可访问项目，结构输入暂不可用。</p>
    <p v-else-if="!projectId" class="panel error-text" data-testid="project-selection-required" role="status">当前访问上下文需要先选择项目，结构输入暂不可用。</p>
  </div>
</template>

<style scoped>
.utility-nav{display:flex;width:fit-content;max-width:100%;gap:8px;flex-wrap:wrap;margin:0 auto 16px;padding:4px;border:1px solid #c9dbe8;border-radius:14px;background:linear-gradient(180deg,#edf5fb 0%,#e7f0f7 100%)}
.utility-nav a{display:flex;min-height:38px;align-items:center;justify-content:center;padding:0 14px;border:1px solid transparent;border-radius:10px;color:#6a7f92;font-weight:700;text-decoration:none;transition:background .15s ease,border-color .15s ease,color .15s ease,box-shadow .15s ease}
.utility-nav a:hover:not(.router-link-active){border-color:#c8d9e7;background:#f8fbfe;color:#315d80}
.utility-nav a.router-link-active{border-color:#b9d8f2;background:var(--primary-deep);color:#fff;box-shadow:0 2px 4px rgba(0,51,102,.16)}
@media(max-width:520px){.utility-nav{width:100%}.utility-nav a{flex:1 1 auto}}
</style>
