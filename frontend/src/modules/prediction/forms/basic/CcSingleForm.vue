<script setup lang="ts">
import { computed, reactive, watch } from 'vue'
import { ccFormValueError, defaultCcFormValue, type CcFormCapability, type CcFormValue } from './form-types'

const props = defineProps<{ capability: CcFormCapability | null; busy?: boolean; disabled?: boolean; initialValue?: CcFormValue | null }>()
const emit = defineEmits<{ submit: [value: CcFormValue]; 'update:value': [value: CcFormValue] }>()
const form = reactive<CcFormValue>({
  column_spec_id: '', eluent_system_id: '', loading_solvent_id: '',
  sample_mass_mg: 0, loading_solvent_volume_ul: 0, flow_rate_ml_min: 0,
})

watch([() => props.capability, () => props.initialValue], ([capability, initialValue]) => {
  Object.assign(form, initialValue ? { ...initialValue } : defaultCcFormValue(capability))
}, { immediate: true })

watch(form, (value) => emit('update:value', { ...value }), { deep: true })
function getValue(): CcFormValue { return { ...form } }
defineExpose({ getValue })

const valueError = computed(() => ccFormValueError(form, props.capability))
const ready = computed(() => !valueError.value && !props.busy && !props.disabled)
</script>

<template>
  <section class="panel prediction-form">
    <div class="section-heading">
      <div><span class="eyebrow">CC · SINGLE</span><h2>柱层析条件</h2></div>
      <p>体积由模型返回；时间由服务端按体积和流速派生。边界来自当前服务端 schema 与模型约束。</p>
    </div>
    <template v-if="capability">
      <div class="grid two-column">
        <label>柱规格
          <select v-model="form.column_spec_id" data-testid="cc-column-spec">
            <option value="" disabled>请选择服务端支持项</option>
            <option v-for="item in capability.columns" :key="item.value" :value="item.value">{{ item.label }}</option>
          </select>
        </label>
        <label>洗脱体系
          <select v-model="form.eluent_system_id" data-testid="cc-eluent-system">
            <option value="" disabled>请选择服务端支持项</option>
            <option v-for="item in capability.eluents" :key="item.value" :value="item.value">{{ item.label }}</option>
          </select>
        </label>
        <label>上样溶剂
          <select v-model="form.loading_solvent_id" data-testid="cc-loading-solvent">
            <option value="" disabled>请选择服务端支持项</option>
            <option v-for="item in capability.loadingSolvents" :key="item.value" :value="item.value">{{ item.label }}</option>
          </select>
        </label>
        <label>样品质量（mg）
          <input v-model.number="form.sample_mass_mg" type="number" :min="capability.ranges.sample_mass_mg.minimum" :max="capability.ranges.sample_mass_mg.maximum" step="any" data-testid="cc-sample-mass" />
          <small>{{ capability.ranges.sample_mass_mg.minimum }}–{{ capability.ranges.sample_mass_mg.maximum }} {{ capability.ranges.sample_mass_mg.unit }}</small>
        </label>
        <label>上样体积（µL）
          <input v-model.number="form.loading_solvent_volume_ul" type="number" :min="capability.ranges.loading_solvent_volume_ul.minimum" :max="capability.ranges.loading_solvent_volume_ul.maximum" step="any" data-testid="cc-loading-volume" />
          <small>{{ capability.ranges.loading_solvent_volume_ul.minimum }}–{{ capability.ranges.loading_solvent_volume_ul.maximum }} {{ capability.ranges.loading_solvent_volume_ul.unit }}</small>
        </label>
        <label>流速（mL/min）
          <input v-model.number="form.flow_rate_ml_min" type="number" :min="capability.ranges.flow_rate_ml_min.minimum" :max="capability.ranges.flow_rate_ml_min.maximum" step="any" data-testid="cc-flow-rate" />
          <small>{{ capability.ranges.flow_rate_ml_min.minimum }}–{{ capability.ranges.flow_rate_ml_min.maximum }} {{ capability.ranges.flow_rate_ml_min.unit }}</small>
        </label>
      </div>
      <p v-if="valueError" class="error-text" role="alert" data-testid="cc-input-validation-error">{{ valueError }}</p>
    </template>
    <p v-else class="error-text" role="alert" data-testid="cc-capability-unavailable">CC 服务端输入 schema 与模型约束缺失或不一致，当前不能提交。</p>
    <button class="primary" :disabled="!ready" data-testid="cc-submit" @click="emit('submit', { ...form })">{{ busy ? '提交中…' : '开始 CC 预测' }}</button>
  </section>
</template>
