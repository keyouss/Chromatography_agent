<script setup lang="ts">
import { computed } from 'vue'
import type { ContractPrediction } from '@/core/contract-types'
import { predictionHighlightsData } from './prediction-highlights'

const props = withDefaults(defineProps<{ prediction: ContractPrediction | null; hideInvalid?: boolean }>(), { hideInvalid: false })
const data = computed(() => props.prediction ? predictionHighlightsData(props.prediction, props.hideInvalid) : null)
const fmt = (value: number | null | undefined) => typeof value === 'number' && Number.isFinite(value) ? value.toFixed(2) : '—'
const interval = (value: [number, number] | null) => value ? `${fmt(value[0])}–${fmt(value[1])}` : null
const ccDetail = (structure: string, window: string, quantile: string, measure: 'volume' | 'time') => {
  const metrics = props.prediction?.result && 'metrics' in props.prediction.result && Array.isArray(props.prediction.result.metrics) ? props.prediction.result.metrics : []
  const key = `structure_${structure}_${window}_${measure}_${quantile}`
  const value = metrics.find((item) => item.key === key)?.value
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}
const tlcComponents = computed(() => {
  const metrics = props.prediction?.result && 'metrics' in props.prediction.result && Array.isArray(props.prediction.result.metrics) ? props.prediction.result.metrics : []
  return metrics.filter((item) => item.key.startsWith('component_') && typeof item.value === 'number' && Number.isFinite(item.value))
})
</script>

<template>
  <div v-if="data?.type === 'tlc'" class="prediction-highlights">
    <section class="headline-result" data-testid="tlc-primary-result"><span>集成预测 · Rf</span><strong>{{ fmt(data.rf) }}</strong></section>
    <details v-if="tlcComponents.length"><summary>各模型分量</summary><div class="detail-grid"><div v-for="metric in tlcComponents" :key="metric.key"><span>{{ metric.key.replace('component_', '') }}</span><b>{{ fmt(metric.value as number) }}</b></div></div></details>
  </div>
  <div v-else-if="data?.type === 'cc'" class="prediction-highlights">
    <article v-for="structure in data.structures" :key="structure.structure" class="compound-card" data-testid="cc-compound-result">
      <header><b>结构 {{ structure.structure }}</b><span>中位预测 q50</span></header>
      <div class="cc-windows"><div v-for="item in structure.windows" :key="item.window"><span>{{ item.window }}</span><strong>{{ fmt(item.volumeMl) }} mL</strong><small>对应时间 {{ fmt(item.timeSeconds / 60) }} min</small></div></div>
      <details><summary>q10 / q50 / q90 与正式时间（秒）</summary><div class="table-scroll"><table><thead><tr><th>指标</th><th>q10</th><th>q50</th><th>q90</th></tr></thead><tbody><template v-for="item in structure.windows" :key="item.window"><tr><th>{{ item.window }} 体积（mL）</th><td v-for="q in ['q10', 'q50', 'q90']" :key="q">{{ fmt(ccDetail(structure.structure, item.window, q, 'volume')) }}</td></tr><tr><th>{{ item.window }} 时间（秒）</th><td v-for="q in ['q10', 'q50', 'q90']" :key="q">{{ fmt(ccDetail(structure.structure, item.window, q, 'time')) }}</td></tr></template></tbody></table></div></details>
    </article>
  </div>
  <div v-else-if="data?.type === 'hplc'" class="prediction-highlights">
    <section v-if="data.recommendedColumn" class="headline-result compact" data-testid="hplc-primary-result"><span>模型推荐柱</span><strong>{{ data.recommendedColumn }}</strong></section>
    <div class="table-scroll"><table><thead><tr><th>候选柱</th><th>Sp</th><th>结构1 / 2 保留时间（min）</th></tr></thead><tbody><tr v-for="row in data.rows" :key="row.column" :class="{ recommended: row.column === data.recommendedColumn }"><th>{{ row.column }}<small v-if="row.column === data.recommendedColumn">推荐</small></th><td>{{ fmt(row.separation) }}</td><td>{{ fmt(row.structure1Min) }} / {{ fmt(row.structure2Min) }}</td></tr></tbody></table></div>
    <details v-if="data.rows.some((row) => row.range1 || row.range2)"><summary>保留时间分位范围 · q10–q90</summary><div class="table-scroll"><table><thead><tr><th>候选柱</th><th>结构1（min）</th><th>结构2（min）</th></tr></thead><tbody><tr v-for="row in data.rows.filter((item) => item.range1 || item.range2)" :key="row.column"><th>{{ row.column }}</th><td>{{ interval(row.range1) }}</td><td>{{ interval(row.range2) }}</td></tr></tbody></table></div></details>
  </div>
  <div v-else-if="data?.type === 'gc'" class="prediction-highlights">
    <section class="headline-result" data-testid="gc-primary-result"><span>预测保留时间 · q50</span><strong>{{ fmt(data.retentionTimeMin) }} <small>min</small></strong></section>
    <details v-if="data.range"><summary>模型分位范围 · q10–q90</summary><p>{{ interval(data.range) }} min <small>不代表实验置信区间</small></p></details>
  </div>
