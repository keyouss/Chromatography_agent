<script setup lang="ts">
import { computed, inject, onBeforeUnmount, ref, shallowRef, watch } from 'vue'
import { resolvedKetcherUrl } from '@/core/structure/ketcher'
import { ketcherApiFromFrame, subscribeToKetcherChanges, type KetcherApi } from '@/core/structure/frameadapter'
import { structureWorkspaceKey, type StructureWorkspaceContext } from '@/core/structure/workspace'

const providedWorkspace = inject(structureWorkspaceKey)
if (!providedWorkspace) throw new Error('SharedKetcherCanvas 必须位于 StructureWorkspace 内')
const workspace: StructureWorkspaceContext = providedWorkspace

const frame = ref<HTMLIFrameElement | null>(null)
const api = shallowRef<KetcherApi | null>(null)
const frameReady = ref(false)
const apiRetryAvailable = ref(false)
const selectedTargetId = computed(() => workspace.activeTargetId.value ?? '')
const dirty = ref(false)
const expanded = ref(false)
const applying = ref(false)
const message = ref('')
const pendingTargetId = ref<string | null>(null)
const targets = computed(() => [...workspace.targets.values()])
const activeTarget = computed(() => workspace.activeTargetId.value ? workspace.targets.get(workspace.activeTargetId.value) ?? null : null)
const ketcherUrl = resolvedKetcherUrl(import.meta.env.VITE_KETCHER_URL)
let unsubscribe: (() => void) | null = null
let loadGeneration = 0
let suppressChanges = 0
let loadTail: Promise<void> = Promise.resolve()
let frameGeneration = 0
let apiProbeTimer: number | null = null
let frameWindow: Window | null = null
const API_PROBE_DELAY_MS = 100
const API_PROBE_LIMIT = 50

function invalidateActive() {
  if (activeTarget.value) activeTarget.value.invalidate()
}

function loadTarget(targetId: string | null) {
  const generation = ++loadGeneration
  const target = targetId ? workspace.targets.get(targetId) : null
  const run = loadTail.catch(() => {}).then(async () => {
    const currentApi = api.value
    if (!currentApi || dirty.value || applying.value || generation !== loadGeneration || workspace.activeTargetId.value !== targetId) return
    try {
      suppressChanges += 1
      await currentApi.setMolecule(target?.smiles.value ?? '')
      if (generation === loadGeneration && workspace.activeTargetId.value === targetId) message.value = ''
    } catch {
      if (generation === loadGeneration) message.value = '无法将当前 SMILES 加载到绘图器，请重新打开绘图器后重试。'
    } finally {
      suppressChanges = Math.max(0, suppressChanges - 1)
    }
  })
  loadTail = run
  return run
}

function clearApiProbe() {
  if (apiProbeTimer !== null) window.clearTimeout(apiProbeTimer)
  apiProbeTimer = null
}

function clearFrameApi() {
  clearApiProbe()
  unsubscribe?.()
  unsubscribe = null
  if (frameWindow) frameWindow.removeEventListener('beforeunload', handleFrameBeforeUnload)
  frameWindow = null
  api.value = null
  frameReady.value = false
  apiRetryAvailable.value = false
}

function handleFrameBeforeUnload() {
  frameGeneration += 1
  loadGeneration += 1
  clearFrameApi()
  message.value = 'Ketcher 正在重新加载；等待新绘图器就绪后再应用。'
}

function probeFrameApi(generation: number, attempt: number) {
  if (generation !== frameGeneration || !frame.value) return
  const candidate = ketcherApiFromFrame(frame.value)
  if (candidate) {
    try {
      const stop = subscribeToKetcherChanges(candidate, () => {
        if (generation !== frameGeneration || api.value !== candidate || suppressChanges || applying.value) return
        dirty.value = true
        message.value = '绘图已修改，应用后才会进入预测。'
        invalidateActive()
      })
      if (generation !== frameGeneration) { stop(); return }
      api.value = candidate
      unsubscribe = stop
      frameReady.value = true
      apiRetryAvailable.value = false
      message.value = ''
      void loadTarget(workspace.activeTargetId.value)
      return
    } catch {
      // The React app can publish window.ketcher before editor event subscription is ready.
    }
  }
  if (attempt >= API_PROBE_LIMIT) {
    apiRetryAvailable.value = true
    message.value = '等待同源 Ketcher API 超时；可重新检测或刷新绘图器。'
    return
  }
  apiProbeTimer = window.setTimeout(() => probeFrameApi(generation, attempt + 1), API_PROBE_DELAY_MS)
}

