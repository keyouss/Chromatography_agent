<script setup lang="ts">
import { createApp, nextTick, onBeforeUnmount, ref } from 'vue'
import { createPinia, getActivePinia, setActivePinia } from 'pinia'
import AssistantView from '@/modules/assistant/AssistantView.vue'
import { useAssistantStore } from '@/modules/assistant/store'

type EventSnapshot = {
  type: string
  key?: string
  isComposing?: boolean
  keyCode?: number
  defaultPrevented?: boolean
}

const events = ref<EventSnapshot[]>([])
const capturedMessages = ref<string[]>([])
const callCount = ref(0)
const mountPoint = ref<HTMLElement | null>(null)
let isolatedApp: ReturnType<typeof createApp> | null = null
const hostPinia = getActivePinia()

function record(event: EventSnapshot) {
  events.value.push(event)
}

function resetLog() {
  events.value = []
  capturedMessages.value = []
  callCount.value = 0
}

async function mountActualAssistant() {
  isolatedApp?.unmount()
  if (mountPoint.value) mountPoint.value.replaceChildren()
  resetLog()

  const pinia = createPinia()
  const store = useAssistantStore(pinia)
  store.projectId = 'SYNTHETIC_PROJECT'
  store.accessContext = { project_selection: 'resolved' } as never
  store.selectedConversationId = 'SYNTHETIC_CONVERSATION'
  store.conversations = [{
    id: 'SYNTHETIC_CONVERSATION', project_id: 'SYNTHETIC_PROJECT', mode: 'business',
    title: 'SYNTHETIC IME diagnostics', status: 'ACTIVE',
  }] as never

  // Keep the actual view mounted while replacing every API-capable store boundary.
  store.bootstrap = async () => undefined
  store.listDataDomains = async () => ({ data: [] }) as never
  store.listPredictionTypes = async () => []
  store.sendMessage = async (_conversationId, body) => {
    callCount.value += 1
    capturedMessages.value.push(body.content)
    return { data: {} } as never
  }

  const app = createApp(AssistantView)
  try {
    app.use(pinia)
    isolatedApp = app
    if (mountPoint.value) app.mount(mountPoint.value)
  } finally { setActivePinia(hostPinia) }
  await nextTick()
  await Promise.resolve()
}

function getInput(): HTMLTextAreaElement | null {
  return mountPoint.value?.querySelector('textarea') ?? null
}

function setSyntheticText(text: string): HTMLTextAreaElement | null {
  const input = getInput()
  if (!input) return null
  input.value = text
  input.dispatchEvent(new Event('input', { bubbles: true }))
  return input
}

function dispatchEnter(input: HTMLTextAreaElement, options: { isComposing: boolean; keyCode: number; shiftKey?: boolean }) {
  const event = new KeyboardEvent('keydown', {
    key: 'Enter', bubbles: true, cancelable: true, shiftKey: options.shiftKey ?? false,
  })
  Object.defineProperty(event, 'isComposing', { configurable: true, value: options.isComposing })
  Object.defineProperty(event, 'keyCode', { configurable: true, value: options.keyCode })
  input.dispatchEvent(event)
  record({
    type: 'keydown', key: event.key, isComposing: event.isComposing,
    keyCode: event.keyCode, defaultPrevented: event.defaultPrevented,
  })
}

async function runCompositionSequence() {
  await mountActualAssistant()
  const input = setSyntheticText('SYNTHETIC 原文输入')
  if (!input) return
  await nextTick()
  const start = new CompositionEvent('compositionstart', { bubbles: true, data: '原' })
  input.dispatchEvent(start)
  record({ type: 'compositionstart' })
  // Deliberately use false/13 so the real component's composition state is the guard under observation.
  dispatchEnter(input, { isComposing: false, keyCode: 13 })
  const end = new CompositionEvent('compositionend', { bubbles: true, data: '原' })
  input.dispatchEvent(end)
  record({ type: 'compositionend' })
  await nextTick()
}

