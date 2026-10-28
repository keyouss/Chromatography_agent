import type { StreamEvent } from '@/core/types'
import { apiClient, createRequestId } from './client'

export interface EventStreamOptions { afterSeq?: string; lastEventId?: string; signal?: AbortSignal; onEvent: (event: StreamEvent) => void; onOpen?: () => void; onError?: (error: unknown) => void }
export class EventStreamError extends Error { constructor(public readonly status: number, message: string) { super(message); this.name = 'EventStreamError' } }

export function normalizeBusinessPath(path: string): string {
  let candidate = path.trim()
  const prefix = apiClient.baseUrl
  if (candidate.startsWith('/api/v1/')) candidate = candidate.slice('/api/v1'.length)
  else if (prefix.startsWith('/') && candidate.startsWith(`${prefix}/`)) candidate = candidate.slice(prefix.length)
  if (!candidate.startsWith('/') || candidate.startsWith('//') || candidate.includes('\\') || candidate.includes('#') || /^[a-z][a-z\d+.-]*:/i.test(candidate)) throw new Error('SSE 路径必须是同源业务路径')
  const pathname = candidate.split('?', 1)[0]
  const normalizedPathname = new URL(candidate, 'http://chromatography.local').pathname
  if (pathname !== normalizedPathname) throw new Error('SSE 路径包含非法路径跳转')
  const allowedRoutes = [/^\/jobs\/[^/]+\/events\/stream$/, /^\/agent-runs\/[^/]+\/events\/stream$/, /^\/conversations\/[^/]+\/events\/stream$/]
  if (!allowedRoutes.some((route) => route.test(pathname))) throw new Error('SSE 路径不是已登记业务事件流')
  return candidate
}

export interface ParsedSseRecord { type: string; id?: string; data: string }
export function parseSseRecord(record: string): ParsedSseRecord {
  let type = 'message'; let id: string | undefined; const dataLines: string[] = []
  for (const line of record.split(/\r?\n/)) {
    if (line.startsWith('event:')) type = line.slice(6).trim()
    else if (line.startsWith('id:')) id = line.slice(3).trim()
    else if (line.startsWith('data:')) { const value = line.slice(5); dataLines.push(value.startsWith(' ') ? value.slice(1) : value) }
  }
  return { type, id, data: dataLines.join('\n') }
}

export function createEventStream(path: string, options: EventStreamOptions): { close: () => void } {
  const controller = new AbortController()
  const signal = options.signal ? AbortSignal.any([options.signal, controller.signal]) : controller.signal
  const headers = new Headers({ Accept: 'text/event-stream', 'X-Request-Id': createRequestId() })
  if (options.lastEventId) headers.set('Last-Event-ID', options.lastEventId)
  let route: string
  try { route = normalizeBusinessPath(path) } catch (error) { options.onError?.(error); return { close: () => controller.abort() } }
  const query = options.afterSeq ? `${route.includes('?') ? '&' : '?'}after_seq=${encodeURIComponent(options.afterSeq)}` : ''
  void (async () => {
    try {
      const response = await fetch(`${apiClient.baseUrl}${route}${query}`, { headers, signal })
      if (!response.ok) throw new EventStreamError(response.status, `SSE 请求失败 (${response.status})`)
      if (!response.body) throw new EventStreamError(response.status, 'SSE 响应没有可读取内容')
      options.onOpen?.()
      const reader = response.body.getReader(); const decoder = new TextDecoder(); let buffer = ''
      while (!signal.aborted) {
        const result = await reader.read(); if (result.done) break
        buffer += decoder.decode(result.value, { stream: true })
        const records = buffer.split(/\r?\n\r?\n/); buffer = records.pop() ?? ''
        for (const record of records) {
          const parsedRecord = parseSseRecord(record)
          if (parsedRecord.data) { const parsed = JSON.parse(parsedRecord.data) as StreamEvent; parsed.type = parsed.type || parsedRecord.type; if (!parsed.seq && parsedRecord.id) parsed.seq = parsedRecord.id; options.onEvent(parsed) }
        }
      }
      if (!signal.aborted) options.onError?.(new EventStreamError(0, 'SSE 连接已结束'))
    } catch (error) { if (!signal.aborted) options.onError?.(error) }
  })()
  return { close: () => controller.abort() }
}
