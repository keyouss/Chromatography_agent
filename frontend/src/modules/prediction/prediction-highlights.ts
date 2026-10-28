import type { ContractPrediction } from '@/core/contract-types'
import { resultMetadataError, type PredictionResultPayload } from '@/shared/prediction/result'

export type PredictionHighlightsData =
  | { type: 'tlc'; rf: number }
  | { type: 'cc'; structures: Array<{ structure: string; windows: Array<{ window: string; volumeMl: number; timeSeconds: number }> }> }
  | { type: 'hplc'; recommendedColumn: string | null; rows: Array<{ column: string; separation: number | null; structure1Min: number | null; structure2Min: number | null; range1: [number, number] | null; range2: [number, number] | null }> }
  | { type: 'gc'; retentionTimeMin: number; range: [number, number] | null }
interface HplcHighlightRow { column: string; separation: number | null; structure1Min: number | null; structure2Min: number | null; range1: [number, number] | null; range2: [number, number] | null }

type RecordValue = Record<string, unknown>
function isRecord(value: unknown): value is RecordValue {
  return Boolean(value && typeof value === 'object' && !Array.isArray(value))
}
function finiteNumber(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}
function resultRecord(prediction: ContractPrediction): RecordValue | null {
  const result = prediction.result
  return isRecord(result) ? result : null
}
function metricsOf(result: RecordValue): Array<RecordValue> {
  return Array.isArray(result.metrics) ? result.metrics.filter(isRecord) : []
}
function metricValue(metrics: Array<RecordValue>, key: string): unknown {
  return metrics.find((item) => item.key === key)?.value
}
function validRange(lower: unknown, upper: unknown): [number, number] | null {
  const low = finiteNumber(lower)
  const high = finiteNumber(upper)
  return low !== null && high !== null && low <= high ? [low, high] : null
}

/** Read only fields present in the returned DTO. Display rounding stays in the view. */
export function predictionHighlightsData(prediction: ContractPrediction, hideInvalid = false): PredictionHighlightsData | null {
  const result = resultRecord(prediction)
  if (hideInvalid || prediction.status !== 'SUCCEEDED' || prediction.error != null || !result
    || resultMetadataError(result as PredictionResultPayload, prediction.chromatography_type)) return null
  const metrics = metricsOf(result)
  if (prediction.chromatography_type === 'tlc' && result.task_type === 'tlc') {
    const primary = finiteNumber(metricValue(metrics, 'ensemble_rf'))
      ?? (Array.isArray(result.rows) && isRecord(result.rows[0]) ? finiteNumber(result.rows[0].rf) : null)
    return primary === null ? null : { type: 'tlc', rf: primary }
  }
  if (prediction.chromatography_type === 'cc' && result.task_type === 'cc') {
    const keys = metrics.map((item) => typeof item.key === 'string' ? item.key : '').filter((key) => /^structure_\d+_.+_volume_q50$/.test(key))
    const structures = [...new Set(keys.map((key) => /^structure_(\d+)_/.exec(key)?.[1]).filter((item): item is string => Boolean(item)))].sort((a, b) => Number(a) - Number(b))
    const groups = structures.map((structure) => {
      const windows = [...new Set(keys.flatMap((key) => {
        const match = new RegExp(`^structure_${structure}_(.+)_volume_q50$`).exec(key)
        return match ? [match[1]!] : []
      }))]
      return { structure, windows: windows.flatMap((window) => {
        const volumeMl = finiteNumber(metricValue(metrics, `structure_${structure}_${window}_volume_q50`))
        const timeSeconds = finiteNumber(metricValue(metrics, `structure_${structure}_${window}_time_q50`))
        return volumeMl !== null && timeSeconds !== null ? [{ window, volumeMl, timeSeconds }] : []
      }) }
    }).filter((group) => group.windows.length)
    return groups.length ? { type: 'cc', structures: groups } : null
  }
  if (prediction.chromatography_type === 'hplc' && result.task_type === 'hplc') {
    const recommended = metricValue(metrics, 'recommended_column')
    const rows = (Array.isArray(result.rows) ? result.rows : []).flatMap((row): HplcHighlightRow[] => {
      if (!isRecord(row) || typeof row.candidate_id !== 'string') return []
      const column = row.candidate_id
      return [{
        column,
        separation: finiteNumber(row.separation),
        structure1Min: finiteNumber(metricValue(metrics, `${column}.compound_1.rt_q50_min`)),
        structure2Min: finiteNumber(metricValue(metrics, `${column}.compound_2.rt_q50_min`)),
        range1: validRange(metricValue(metrics, `${column}.compound_1.rt_q10_min`), metricValue(metrics, `${column}.compound_1.rt_q90_min`)),
        range2: validRange(metricValue(metrics, `${column}.compound_2.rt_q10_min`), metricValue(metrics, `${column}.compound_2.rt_q90_min`)),
      }]
    })
    return rows.length ? { type: 'hplc', recommendedColumn: typeof recommended === 'string' ? recommended : null, rows } : null
  }
  if (prediction.chromatography_type === 'gc' && result.task_type === 'gc' && Array.isArray(result.rows) && isRecord(result.rows[0])) {
    const row = result.rows[0]
    // GC's local adapter maps its q50 model output to the contract's retention_time_min alias.
    const retentionTimeMin = finiteNumber(metricValue(metrics, 'retention_time_q50_min')) ?? finiteNumber(row.retention_time_min)
    if (retentionTimeMin === null) return null
    const quantiles = isRecord(row.quantiles) ? row.quantiles : {}
    return { type: 'gc', retentionTimeMin, range: validRange(quantiles.q10, quantiles.q90) }
  }
  return null
}
