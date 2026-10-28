<script setup lang="ts">
import { computed } from 'vue'
import type { SfcPinnedCondition } from './types'

const props = withDefaults(defineProps<{ pinnedCondition: SfcPinnedCondition; maxTopK: number; disabled?: boolean }>(), { disabled: false })
const emit = defineEmits<{ submit: [value: { top_k: number }] }>()
const maxTopK = computed(() => Math.max(0, Math.min(1, Math.floor(props.maxTopK))))
const validationErrors = computed(() => {
  const errors: string[] = []
  if (maxTopK.value < 1) errors.push('服务端评估上限不支持当前单候选范围')
  return errors
})
const canSubmit = computed(() => !props.disabled && validationErrors.value.length === 0)

function submit() {
  if (canSubmit.value) emit('submit', { top_k: 1 })
}
</script>

<template>
  <form class="sfc-form" data-testid="sfc-prediction-form" @submit.prevent="submit">
    <fieldset :disabled="disabled">
      <legend>SFC 全柱预测</legend>
      <p class="scope-notice">当前仅开放模型能力声明的固定全柱条件；单柱与批量范围尚未通过验证，不可提交。</p>
      <dl class="pinned-condition">
        <div><dt>色谱柱</dt><dd>{{ pinnedCondition.column }} · {{ pinnedCondition.stationary_phase }} · {{ pinnedCondition.mode }}</dd></div>
        <div><dt>柱尺寸</dt><dd>{{ pinnedCondition.length_mm }} mm × {{ pinnedCondition.inner_diameter_mm }} mm · {{ pinnedCondition.particle_size_um }} μm</dd></div>
        <div><dt>柱温 / 流速</dt><dd>{{ pinnedCondition.column_temperature_c }} °C / {{ pinnedCondition.flow_rate_ml_min }} mL/min</dd></div>
        <div><dt>Modifier</dt><dd>{{ pinnedCondition.modifier_smiles === 'CO' ? `甲醇（${pinnedCondition.modifier_smiles}）` : `SMILES ${pinnedCondition.modifier_smiles}` }}，总体积分数 {{ pinnedCondition.modifier_total_fraction }}</dd></div>
      </dl>
      <p class="top-k" data-testid="top-k">Top-k：1（固定单候选）</p>
      <ul v-if="validationErrors.length" class="validation-errors" data-testid="validation-errors"><li v-for="error in validationErrors" :key="error">{{ error }}</li></ul>
      <button class="primary" type="submit" data-testid="submit" :disabled="!canSubmit">提交固定条件全柱预测</button>
    </fieldset>
  </form>
</template>

<style scoped>
.sfc-form { display: grid; gap: 16px; color: #24445f; }
fieldset { min-width: 0; margin: 0; padding: 0; border: 0; }
legend { margin-bottom: 12px; font-size: 20px; font-weight: 700; }
.scope-notice { color: #6b8193; font-size: 13px; line-height: 1.5; }
.pinned-condition { display: grid; gap: 8px; margin: 0; padding: 14px; border: 1px solid #d8e5ef; border-radius: 12px; background: #fbfdff; }
.pinned-condition div { display: grid; grid-template-columns: minmax(100px, 1fr) minmax(0, 3fr); gap: 12px; }
.pinned-condition dt { color: #6b8193; font-size: 12px; }
.pinned-condition dd { min-width: 0; margin: 0; overflow-wrap: anywhere; color: #24445f; font-size: 13px; }
.top-k { display: grid; max-width: 220px; gap: 6px; color: #486277; font-size: 13px; font-weight: 700; }
input { box-sizing: border-box; width: 100%; border: 1px solid #cbdce9; border-radius: 8px; padding: 9px; background: #fff; color: #24445f; font: inherit; }
.primary { border: 1px solid #1688e8; border-radius: 9px; padding: 9px 12px; background: #1688e8; color: #fff; cursor: pointer; }
.primary:disabled { cursor: not-allowed; opacity: .55; }
.validation-errors { margin: 0; padding: 10px 14px 10px 30px; border: 1px solid #f0c4bc; border-radius: 9px; background: #fff5f2; color: #ad4335; font-size: 13px; line-height: 1.6; }
@media (max-width: 560px) { .pinned-condition div { grid-template-columns: 1fr; gap: 3px; } }
</style>
