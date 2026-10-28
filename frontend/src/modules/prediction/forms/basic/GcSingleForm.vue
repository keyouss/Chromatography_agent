<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import type { GcFormValue } from './form-types'
import { gcTemperatureSeriesPreset, gcTemperatureValueError, parseGcTemperatureSeries, type GcFormCapability, type GcFormDraftValue, type GcSeriesPreset } from './gc-temperature-program'
const props = defineProps<{ busy?: boolean; disabled?: boolean; capability?: GcFormCapability | null; initialValue?: GcFormValue | GcFormDraftValue | null }>()
const emit = defineEmits<{ submit: [value: GcFormValue]; 'update:value': [value: GcFormDraftValue] }>()
const mode = ref<'linear' | 'series'>('linear')
const linear = reactive({ initial_temperature_c: 50, final_temperature_c: 250, heating_rate_c_per_min: 10, initial_hold_min: 1 })
const seriesText = ref('')
watch(() => props.initialValue, (value) => {
  if (!value) return
  if (value.temperature_program.mode === 'linear') { mode.value = 'linear'; Object.assign(linear, value.temperature_program) }
  else { mode.value = 'series'; seriesText.value = 'seriesText' in value ? value.seriesText : value.temperature_program.temperature_series_c.join(', ') }
}, { immediate: true, deep: true })
const parsedSeries = computed(() => parseGcTemperatureSeries(seriesText.value))
const linearError = computed(() => {
  const capability = props.capability
  if (!capability) return '当前没有完整、可信的 GC 模型输入能力。'
  for (const field of ['initial_temperature_c', 'final_temperature_c', 'heating_rate_c_per_min', 'initial_hold_min'] as const) {
    const error = gcTemperatureValueError(linear[field], capability[field])
    if (error) return error
  }
  if (linear.final_temperature_c < linear.initial_temperature_c) return '终温须不低于初温。'
  return null
})
const seriesError = computed(() => {
  if (parsedSeries.value.error) return parsedSeries.value.error
  if (!props.capability) return '当前没有完整、可信的 GC 模型输入能力。'
  const { minItems, maxItems } = props.capability.series
  if (parsedSeries.value.values.length < minItems || parsedSeries.value.values.length > maxItems) {
    return `温度序列需输入 ${minItems === maxItems ? minItems : `${minItems}–${maxItems}`} 个数值。`
  }
  return null
})
const ready = computed(() => Boolean(props.capability) && (mode.value === 'linear' ? !linearError.value : !seriesError.value))
function getValue(): GcFormDraftValue {
  return mode.value === 'linear'
    ? { temperature_program: { mode: 'linear', ...linear } }
    : { temperature_program: { mode: 'series', temperature_series_c: [...parsedSeries.value.values] }, seriesText: seriesText.value }
}
watch([mode, linear, seriesText], () => emit('update:value', getValue()), { deep: true })
function fillSeriesPreset(preset: GcSeriesPreset) {
  const values = props.capability ? gcTemperatureSeriesPreset(preset, props.capability.series.temperaturePoints) : null
  if (!values) return
  mode.value = 'series'
  seriesText.value = values.join(', ')
}
defineExpose({ getValue })
function submit() {
  if (!ready.value || props.disabled || props.busy) return
  emit('submit', mode.value === 'linear'
    ? { temperature_program: { mode: 'linear', ...linear } }
    : { temperature_program: { mode: 'series', temperature_series_c: parsedSeries.value.values } })
}
</script>
<template><section class="panel prediction-form"><div class="section-heading"><div><span class="eyebrow">GC · SINGLE</span><h2>温度程序</h2></div><p>温度模式、数值约束和序列长度来自当前 READY 模型能力；当前无法确认能力时不会提交。</p></div><div class="segmented"><button type="button" :disabled="!capability || disabled || busy" :class="{ active: mode === 'linear' }" @click="mode = 'linear'">线性升温</button><button type="button" :disabled="!capability || disabled || busy" :class="{ active: mode === 'series' }" @click="mode = 'series'">温度序列</button></div><div v-if="mode === 'linear'" class="grid two-column"><label>初始温度（°C）<input v-model.number="linear.initial_temperature_c" type="number" :min="capability?.initial_temperature_c.minimum" :max="capability?.initial_temperature_c.maximum" step="any" :disabled="busy || disabled" /></label><label>终止温度（°C）<input v-model.number="linear.final_temperature_c" type="number" :min="capability?.final_temperature_c.minimum" :max="capability?.final_temperature_c.maximum" step="any" :disabled="busy || disabled" /></label><label>升温速率（°C/min）<input v-model.number="linear.heating_rate_c_per_min" type="number" :min="capability?.heating_rate_c_per_min.exclusiveMinimum ?? capability?.heating_rate_c_per_min.minimum" :max="capability?.heating_rate_c_per_min.maximum" :step="capability?.heating_rate_c_per_min.multipleOf ?? 'any'" :disabled="busy || disabled" /></label><label>初始保持（min）<input v-model.number="linear.initial_hold_min" type="number" :min="capability?.initial_hold_min.minimum" :max="capability?.initial_hold_min.maximum" :step="capability?.initial_hold_min.multipleOf ?? 'any'" :disabled="busy || disabled" /></label></div><div v-else class="gc-series-editor"><div class="gc-series-presets"><span>参考程序 40→200 °C · {{ capability?.series.temperaturePoints ?? '—' }} 点</span><div><button v-for="preset in [{ id: 'fast', label: '升温快' }, { id: 'medium', label: '升温中' }, { id: 'slow', label: '升温慢' }]" :key="preset.id" type="button" :disabled="!capability || disabled || busy" :data-testid="`gc-preset-${preset.id}`" @click="fillSeriesPreset(preset.id as GcSeriesPreset)">{{ preset.label }}</button></div></div><label class="wide-field">温度序列（°C，逗号分隔）<textarea v-model="seriesText" rows="4" data-testid="gc-series-input" :disabled="busy || disabled" /></label><small class="gc-series-note">等间隔温度点快捷填入，可继续编辑；采样间隔由方法定义。</small></div><p v-if="mode === 'series' && seriesError" class="error-text" data-testid="gc-series-error">{{ seriesError }}</p><p v-if="mode === 'linear' && linearError" class="error-text" data-testid="gc-linear-error">{{ linearError }}</p><p v-if="!capability" class="error-text" data-testid="gc-capability-missing">未加载完整模型输入 schema；提交已禁用。</p><button class="primary" data-testid="gc-submit" :disabled="!ready || busy || disabled" @click="submit">{{ busy ? '提交中…' : '开始 GC 预测' }}</button></section></template>

<style scoped>
.gc-series-editor{display:grid;gap:9px}.gc-series-presets{display:flex;align-items:center;justify-content:space-between;gap:10px;color:var(--muted,#66788a);font-size:12px}.gc-series-presets>div{display:flex;gap:6px}.gc-series-presets button{min-height:30px;padding:0 10px;border:1px solid var(--line,#d7e1eb);border-radius:8px;color:var(--primary-deep,#003366);background:var(--primary-soft,#eaf5ff);cursor:pointer}.gc-series-presets button:disabled{opacity:.5;cursor:not-allowed}.gc-series-note{color:var(--muted,#66788a);font-size:11px;line-height:1.5}@media(max-width:600px){.gc-series-presets{align-items:flex-start;flex-direction:column}}
</style>
