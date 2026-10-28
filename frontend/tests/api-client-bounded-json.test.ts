import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiClient } from '@/core/api/client'

afterEach(() => vi.restoreAllMocks())

describe('bounded raw JSON reads', () => {
  it('reads split UTF-8 JSON without envelope unwrapping and disables redirects', async () => {
    const bytes = new TextEncoder().encode('{"label":"分子"}')
    const stream = new ReadableStream<Uint8Array>({ start(controller) {
      controller.enqueue(bytes.slice(0, 11)); controller.enqueue(bytes.slice(11)); controller.close()
    } })
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(stream))
    expect(await new ApiClient().getBoundedRawJson('/artifacts/report/download', bytes.length)).toEqual({ label: '分子' })
    expect(fetchMock.mock.calls[0]?.[1]).toMatchObject({ method: 'GET', redirect: 'error' })
  })

  it('rejects misleading or absent size headers when actual chunks exceed the limit and cancels', async () => {
    const cancel = vi.fn()
    const stream = new ReadableStream<Uint8Array>({ start(controller) {
      controller.enqueue(new Uint8Array(3)); controller.enqueue(new Uint8Array(3))
    }, cancel })
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(stream, { headers: { 'content-length': '1' } }))
    await expect(new ApiClient().getBoundedRawJson('/artifacts/report/download', 5)).rejects.toThrow('大小限制')
    expect(cancel).toHaveBeenCalledOnce()
  })

  it('cancels an in-progress read when its signal aborts', async () => {
    const cancel = vi.fn()
    const controller = new AbortController()
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(new ReadableStream({ cancel })))
    const pending = new ApiClient().getBoundedRawJson('/artifacts/report/download', 100, { signal: controller.signal })
    await Promise.resolve(); await Promise.resolve(); controller.abort()
    await expect(pending).rejects.toThrow('取消')
    expect(cancel).toHaveBeenCalledOnce()
  })

  it('does not send a request with an already aborted signal', async () => {
    const controller = new AbortController(); controller.abort()
    const fetchMock = vi.spyOn(globalThis, 'fetch')
    await expect(new ApiClient().getBoundedRawJson('/artifacts/report/download', 100, { signal: controller.signal })).rejects.toThrow('取消')
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it.each([new Uint8Array([0xff]), new TextEncoder().encode('private invalid json')])('rejects malformed UTF-8 or JSON without exposing content', async (bytes) => {
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(bytes))
    await expect(new ApiClient().getBoundedRawJson('/artifacts/report/download', 100)).rejects.toThrow('报告响应无效')
  })
})
