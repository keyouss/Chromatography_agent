<script setup lang="ts">
import { computed, onBeforeUnmount, watch } from 'vue'
import { useJobStore } from '@/stores/jobs'
import type { JobStatus } from '@/core/types'
import { statusLabel } from '@/core/status-labels'
const props = defineProps<{ jobId: string }>(); const store = useJobStore(); const job = computed(() => store.jobs[props.jobId]); const events = computed(() => store.events[props.jobId] ?? [])
watch(() => props.jobId, (id) => { if (id) { void store.fetchJob(id); store.subscribe(id) } }, { immediate: true }); onBeforeUnmount(() => store.close(props.jobId))
</script>
<template>
  <section class="panel job-status"><div class="panel-heading"><div><span class="eyebrow">JOB</span><h2>任务状态</h2></div><span v-if="job" class="badge" :class="job.status.toLowerCase()">{{ statusLabel(job.status) }}</span></div>
    <div v-if="store.loading[jobId]" class="placeholder">正在读取任务状态…</div><div v-else-if="store.errors[jobId]" class="error-text">{{ store.errors[jobId] }}</div><div v-else-if="!job" class="placeholder">等待任务 ID。提交后将在这里显示真实状态。</div>
    <template v-else><dl class="facts"><div><dt>任务 ID</dt><dd>{{ job.id }}</dd></div><div><dt>尝试次数</dt><dd>{{ job.attempt }}</dd></div><div><dt>更新时间</dt><dd>{{ job.updated_at }}</dd></div></dl><p v-if="job.error" class="error-text">服务端返回任务错误详情</p><div class="event-list"><div v-for="event in events.slice(-5)" :key="`${event.seq}-${event.type}`"><code>#{{ event.seq }}</code> {{ event.type }}</div></div></template>
  </section>
</template>
