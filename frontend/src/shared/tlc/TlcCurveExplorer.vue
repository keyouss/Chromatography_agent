<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import {
  clampRf,
  curveRange,
  estimateLabel,
  estimateRatio,
  isValidRf,
  pointerYFromRf,
  rfFromPointer,
  type CurvePoint,
} from './interaction'

const CANVAS_HEIGHT = 330
const PLOT = { left: 48, right: 18, top: 18, bottom: 48 }
const PLATE = { left: 18, right: 18, front: 48, baseline: CANVAS_HEIGHT - 48 }

const props = withDefaults(defineProps<{
  points: readonly CurvePoint[]
  solventA: string
  solventB: string
  initialRf?: number
}>(), { initialRf: 0.5 })

const emit = defineEmits<{ change: [rf: number] }>()
const curveCanvas = ref<HTMLCanvasElement | null>(null)
const plateCanvas = ref<HTMLCanvasElement | null>(null)
const currentRf = ref(clampRf(props.initialRf))
const range = computed(() => curveRange(props.points))
const estimate = computed(() => estimateRatio(currentRf.value, props.points))
const failures = computed(() => props.points.filter((point) => !isValidRf(point.rf)).length)
let resizeObserver: ResizeObserver | null = null
let windowResizeHandler: (() => void) | null = null
let dragging = false

function setCurrentRf(value: number, notify: boolean): void {
  const next = clampRf(value, range.value ?? undefined)
  if (Object.is(currentRf.value, next)) return
  currentRf.value = next
  if (notify) emit('change', next)
}

function canvasContext(canvas: HTMLCanvasElement, height: number): { context: CanvasRenderingContext2D; width: number; height: number } | null {
  const rectangle = canvas.getBoundingClientRect()
  // A hidden/unmounted canvas can report zero dimensions. Use a deterministic
  // logical fallback so an empty state never writes NaN/Infinity to Canvas.
  const width = Math.max(240, Math.round(rectangle.width || canvas.clientWidth || 640))
  const dpr = typeof window !== 'undefined' && Number.isFinite(window.devicePixelRatio) && window.devicePixelRatio > 0
    ? window.devicePixelRatio
    : 1
  canvas.width = Math.max(1, Math.round(width * dpr))
  canvas.height = Math.max(1, Math.round(height * dpr))
  canvas.style.height = `${height}px`
  const context = canvas.getContext('2d')
  if (!context) return null
  context.setTransform(dpr, 0, 0, dpr, 0, 0)
  context.clearRect(0, 0, width, height)
  return { context, width, height }
}

function xDomain(points: readonly CurvePoint[]): { min: number; max: number } {
  const values = points.filter((point) => Number.isFinite(point.x)).map((point) => point.x)
  if (!values.length) return { min: 0, max: 1 }
  const min = Math.min(...values)
  const max = Math.max(...values)
  return min === max ? { min: min - 0.5, max: max + 0.5 } : { min, max }
}