</template>

<style scoped>
.prediction-highlights{color:var(--text,#1f2d3d)}.headline-result{padding:22px 24px;border:1px solid var(--line,#d7e1eb);border-radius:15px;background:linear-gradient(130deg,var(--primary-soft,#eaf5ff),var(--surface,#fff));margin-bottom:16px}.headline-result>span{font-size:13px;color:var(--muted,#66788a)}.headline-result>strong{display:block;margin:10px 0 4px;font-size:42px;line-height:1.1;color:var(--primary-deep,#003366);font-variant-numeric:tabular-nums}.headline-result strong small{font-size:18px}.headline-result.compact>strong{font-size:32px}.compound-card{border:1px solid var(--line,#d7e1eb);border-radius:12px;padding:15px;margin-bottom:12px;background:var(--surface,#fff)}.compound-card header{display:flex;justify-content:space-between;font-size:13px;margin-bottom:13px}.compound-card header span{color:var(--muted,#66788a)}.cc-windows{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}.cc-windows>div{display:grid;gap:5px;border-left:3px solid var(--primary,#1890ff);padding-left:11px}.cc-windows span,.cc-windows small{font-size:12px;color:var(--muted,#66788a)}.cc-windows strong{font-size:25px;color:var(--primary-deep,#003366);font-variant-numeric:tabular-nums}.prediction-highlights details{border-top:1px solid var(--line,#d7e1eb);padding:12px 0;margin-top:8px}.prediction-highlights summary{cursor:pointer;font-size:12px;font-weight:600;color:var(--primary-deep,#003366)}.detail-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px;margin-top:12px}.detail-grid>div{display:flex;justify-content:space-between;padding:10px;background:var(--surface-soft,#f8fbfe);border-radius:8px;font-size:12px}.table-scroll{overflow-x:auto;margin:12px 0}table{width:100%;border-collapse:collapse;font-size:12px;font-variant-numeric:tabular-nums;text-align:left}th,td{padding:10px 8px;border-bottom:1px solid var(--line,#d7e1eb);white-space:nowrap}.recommended{background:var(--primary-soft,#eaf5ff);color:var(--primary-deep,#003366)}.recommended small{display:block;font-size:10px;margin-top:4px;color:var(--primary,#1890ff)}.prediction-highlights details p{font-size:20px;color:var(--text,#1f2d3d)}.prediction-highlights details p small{font-size:11px;color:var(--muted,#66788a)}@media(max-width:520px){.headline-result{padding:18px}.headline-result>strong{font-size:36px}.cc-windows{grid-template-columns:1fr}.table-scroll th,.table-scroll td{padding:9px 5px}}
</style>
