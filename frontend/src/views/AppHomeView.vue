<script setup lang="ts">
import { computed } from 'vue'
import StructureInput from '@/components/StructureInput.vue'
import { useAppStore } from '@/stores/app'
import { resolveProjectId } from '@/core/project-context'
const app = useAppStore()
const projectId = computed(() => resolveProjectId(app.accessContext, null))
</script>
<template><div class="view home-view"><header class="hero"><span class="eyebrow">CHROMATOGRAPHY INTELLIGENCE PLATFORM</span><h1>从结构开始，连接可追溯的色谱工作流。</h1><p>当前构建：{{ app.activeProfile.id }}。业务能力会根据服务端真实健康状态显示。</p><p v-if="app.profileMismatch" class="error-text">服务端 build_id 与当前前端构建不一致，业务模块已暂停，等待部署配置修正。</p></header><div class="grid two-column"><StructureInput :project-id="projectId ?? ''" :offline-mode="!projectId" :disabled="Boolean(app.accessContext && !projectId) || (Boolean(projectId) && app.state !== 'ready')" /><section class="panel status-panel"><span class="eyebrow">RUNTIME STATUS</span><h2>运行上下文</h2><div v-if="app.state === 'loading'" class="placeholder">加载能力、版本和访问上下文…</div><div v-else-if="app.state === 'error'" class="error-text">{{ app.error }}</div><dl v-else class="facts"><div><dt>构建</dt><dd>{{ app.capabilities?.build_id || '未知' }}</dd></div><div><dt>访问模式</dt><dd>{{ app.accessContext?.auth_mode || '未知' }}</dd></div><div><dt>服务端状态</dt><dd>{{ app.capabilities ? '已返回能力清单' : '等待连接' }}</dd></div></dl></section></div></div></template>