function drawCurve(): void {
  const canvas = curveCanvas.value
  if (!canvas) return
  const setup = canvasContext(canvas, CANVAS_HEIGHT)
  if (!setup) return
  const { context, width, height } = setup
  const bounds = { left: PLOT.left, right: width - PLOT.right, top: PLOT.top, bottom: height - PLOT.bottom }
  const plotWidth = Math.max(1, bounds.right - bounds.left)
  const plotHeight = Math.max(1, bounds.bottom - bounds.top)
  const sorted = [...props.points]
    .filter((point) => Number.isFinite(point.x))
    .sort((left, right) => (left.series ?? '').localeCompare(right.series ?? '') || left.x - right.x)
  const domain = xDomain(sorted)
  const x = (value: number) => bounds.left + ((value - domain.min) / (domain.max - domain.min)) * plotWidth
  const y = (value: number) => bounds.bottom - clampRf(value) * plotHeight

  context.strokeStyle = '#e2e9e7'
  context.lineWidth = 1
  for (let step = 0; step <= 5; step += 1) {
    const gridY = y(step / 5)
    context.beginPath(); context.moveTo(bounds.left, gridY); context.lineTo(bounds.right, gridY); context.stroke()
  }
  context.strokeStyle = '#9aa9a4'
  context.lineWidth = 1.4
  context.beginPath(); context.moveTo(bounds.left, bounds.top); context.lineTo(bounds.left, bounds.bottom); context.lineTo(bounds.right, bounds.bottom); context.stroke()
  context.fillStyle = '#586b65'
  context.font = '12px system-ui'
  context.textAlign = 'right'
  context.textBaseline = 'middle'
  for (let step = 0; step <= 5; step += 1) context.fillText((step / 5).toFixed(1), bounds.left - 7, y(step / 5))
  context.textAlign = 'center'
  context.textBaseline = 'bottom'
  context.fillText(`${props.solventA} : ${props.solventB}`, (bounds.left + bounds.right) / 2, height - 5)

  context.strokeStyle = '#0f6e56'
  context.lineWidth = 2.5
  let previous: CurvePoint | null = null
  for (const point of sorted) {
    if (!isValidRf(point.rf)) {
      // A failed model response is a hard break. Never draw or interpolate
      // through it, even when successful points exist on both sides.
      previous = null
      if (Number.isFinite(point.x)) {
        const failedX = x(point.x)
        context.strokeStyle = '#c66b5d'
        context.lineWidth = 1.5
        context.beginPath(); context.moveTo(failedX - 4, bounds.bottom + 7); context.lineTo(failedX + 4, bounds.bottom + 15); context.moveTo(failedX + 4, bounds.bottom + 7); context.lineTo(failedX - 4, bounds.bottom + 15); context.stroke()
        context.strokeStyle = '#0f6e56'
        context.lineWidth = 2.5
      }
      continue
    }
    if (previous && isValidRf(previous.rf) && previous.series === point.series && point.connectFromPrevious !== false) {
      context.beginPath(); context.moveTo(x(previous.x), y(previous.rf)); context.lineTo(x(point.x), y(point.rf)); context.stroke()
    }
    context.fillStyle = '#0f6e56'
    context.beginPath(); context.arc(x(point.x), y(point.rf), 3.5, 0, Math.PI * 2); context.fill()
    previous = point
  }

  const selectedRf = clampRf(currentRf.value, range.value ?? undefined)
  context.strokeStyle = '#df7e2f'
  context.setLineDash([6, 4])
  context.lineWidth = 1.5
  context.beginPath(); context.moveTo(bounds.left, y(selectedRf)); context.lineTo(bounds.right, y(selectedRf)); context.stroke()
  context.setLineDash([])
  for (const candidate of estimate.value.candidates) {
    context.fillStyle = '#df7e2f'
    context.beginPath(); context.arc(x(candidate.x), y(selectedRf), 5, 0, Math.PI * 2); context.fill()
  }
}

function drawPlate(): void {
  const canvas = plateCanvas.value
  if (!canvas) return
  const setup = canvasContext(canvas, CANVAS_HEIGHT)
  if (!setup) return
  const { context, width, height } = setup
  const left = PLATE.left
  const right = Math.max(left + 1, width - PLATE.right)
  const front = PLATE.front
  const baseline = height - (CANVAS_HEIGHT - PLATE.baseline)
  context.fillStyle = '#f5f1e7'
  context.fillRect(left, 14, right - left, Math.max(1, height - 28))
  if (range.value && range.value.max - range.value.min < 0.999) {
    const top = pointerYFromRf(range.value.max, front, baseline)
    const bottom = pointerYFromRf(range.value.min, front, baseline)
    context.fillStyle = 'rgba(15,110,86,.1)'
    context.fillRect(left, top, right - left, Math.max(0, bottom - top))
  }
  context.strokeStyle = '#97a49f'
  context.setLineDash([5, 4])
  context.beginPath(); context.moveTo(left, front); context.lineTo(right, front); context.stroke()
  context.setLineDash([])
  context.strokeStyle = '#0f6e56'
  context.lineWidth = 2
  context.beginPath(); context.moveTo(left, baseline); context.lineTo(right, baseline); context.stroke()
  const selectedRf = clampRf(currentRf.value, range.value ?? undefined)
  const spotY = pointerYFromRf(selectedRf, front, baseline)
  const gradient = context.createRadialGradient(width / 2, spotY, 1, width / 2, spotY, 10)
  gradient.addColorStop(0, '#0f6e56'); gradient.addColorStop(1, 'rgba(15,110,86,.45)')
  context.fillStyle = gradient
  context.beginPath(); context.arc(width / 2, spotY, 9, 0, Math.PI * 2); context.fill()
}

