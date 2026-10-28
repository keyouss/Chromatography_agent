<script setup lang="ts">
import { computed, reactive, watch } from 'vue'
import type { HplcFormValue, SelectOption } from './form-types'
const props = defineProps<{ columns: SelectOption[]; busy?: boolean; disabled?: boolean; initialValue?: HplcFormValue | null }>()
const emit = defineEmits<{ submit: [value: HplcFormValue]; 'update:value': [value: HplcFormValue] }>()
const form = reactive<HplcFormValue>({ column_ids: [], isopropanol_fraction: 0.2, flow_rate_ml_min: 1 })
watch(() => props.initialValue, (value) => {
  if (!value) return
  form.isopropanol_fraction = value.isopropanol_fraction
  form.flow_rate_ml_min = value.flow_rate_ml_min
  if (form.column_ids.length !== value.column_ids.length || value.column_ids.some((id, index) => form.column_ids[index] !== id)) {
    form.column_ids = [...value.column_ids]
  }
}, { immediate: true, deep: true })
watch(form, (value) => emit('update:value', { ...value, column_ids: [...value.column_ids] }), { deep: true })
function getValue(): HplcFormValue { return { ...form, column_ids: [...form.column_ids] } }
defineExpose({ getValue })
const ready = computed(() => form.column_ids.length > 0 && form.isopropanol_fraction >= 0 && form.isopropanol_fraction <= 1 && form.flow_rate_ml_min > 0 && !props.busy && !props.disabled)
</script>
<template><section class="panel prediction-form"><div class="section-heading"><div><span class="eyebrow">HPLC · SINGLE</span><h2>手性 HPLC 条件</h2></div><p>该模型接收两个对映体结构；候选柱来自服务端能力。</p></div><div class="grid two-column"><fieldset class="column-picker" :disabled="busy || disabled"><legend>候选手性柱</legend><div class="column-options"><label v-for="item in columns" :key="item.value" class="column-option" :class="{ selected: form.column_ids.includes(item.value) }"><input v-model="form.column_ids" type="checkbox" :value="item.value" :aria-label="item.label" /><span>{{ item.label }}</span></label></div></fieldset><div class="field-stack"><label>异丙醇比例<input v-model.number="form.isopropanol_fraction" type="number" min="0" max="1" step="0.01" :disabled="busy || disabled" /></label><label>流速（mL/min）<input v-model.number="form.flow_rate_ml_min" type="number" min="0.001" step="0.1" :disabled="busy || disabled" /></label></div></div><p v-if="!columns.length" class="error-text">服务端尚未返回候选手性柱，当前不能提交。</p><button class="primary" :disabled="!ready" data-testid="hplc-submit" @click="emit('submit', { ...form, column_ids: [...form.column_ids] })">{{ busy ? '提交中…' : '开始 HPLC 预测' }}</button></section></template>

<style scoped>
.column-picker{margin:0;padding:0;border:0;min-width:0}.column-picker legend{font-size:12px;margin-bottom:7px}.column-options{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:7px;max-height:238px;overflow-y:auto}.column-option{display:flex;align-items:center;gap:8px;padding:9px 10px;border:1px solid var(--line,#d7e1eb);border-radius:8px;background:var(--surface,#fff);cursor:pointer;min-width:0;font-size:12px}.column-option.selected{color:var(--primary-deep,#003366);border-color:var(--primary,#1890ff);background:var(--primary-soft,#eaf5ff)}.column-option input{width:14px;height:14px;margin:0;accent-color:var(--primary,#1890ff)}.column-option:focus-within{outline:2px solid var(--primary,#1890ff);outline-offset:1px}.column-picker:disabled .column-option{opacity:.55;cursor:not-allowed}
</style>
