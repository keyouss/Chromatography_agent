import { ref } from 'vue'
import { defineStore } from 'pinia'
import { apiClient, createIdempotencyKey } from '@/core/api/client'
import { createEventStream, EventStreamError } from '@/core/api/sse'
import type { ContractCancelJobBody } from '@/core/contract-types'
import type { Job, JobStatus, StreamEvent } from '@/core/types'

export const useJobStore = defineStore('jobs', () => {
  const jobs = ref<Record<string, Job>>({}); const events = ref<Record<string, StreamEvent[]>>({}); const loading = ref<Record<string, boolean>>({}); const errors = ref<Record<string, string>>({})
  const streams = new Map<string, { close: () => void }>()
  const reconnectTimers = new Map<string, number>(); const pollTimers = new Map<string, number>()
  const terminalStatuses = new Set<JobStatus>(['SUCCEEDED', 'PARTIAL', 'FAILED', 'CANCELED', 'TIMED_OUT', 'INTERRUPTED'])
  const isTerminal = (status: JobStatus | undefined) => status !== undefined && terminalStatuses.has(status)
  function clearPolling(id: string) { const timer = pollTimers.get(id); if (timer !== undefined) window.clearInterval(timer); pollTimers.delete(id) }
  function clearReconnect(id: string) { const timer = reconnectTimers.get(id); if (timer !== undefined) window.clearTimeout(timer); reconnectTimers.delete(id) }
  function stopStream(id: string) { streams.get(id)?.close(); streams.delete(id); clearPolling(id); clearReconnect(id) }
  function ensurePolling(id: string) { if (!pollTimers.has(id)) pollTimers.set(id, window.setInterval(() => { void fetchJob(id).catch(() => undefined) }, 2000)) }
  function scheduleRecovery(id: string) {
    if (isTerminal(jobs.value[id]?.status)) { stopStream(id); return }
    ensurePolling(id)
    if (!reconnectTimers.has(id)) reconnectTimers.set(id, window.setTimeout(() => { reconnectTimers.delete(id); subscribe(id) }, 3000))
  }
  async function fetchJob(id: string) { loading.value[id] = true; try { jobs.value[id] = await apiClient.get<Job>(`/jobs/${encodeURIComponent(id)}`); if (isTerminal(jobs.value[id].status)) stopStream(id) } catch (error) { errors.value[id] = error instanceof Error ? error.message : '任务加载失败'; throw error } finally { loading.value[id] = false } }
  function subscribe(id: string) {
    clearReconnect(id); streams.get(id)?.close(); const known = events.value[id] ?? []; events.value[id] = known
    if (isTerminal(jobs.value[id]?.status)) { clearPolling(id); return { close: () => undefined } }
    const last = known.at(-1)?.seq
    const stream = createEventStream(`/jobs/${encodeURIComponent(id)}/events/stream`, { afterSeq: last, lastEventId: last, onOpen: () => { clearPolling(id); errors.value[id] = '' }, onEvent: (event) => {
      if (event.seq && events.value[id].some((existing) => existing.seq === event.seq)) return
      if (event.seq) events.value[id].push(event)
      clearPolling(id)
      const status = event.type === 'job.status_changed' && typeof event.data === 'object' && event.data && 'status' in event.data ? (event.data.status as JobStatus) : undefined
      if (status && terminalStatuses.has(status)) { void fetchJob(id).catch(() => undefined); stopStream(id) }
      else if (event.type === 'job.status_changed') void fetchJob(id).catch(() => undefined)
    }, onError: (error) => {
      errors.value[id] = error instanceof Error ? error.message : '任务事件流暂不可用'
      if (error instanceof EventStreamError && error.status === 410) { streams.get(id)?.close(); streams.delete(id); clearReconnect(id); events.value[id] = []; errors.value[id] = '事件重放窗口已过期，已回退到任务详情'; ensurePolling(id); void fetchJob(id).catch(() => undefined); return }
      scheduleRecovery(id)
    } })
    streams.set(id, stream); return stream
  }
  function close(id: string) { stopStream(id) }
  async function cancel(id: string, reason?: string) {
    const body: ContractCancelJobBody = { reason }
    return apiClient.post<Job>(`/jobs/${encodeURIComponent(id)}/cancel`, body, { idempotencyKey: createIdempotencyKey(), ifMatch: jobs.value[id]?.etag }).then((job) => { jobs.value[id] = job; if (isTerminal(job.status)) stopStream(id); return job })
  }
  return { jobs, events, loading, errors, fetchJob, subscribe, close, cancel }
})