async function runNormalEnterBoundary() {
  await mountActualAssistant()
  const input = setSyntheticText('SYNTHETIC normal Enter boundary')
  if (!input) return
  await nextTick()
  dispatchEnter(input, { isComposing: false, keyCode: 13 })
  await nextTick()
  await Promise.resolve()
}

async function runLegacyGuards() {
  await mountActualAssistant()
  const input = setSyntheticText('SYNTHETIC no-send guard probes')
  if (!input) return
  await nextTick()
  dispatchEnter(input, { isComposing: true, keyCode: 13 })
  dispatchEnter(input, { isComposing: false, keyCode: 229 })
  dispatchEnter(input, { isComposing: false, keyCode: 13, shiftKey: true })
}

onBeforeUnmount(() => {
  try { isolatedApp?.unmount() } finally { setActivePinia(hostPinia) }
})
</script>

<template>
  <section class="ime-diagnostics" aria-label="Synthetic IME diagnostics">
    <header>
      <strong>SYNTHETIC IME diagnostics · actual AssistantView</strong>
      <p>独立 Pinia；bootstrap、能力列表和 sendMessage 均为本地替身。正常 Enter 仅记录 sendMessage 边界参数，不会调用网络。</p>
    </header>
    <div class="ime-actions">
      <button type="button" @click="runCompositionSequence">执行 compositionstart → Enter → compositionend</button>
      <button type="button" @click="runLegacyGuards">检查 isComposing / 229 / Shift+Enter</button>
      <button type="button" @click="runNormalEnterBoundary">记录普通 Enter 边界（合成）</button>
    </div>
    <dl class="ime-counts">
      <div><dt>sendMessage 边界调用</dt><dd data-testid="ime-send-count">{{ callCount }}</dd></div>
      <div><dt>捕获原文</dt><dd data-testid="ime-captured-text">{{ capturedMessages.join(' | ') || '无' }}</dd></div>
    </dl>
    <ol class="ime-events" aria-label="合成事件顺序">
      <li v-for="(event, index) in events" :key="index">
        <code>{{ event.type }}</code>
        <span v-if="event.key">key={{ event.key }}</span>
        <span v-if="event.isComposing !== undefined">isComposing={{ event.isComposing }}</span>
        <span v-if="event.keyCode !== undefined">keyCode={{ event.keyCode }}</span>
        <span v-if="event.defaultPrevented !== undefined">defaultPrevented={{ event.defaultPrevented }}</span>
      </li>
    </ol>
    <p class="ime-boundary">这些是脚本派发的 DOM 事件，不代表操作系统原生 IME 或真实浏览器组合输入通过。</p>
    <div ref="mountPoint" class="ime-hidden-mount" aria-hidden="true" inert></div>
  </section>
</template>

<style scoped>
.ime-diagnostics { display: grid; gap: 10px; border: 1px solid #d3e0ea; border-radius: 10px; padding: 12px; background: #fff; color: #213d53; font: 13px/1.45 system-ui, sans-serif; }
.ime-diagnostics p { margin: 4px 0 0; color: #587187; }
.ime-actions { display: flex; flex-wrap: wrap; gap: 8px; }
.ime-actions button { border: 1px solid #b6cddd; border-radius: 7px; padding: 6px 9px; background: #fff; color: #264e6c; cursor: pointer; }
.ime-counts { display: flex; flex-wrap: wrap; gap: 14px; margin: 0; }
.ime-counts div { display: flex; gap: 6px; }
.ime-counts dt { color: #587187; }
.ime-counts dd { margin: 0; font-weight: 600; overflow-wrap: anywhere; }
.ime-events { display: grid; gap: 3px; margin: 0; padding-left: 22px; }
.ime-events li { display: flex; flex-wrap: wrap; gap: 10px; }
.ime-boundary { font-size: 12px; }
.ime-hidden-mount { position: absolute; left: -10000px; top: 0; width: 800px; height: 600px; overflow: hidden; }
</style>
