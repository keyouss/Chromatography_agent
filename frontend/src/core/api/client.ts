import type { ApiEnvelope, ApiErrorBody } from '@/core/types'
import type { ContractPageInfo } from '@/core/contract-types'

export class ApiClientError extends Error {
  constructor(public readonly status: number, public readonly code: string, message: string, public readonly requestId?: string, public readonly fieldErrors: ApiErrorBody['error']['field_errors'] = []) {
    super(message)
    this.name = 'ApiClientError'
  }
}

export interface RequestOptions extends Omit<RequestInit, 'body'> {
  body?: unknown
  contentType?: string
  idempotencyKey?: string
  ifMatch?: string
  requestId?: string
}
export interface ApiResult<T> {
  data: T
  warnings: NonNullable<ApiEnvelope<T>['warnings']>
  requestId: string
  response: Response
  page?: ContractPageInfo
  etag?: string
  location?: string
}

function makeId(prefix: string): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') return `${prefix}-${crypto.randomUUID()}`
  return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2)}`
}

export function createRequestId(): string { return makeId('req') }
export function createIdempotencyKey(): string { return makeId('idem') }

export class ApiClient {
  readonly baseUrl: string
  constructor(baseUrl = '/api/v1') {
    const normalized = baseUrl.replace(/\/$/, '')
    if (normalized !== '/api/v1') throw new Error('浏览器 API 客户端只允许同源 /api/v1')
    this.baseUrl = normalized
  }

  private async send(path: string, options: RequestOptions = {}): Promise<{ response: Response; requestId: string }> {
    const {
      body: rawBody,
      contentType: requestContentType,
      idempotencyKey,
      ifMatch,
      requestId: suppliedRequestId,
      ...requestInit
    } = options
    const requestId = suppliedRequestId ?? createRequestId()
    const headers = new Headers(requestInit.headers)
    headers.set('Accept', headers.get('Accept') ?? 'application/json')
    headers.set('X-Request-Id', requestId)
    if (idempotencyKey) headers.set('Idempotency-Key', idempotencyKey)
    if (ifMatch) headers.set('If-Match', ifMatch)
    let body: BodyInit | undefined
    const isFormData = typeof FormData !== 'undefined' && rawBody instanceof FormData
    const isBlob = typeof Blob !== 'undefined' && rawBody instanceof Blob
    const isArrayBuffer = typeof ArrayBuffer !== 'undefined' && rawBody instanceof ArrayBuffer
    const isView = typeof ArrayBuffer !== 'undefined' && ArrayBuffer.isView(rawBody)
    if (rawBody !== undefined) {
      if (isFormData || typeof rawBody === 'string' || isBlob || isArrayBuffer || isView) body = rawBody as BodyInit
      else body = JSON.stringify(rawBody)
    }
    if (requestContentType) {
      headers.set('Content-Type', requestContentType)
    } else if (rawBody !== undefined && !isFormData && !isBlob && !isArrayBuffer && !isView) {
      headers.set('Content-Type', headers.get('Content-Type') ?? 'application/json')
    }
    let response: Response
    try {
      response = await fetch(`${this.baseUrl}${path.startsWith('/') ? path : `/${path}`}`, { ...requestInit, headers, body })
    } catch (reason) {
      if (reason instanceof TypeError) throw new ApiClientError(0, 'NETWORK_ERROR', '无法连接服务端；本地页面仍可浏览，尚未确认请求是否到达服务端。', requestId)
      throw reason
    }
    const responseRequestId = response.headers.get('X-Request-Id') ?? requestId
    if (!response.ok) {
      let payload: ApiErrorBody | undefined
      try { payload = await response.json() as ApiErrorBody } catch { /* response may be empty */ }
      const error = payload?.error
      const message = error?.message ?? (response.status >= 500
        ? `服务端暂时不可用（HTTP ${response.status}）`
        : response.statusText || '请求失败')
      throw new ApiClientError(response.status, error?.code ?? `HTTP_${response.status}`, message, responseRequestId, error?.field_errors ?? [])
    }
    return { response, requestId: responseRequestId }
  }

  async requestRaw<T>(path: string, options: RequestOptions = {}): Promise<T> {
    const { response } = await this.send(path, options)
    if (response.status === 204) return undefined as T
    return await response.json() as T
  }

  async getBoundedRawJson(path: string, maxBytes: number, options: RequestOptions = {}): Promise<unknown> {
    if (options.signal?.aborted) throw new Error('报告读取已取消。')
    if (!path.startsWith('/') || path.startsWith('//') || path.includes('\\') || !Number.isSafeInteger(maxBytes) || maxBytes < 1) {
      throw new Error('原始 JSON 读取参数无效。')
    }
    const { response } = await this.send(path, { ...options, method: 'GET', redirect: 'error', body: undefined })
    const reject = () => new Error('报告响应无效、超出大小限制或读取已取消。')
    const length = response.headers.get('content-length')
    if ((length !== null && Number(length) > maxBytes) || response.redirected || !response.body) {
      await response.body?.cancel().catch(() => undefined)
      throw reject()
    }
    const reader = response.body.getReader()
    const abortRead = () => { void reader.cancel().catch(() => undefined) }
    options.signal?.addEventListener('abort', abortRead, { once: true })
    const chunks: Uint8Array[] = []
    let size = 0
    try {
      while (true) {
        if (options.signal?.aborted) throw reject()
        const { done, value } = await reader.read()
        if (options.signal?.aborted) throw reject()
        if (done) break
        size += value.byteLength
        if (size > maxBytes) throw reject()
        chunks.push(value)
      }
      const bytes = new Uint8Array(size)
      let offset = 0
      for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength }
      return JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(bytes)) as unknown
    } catch {
      await reader.cancel().catch(() => undefined)
      throw reject()
    } finally { options.signal?.removeEventListener('abort', abortRead); reader.releaseLock() }
  }

  async requestWithMeta<T>(path: string, options: RequestOptions = {}): Promise<ApiResult<T>> {
    const { response, requestId: responseRequestId } = await this.send(path, options)
    if (response.status === 204) return { data: undefined as T, warnings: [], requestId: responseRequestId, response }
    const responseContentType = response.headers.get('content-type') ?? ''
    if (responseContentType.includes('application/json')) {
      const payload = await response.json() as ApiEnvelope<T> & { page?: ContractPageInfo }
      return {
        data: payload.data,
        warnings: payload.warnings ?? [],
        requestId: payload.request_id ?? responseRequestId,
        response,
        page: payload.page,
        etag: response.headers.get('ETag') ?? undefined,
        location: response.headers.get('Location') ?? undefined,
      }
    }
    return {
      data: await response.blob() as T,
      warnings: [],
      requestId: responseRequestId,
      response,
      etag: response.headers.get('ETag') ?? undefined,
      location: response.headers.get('Location') ?? undefined,
    }
  }
  async request<T>(path: string, options: RequestOptions = {}): Promise<T> {
    return (await this.requestWithMeta<T>(path, options)).data
  }
  getRaw<T>(path: string, options: RequestOptions = {}) { return this.requestRaw<T>(path, { ...options, method: 'GET' }) }
  get<T>(path: string, options: RequestOptions = {}) { return this.request<T>(path, { ...options, method: 'GET' }) }
  post<T>(path: string, body?: unknown, options: RequestOptions = {}) { return this.request<T>(path, { ...options, method: 'POST', body }) }
  patch<T>(path: string, body: unknown, options: RequestOptions = {}) { return this.request<T>(path, { ...options, method: 'PATCH', body }) }
}

export const apiClient = new ApiClient()