function startFrameApiProbe() {
  clearFrameApi()
  const currentWindow = frame.value?.contentWindow
  if (!currentWindow) {
    apiRetryAvailable.value = true
    message.value = '无法连接同源 Ketcher 窗口；请刷新绘图器后重试。'
    return
  }
  frameWindow = currentWindow
  frameWindow.addEventListener('beforeunload', handleFrameBeforeUnload)
  const generation = ++frameGeneration
  message.value = '正在确认同源 Ketcher API…'
  probeFrameApi(generation, 0)
}

function handleFrameLoad() {
  startFrameApiProbe()
}

function requestTarget(targetId: string) {
  if (targetId === workspace.activeTargetId.value || applying.value) return
  message.value = ''
  if (dirty.value) {
    pendingTargetId.value = targetId
    message.value = '绘图尚未应用。请先应用当前绘图，或明确放弃后再切换目标。'
    return
  }
  workspace.activeTargetId.value = targetId
}

async function applyDrawing() {
  const targetId = workspace.activeTargetId.value
  if (!targetId) return
  const target = targetId ? workspace.targets.get(targetId) : null
  const currentApi = api.value
  if (!target || !currentApi || target.disabled.value || applying.value) return
  applying.value = true
  message.value = ''
  const projectId = target.projectId
  const capturedFrameGeneration = frameGeneration
  const rawTarget = target
  rawTarget.invalidate()
  try {
    const rawSmiles = await currentApi.getSmiles()
    if (capturedFrameGeneration !== frameGeneration || workspace.activeTargetId.value !== targetId || workspace.targets.get(targetId) !== rawTarget || rawTarget.projectId !== projectId) return
    if (rawTarget.disabled.value) throw new Error('当前结构输入已禁用，绘图未应用')
    if (typeof rawSmiles !== 'string' || !rawSmiles.trim()) throw new Error('绘图没有可应用的 SMILES')
    rawTarget.setSmiles(rawSmiles)
    const normalized = await rawTarget.normalize()
    if (capturedFrameGeneration !== frameGeneration) {
      rawTarget.invalidate()
      return
    }
    if (!normalized) throw new Error('后端校验失败，结构引用已清除')
    if (workspace.activeTargetId.value !== targetId || workspace.targets.get(targetId) !== rawTarget || rawTarget.projectId !== projectId) return
    if (rawTarget.disabled.value) { rawTarget.invalidate(); throw new Error('当前结构输入已禁用，结构引用已清除') }
    dirty.value = false
    message.value = '绘图已应用并通过后端结构校验。'
    if (pendingTargetId.value) {
      const nextId = pendingTargetId.value
      pendingTargetId.value = null
      workspace.activeTargetId.value = nextId
    }
  } catch (reason) {
    message.value = reason instanceof Error ? reason.message : '绘图导出或结构校验失败，结构引用已清除。'
  } finally {
    applying.value = false
    if (!dirty.value && workspace.activeTargetId.value !== targetId) void loadTarget(workspace.activeTargetId.value)
  }
}

function discardDrawing() {
  if (applying.value || !pendingTargetId.value) return
  const nextId = pendingTargetId.value
  pendingTargetId.value = null
  dirty.value = false
  message.value = ''
  workspace.activeTargetId.value = nextId
}

function cancelSwitch() {
  pendingTargetId.value = null
  message.value = dirty.value ? '绘图尚未应用。' : ''
}

