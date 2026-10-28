<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { useAppStore } from '@/stores/app'

const route = useRoute()
const app = useAppStore()
const labels: Record<string, string> = { prediction: '色谱预测', records: '预测记录', datasets: '色谱数据', knowledge: '知识中心', training: '模型训练', model_center: '模型中心' }
const title = computed(() => labels[String(route.meta.module)] || '业务模块')
</script>
<template><div class="view"><header class="page-header"><span class="eyebrow">MODULE STATUS</span><h1>{{ title }}</h1><p>该模块的业务表单和结果必须以服务端能力与 OpenAPI 为事实来源。</p></header><section class="panel empty-state"><div class="empty-icon">◌</div><h2>等待后端能力就绪</h2><p v-if="app.capabilities">当前页面壳已装配，尚未收到可执行的模块 schema 或模型能力。</p><p v-else>正在读取 `/api/v1/system/capabilities`。没有服务端响应时不会展示演示结果。</p></section></div></template>