function updateFromPointer(event: PointerEvent): void {
  const canvas = plateCanvas.value
  if (!canvas) return
  const rectangle = canvas.getBoundingClientRect()
  if (!Number.isFinite(rectangle.top) || rectangle.height <= 0) return
  const logicalY = PLATE.front + ((event.clientY - rectangle.top) / rectangle.height) * (CANVAS_HEIGHT - PLATE.front - (CANVAS_HEIGHT - PLATE.baseline))
  setCurrentRf(rfFromPointer(logicalY, PLATE.front, PLATE.baseline, range.value ?? undefined), true)
}

function pointerDown(event: PointerEvent): void {
  if (event.button !== 0) return
  dragging = true
  try { plateCanvas.value?.setPointerCapture(event.pointerId) } catch { /* unsupported in some test DOMs */ }
  updateFromPointer(event)
}

function pointerMove(event: PointerEvent): void {
  if (dragging) updateFromPointer(event)
}

function pointerUp(): void { dragging = false }
function redraw(): void { drawCurve(); drawPlate() }

watch(range, () => setCurrentRf(currentRf.value, true), { immediate: true })
watch(() => props.initialRf, (next) => setCurrentRf(next, false))
watch([() => props.points, currentRf], () => void nextTick(redraw), { deep: true })

onMounted(() => {
  redraw()
  if (typeof ResizeObserver !== 'undefined') {
    resizeObserver = new ResizeObserver(redraw)
    if (curveCanvas.value) resizeObserver.observe(curveCanvas.value)
    if (plateCanvas.value) resizeObserver.observe(plateCanvas.value)
  } else if (typeof window !== 'undefined') {
    windowResizeHandler = redraw
    window.addEventListener('resize', windowResizeHandler)
  }
})

onBeforeUnmount(() => {
  resizeObserver?.disconnect()
  if (windowResizeHandler && typeof window !== 'undefined') window.removeEventListener('resize', windowResizeHandler)
})
</script>

<template>
  <section class="tlc-explorer" aria-label="TLC Rf 曲线交互估计">
    <div class="chart-card"><canvas ref="curveCanvas" role="img" aria-label="Rf 曲线" /></div>
    <div class="plate-card">
      <canvas ref="plateCanvas" role="slider" tabindex="0" aria-label="可拖动的薄层板 Rf 指针" :aria-valuenow="currentRf.toFixed(2)" aria-valuemin="0" aria-valuemax="1" @pointerdown="pointerDown" @pointermove="pointerMove" @pointerup="pointerUp" @pointercancel="pointerUp" />
      <dl><div><dt>当前 Rf</dt><dd>{{ currentRf.toFixed(2) }}</dd></div><div><dt>比例</dt><dd>{{ estimateLabel(estimate) }}</dd></div></dl>
      <p class="estimate-note">橙色标记是前端交互估计，不是正式预测结果。正式条件以服务端反推任务为准。</p>
      <p v-if="failures" class="warning-text">{{ failures }} 个扫描点失败，曲线不会跨失败点连线或插值。</p>
    </div>
  </section>
</template>

<style scoped>
.tlc-explorer{display:grid;grid-template-columns:minmax(0,2fr) minmax(220px,1fr);gap:16px}.chart-card,.plate-card{border:1px solid #d7e1dd;border-radius:14px;background:#fff;padding:12px}canvas{display:block;width:100%;height:330px;touch-action:none}.plate-card dl{display:grid;gap:8px;margin:12px 0}.plate-card dl div{display:flex;justify-content:space-between;gap:12px}.plate-card dt{color:#657871}.plate-card dd{margin:0;font-weight:650;text-align:right}.estimate-note,.warning-text{font-size:12px;line-height:1.5}.estimate-note{color:#657871}.warning-text{color:#9a5a20}@media(max-width:760px){.tlc-explorer{grid-template-columns:1fr}}
</style>