watch(() => workspace.activeTargetId.value, (targetId) => {
  if (!targetId && !dirty.value && targets.value.length) {
    workspace.activeTargetId.value = targets.value[0]!.id
    return
  }
  void loadTarget(targetId)
})
watch(() => activeTarget.value?.smiles.value, () => {
  if (dirty.value) {
    message.value = '输入框内容已变化；应用绘图会用当前画布覆盖它。'
    return
  }
  void loadTarget(workspace.activeTargetId.value)
})
onBeforeUnmount(() => {
  frameGeneration += 1
  loadGeneration += 1
  clearFrameApi()
})
</script>

<template>
  <section class="shared-ketcher" :class="{ expanded }" data-testid="shared-ketcher">
    <div class="canvas-heading">
      <strong>Ketcher 绘图输入</strong>
      <label v-if="targets.length" class="target-picker">编辑目标
        <select :value="selectedTargetId" :disabled="applying" data-testid="ketcher-target" @change="requestTarget(($event.target as HTMLSelectElement).value)">
          <option v-for="target in targets" :key="target.id" :value="target.id">{{ target.label }}</option>
        </select>
      </label>
      <button type="button" class="ghost" :disabled="applying" @click="expanded = !expanded">{{ expanded ? '收起窗口' : '扩大窗口' }}</button>
    </div>
    <div class="frame-wrap">
      <iframe ref="frame" title="Ketcher 结构绘图器" :src="ketcherUrl" sandbox="allow-scripts allow-same-origin" :inert="applying || activeTarget?.disabled.value" :tabindex="applying || activeTarget?.disabled.value ? -1 : 0" @load="handleFrameLoad" />
      <div v-if="applying || activeTarget?.disabled.value" class="disabled-overlay">{{ applying ? '正在导出并校验结构…' : '当前结构输入已禁用' }}</div>
    </div>
    <div class="canvas-actions">
      <button type="button" class="primary" data-testid="apply-ketcher" :disabled="!frameReady || !activeTarget || activeTarget.disabled.value || applying" @click="applyDrawing">{{ applying ? '正在导出并校验…' : '应用绘图并校验' }}</button>
      <button v-if="apiRetryAvailable" type="button" class="ghost" data-testid="retry-ketcher-api" @click="startFrameApiProbe">重新检测 Ketcher API</button>
      <button v-if="pendingTargetId && dirty" type="button" class="ghost" data-testid="discard-ketcher" :disabled="applying" @click="discardDrawing">放弃绘图并切换</button>
      <button v-if="pendingTargetId && dirty" type="button" class="ghost" data-testid="cancel-ketcher-switch" :disabled="applying" @click="cancelSwitch">取消切换</button>
      <span v-if="!frameReady" class="hint">正在确认同源 Ketcher API…</span>
      <span v-else-if="dirty" class="hint">未应用绘图</span>
    </div>
    <p v-if="message" class="canvas-message" role="status">{{ message }}</p>
  </section>
</template>

<style scoped>
.shared-ketcher { margin-top: 18px; padding: 14px; border: 1px solid #d8e5ef; border-radius: 12px; background: var(--surface-soft, #f7fbfe) }
.canvas-heading, .canvas-actions { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-bottom: 10px }
.canvas-heading > strong { color: #315d80; font-size: 13px; margin-right: auto }
.target-picker { display: flex; align-items: center; gap: 8px; color: #315d80; font-size: 13px }
.target-picker select { max-width: 240px; min-height: 36px }
.frame-wrap { position: relative }
.frame-wrap iframe { display: block; width: 100%; height: clamp(480px, 68vh, 620px); border: 1px dashed #b8d0e2; border-radius: 8px; background: #fbfdff }
.disabled-overlay { position: absolute; inset: 0; display: grid; place-items: center; background: #ffffffb8; color: #52677a; font-weight: 600 }
.canvas-actions { margin: 10px 0 0 }
.hint, .canvas-message { color: #52677a; font-size: 12px; line-height: 1.5 }
.canvas-message { margin: 8px 0 0 }
.expanded { position: fixed; inset: 12px; z-index: 1000; overflow: auto; margin: 0; background: var(--surface, #fff); box-shadow: 0 18px 64px #102a4333 }
.expanded .frame-wrap iframe { height: calc(100vh - 150px); min-height: 540px }
</style>
