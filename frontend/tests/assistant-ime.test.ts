import { createPinia, setActivePinia } from 'pinia'
import { mount, flushPromises } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import AssistantView from '@/modules/assistant/AssistantView.vue'
import { useAssistantStore } from '@/modules/assistant/store'

function dispatchEnter(input: HTMLTextAreaElement, options: { shiftKey?: boolean; isComposing?: boolean; keyCode?: number } = {}) {
  const event = new KeyboardEvent('keydown', {
    key: 'Enter', bubbles: true, cancelable: true, shiftKey: options.shiftKey ?? false,
  })
  Object.defineProperty(event, 'isComposing', { configurable: true, value: options.isComposing ?? false })
  Object.defineProperty(event, 'keyCode', { configurable: true, value: options.keyCode ?? 13 })
  input.dispatchEvent(event)
  return event
}

function mountAssistant() {
  const pinia = createPinia()
  setActivePinia(pinia)
  const store = useAssistantStore()
  vi.spyOn(store, 'bootstrap').mockResolvedValue(undefined)
  const wrapper = mount(AssistantView, { global: { plugins: [pinia] } })
  store.projectId = 'synthetic-project'
  store.accessContext = { project_selection: 'resolved' } as never
  store.selectedConversationId = 'synthetic-conversation'
  store.conversations = [{
    id: 'synthetic-conversation', project_id: 'synthetic-project', mode: 'business', title: 'SYNTHETIC', status: 'ACTIVE',
  }] as never
  return { store, wrapper }
}

afterEach(() => vi.restoreAllMocks())

describe('AssistantView synthetic IME keyboard behavior', () => {
  it('does not submit during composition state, then submits on ordinary Enter after compositionend', async () => {
    const { store, wrapper } = mountAssistant()
    const send = vi.spyOn(store, 'sendMessage').mockResolvedValue(undefined as never)
    await flushPromises()
    const input = wrapper.get('textarea').element
    input.value = 'SYNTHETIC 组合输入'
    input.dispatchEvent(new Event('input', { bubbles: true }))

    input.dispatchEvent(new CompositionEvent('compositionstart', { bubbles: true, data: '合' }))
    const composingEnter = dispatchEnter(input, { isComposing: false, keyCode: 13 })
    expect(send).not.toHaveBeenCalled()
    expect(composingEnter.defaultPrevented).toBe(false)
    expect(input.value).toBe('SYNTHETIC 组合输入')

    input.dispatchEvent(new CompositionEvent('compositionend', { bubbles: true, data: '合' }))
    const ordinaryEnter = dispatchEnter(input)
    expect(send).toHaveBeenCalledOnce()
    expect(send).toHaveBeenCalledWith('synthetic-conversation', {
      client_message_id: expect.any(String), content: 'SYNTHETIC 组合输入',
    })
    expect(ordinaryEnter.defaultPrevented).toBe(true)
    wrapper.unmount()
  })

  it('guards isComposing and legacy keyCode 229, while Shift+Enter remains a newline', async () => {
    const { store, wrapper } = mountAssistant()
    const send = vi.spyOn(store, 'sendMessage').mockResolvedValue(undefined as never)
    await flushPromises()
    const input = wrapper.get('textarea').element

    const composingFlagEnter = dispatchEnter(input, { isComposing: true })
    const legacyImeEnter = dispatchEnter(input, { keyCode: 229 })
    const shiftEnter = dispatchEnter(input, { shiftKey: true })
    expect(send).not.toHaveBeenCalled()
    expect(composingFlagEnter.defaultPrevented).toBe(false)
    expect(legacyImeEnter.defaultPrevented).toBe(false)
    expect(shiftEnter.defaultPrevented).toBe(false)
    wrapper.unmount()
  })
})
