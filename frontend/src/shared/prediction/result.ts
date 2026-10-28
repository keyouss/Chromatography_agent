import type { ContractPrediction } from '@/core/contract-types'
import type { TlcResultState } from '@/shared/tlc/types'
import type { CurvePoint } from '@/shared/tlc/interaction'
import { activeProfile } from '@/app/generated/profile'
import { isSfcScopeProvenance } from './sfc-provenance'

export type DisplayValue = string | number | boolean | null
export type DisplayRow = Record<string, DisplayValue>

const TERMINAL_PREDICTION_STATUSES: ReadonlySet<ContractPrediction['status']> = new Set([
  'SUCCEEDED', 'PARTIAL', 'FAILED', 'CANCELED', 'TIMED_OUT', 'INTERRUPTED',
])

type GeneratedPredictionResult = NonNullable<ContractPrediction['result']>
export type TlcSingleResult = Extract<GeneratedPredictionResult, { task_type: 'tlc' }>

export interface TlcConditionComparisonResult {
  task_type: 'tlc_condition_comparison'
  schema_version: string
  structure_ref: string
  model_id: string
  model_version: string
  artifact_sha256: string
  results: Record<string, unknown>[]
  warnings?: string[]
}

export interface TlcReverseResult {
  task_type: 'tlc_reverse_solvent'
  schema_version: string
  structure_ref: string
  model_id: string
  model_version: string
  artifact_sha256: string
  candidates: Record<string, unknown>[]
  evaluated: number
  failed: number
  target_reachable: boolean
  warnings: string[]
}

export type TlcResult = TlcSingleResult | TlcConditionComparisonResult | TlcReverseResult
export type PredictionResultPayload = GeneratedPredictionResult | TlcConditionComparisonResult | TlcReverseResult

const SFC_RESULT_CONDITION_KEYS = ['column', 'stationary_phase', 'mode', 'length_mm', 'inner_diameter_mm', 'particle_size_um', 'column_temperature_c', 'flow_rate_ml_min'] as const

export interface TlcReverseCurveProps {
  points: CurvePoint[]
  initialRf: number
  resultState: TlcResultState
  failedPointCount: number
  resultMessage: string | null
}

/** Maps only directly returned model candidates; absent scan rows stay absent. */
export function mapTlcReverseCurve(result: PredictionResultPayload | null | undefined, input: unknown, status?: ContractPrediction['status'], pending = false): TlcReverseCurveProps {
  const source = result?.task_type === 'tlc_reverse_solvent' ? result : null
  const inputRecord = input && typeof input === 'object' && !Array.isArray(input) ? input as Record<string, unknown> : {}
  const targetMin = inputRecord.target_rf_min
  const targetMax = inputRecord.target_rf_max
  const initialRf = typeof targetMin === 'number' && Number.isFinite(targetMin) && typeof targetMax === 'number' && Number.isFinite(targetMax)
    ? Math.max(0, Math.min(1, (targetMin + targetMax) / 2))
    : 0.5

  if (!source) {
    const terminal = status ? isPredictionTerminalStatus(status) : false
    return { points: [], initialRf, resultState: pending || (status && !terminal) ? 'loading' : terminal ? 'failed' : 'empty', failedPointCount: 0, resultMessage: terminal ? '服务端尚未返回有效的 TLC 反推结果。' : null }
  }

  const step = typeof inputRecord.ratio_step === 'number' && Number.isFinite(inputRecord.ratio_step) && inputRecord.ratio_step > 0 ? inputRecord.ratio_step : null
  let malformedCandidateCount = 0
  const points = source.candidates.flatMap((candidate, index): CurvePoint[] => {
    const systemId = candidate.system_id
    const phase = candidate.mobile_phase
    const rf = candidate.predicted_rf
    if (typeof systemId !== 'string' || !systemId || !phase || typeof phase !== 'object' || Array.isArray(phase)) { malformedCandidateCount += 1; return [] }
    const components = systemId.split('/').sort()
    if (components.length !== 2) { malformedCandidateCount += 1; return [] }
    const mobilePhase = phase as Record<string, unknown>
    const firstRatio = mobilePhase[components[0]!]
    const secondRatio = mobilePhase[components[1]!]
    if (typeof firstRatio !== 'number' || !Number.isFinite(firstRatio) || firstRatio < 0 || firstRatio > 1 || typeof secondRatio !== 'number' || !Number.isFinite(secondRatio) || secondRatio < 0 || secondRatio > 1 || Math.abs(firstRatio + secondRatio - 1) > 1e-6) { malformedCandidateCount += 1; return [] }
    const validRf = typeof rf === 'number' && Number.isFinite(rf) && rf >= 0 && rf <= 1
    const previous = source.candidates.slice(0, index).reverse().find((item) => item.system_id === systemId && typeof item.mobile_phase === 'object' && item.mobile_phase !== null && !Array.isArray(item.mobile_phase))
    const previousRatio = previous?.mobile_phase && !Array.isArray(previous.mobile_phase) ? (previous.mobile_phase as Record<string, unknown>)[components[0]!] : undefined
    const adjacent = step !== null && typeof previousRatio === 'number' && Math.abs(Math.abs(firstRatio - previousRatio) - step) <= 1e-6
    if (!validRf) malformedCandidateCount += 1
    return [{ x: firstRatio, ratio: firstRatio, rf: validRf ? rf : null, series: systemId, connectFromPrevious: adjacent, label: `${systemId} · ${components[0]} ${(firstRatio * 100).toFixed(0)}%` }]
  }).sort((left, right) => left.series!.localeCompare(right.series!) || left.x - right.x)

  const failedPointCount = Math.max(0, Math.floor(source.failed))
  const allEvaluationsFailed = source.evaluated > 0 && failedPointCount >= source.evaluated
  const hasInvalidPoint = malformedCandidateCount > 0 || points.some((point) => point.rf === null)
  const resultState: TlcResultState = failedPointCount > 0
    ? allEvaluationsFailed ? 'failed' : 'partial_failure'
    : hasInvalidPoint ? points.some((point) => point.rf !== null) ? 'partial_failure' : 'failed'
      : points.length > 0 && source.target_reachable ? 'success'
      : !source.target_reachable && source.candidates.length === 0 ? 'unreachable' : 'failed'
  const mappingWarning = malformedCandidateCount ? `${malformedCandidateCount} 个服务端候选点字段无效，未作为有效结果绘制。` : ''
  return { points, initialRf, resultState, failedPointCount, resultMessage: [source.warnings.join(' '), mappingWarning].filter(Boolean).join(' ') || null }
}

export function isPredictionTerminalStatus(status: ContractPrediction['status']): boolean {
  return TERMINAL_PREDICTION_STATUSES.has(status)
}

function hasCommonMetadata(result: { schema_version?: unknown; model_id?: unknown; model_version?: unknown; artifact_sha256?: unknown }): boolean {
  return typeof result.schema_version === 'string' && result.schema_version.length > 0
    && typeof result.model_id === 'string' && result.model_id.length > 0
    && typeof result.model_version === 'string' && result.model_version.length > 0
    && typeof result.artifact_sha256 === 'string' && result.artifact_sha256.length > 0
}

function hasApplicability(result: { applicability?: unknown }): boolean {
  const applicability = result.applicability
  return Boolean(applicability && typeof applicability === 'object' && !Array.isArray(applicability)
    && typeof (applicability as { supported_scope?: unknown }).supported_scope === 'string'
    && (applicability as { supported_scope: string }).supported_scope.length > 0)
}

function isRecordArray(value: unknown): value is Record<string, unknown>[] {
  return Array.isArray(value) && value.every((item) => Boolean(item && typeof item === 'object' && !Array.isArray(item)))
}

function isCcResultRow(value: Record<string, unknown>): boolean {
  if (Object.keys(value).length !== 3 || Object.keys(value).some((key) => !['volume_ml', 'flow_rate_ml_min', 'time_seconds'].includes(key))) return false
  const { volume_ml: volume, flow_rate_ml_min: flowRate, time_seconds: time } = value
  if (typeof volume !== 'number' || !Number.isFinite(volume) || volume <= 0
    || typeof flowRate !== 'number' || !Number.isFinite(flowRate) || flowRate <= 0
    || typeof time !== 'number' || !Number.isFinite(time) || time <= 0) return false
  const derivedTime = volume * 60 / flowRate
  if (!Number.isFinite(derivedTime)) return false
  return Math.abs(time - derivedTime) <= Math.max(1e-6, Math.abs(derivedTime) * 1e-6)
}

function isSfcResultRow(value: Record<string, unknown>): boolean {
  if (Object.keys(value).some((key) => !['candidate_id', 'rank', 'conditions', 'score_semantics', 'separation'].includes(key))
    || typeof value.candidate_id !== 'string' || value.candidate_id.trim().length === 0 || value.candidate_id.length > 96
    || typeof value.rank !== 'number' || !Number.isInteger(value.rank) || value.rank < 1
    || value.score_semantics !== 'positive_class_probability'
    || (value.separation !== undefined && value.separation !== null && (typeof value.separation !== 'number' || !Number.isFinite(value.separation)))) return false
  const conditions = value.conditions
  if (!conditions || typeof conditions !== 'object' || Array.isArray(conditions)) return false
  const fields = conditions as Record<string, unknown>
  if (Object.keys(fields).some((key) => key !== 'observed_in_source' && !SFC_RESULT_CONDITION_KEYS.includes(key as (typeof SFC_RESULT_CONDITION_KEYS)[number]))
    || (fields.observed_in_source !== undefined && fields.observed_in_source !== null && typeof fields.observed_in_source !== 'boolean')) return false
  return SFC_RESULT_CONDITION_KEYS.every((key) => {
    const item = fields[key]
    if (key === 'column' || key === 'stationary_phase' || key === 'mode') {
      const maximum = key === 'column' ? 160 : key === 'mode' ? 64 : 255
      return typeof item === 'string' && item.trim().length > 0 && item.length <= maximum
    }
    if (typeof item !== 'number' || !Number.isFinite(item)) return false
    return key === 'column_temperature_c' || item > 0
  })
}

function isLegacySfcResultRow(value: Record<string, unknown>): boolean {
  if (Object.keys(value).some((key) => !['candidate_id', 'rank', 'separation'].includes(key))) return false
  if (typeof value.candidate_id !== 'string' || value.candidate_id.trim().length === 0
    || typeof value.rank !== 'number' || !Number.isInteger(value.rank) || value.rank < 1) return false
  return value.separation === undefined || value.separation === null
    || (typeof value.separation === 'number' && Number.isFinite(value.separation))
}

function isHplcResultRow(value: Record<string, unknown>): boolean {
  if (typeof value.candidate_id !== 'string' || value.candidate_id.trim().length === 0 || value.candidate_id.length > 96) return false
  return value.separation === undefined || value.separation === null
    || (typeof value.separation === 'number' && Number.isFinite(value.separation))
}

function isGcResultRow(value: Record<string, unknown>): boolean {
  if (Object.keys(value).some((key) => !['retention_time_min', 'quantiles'].includes(key))) return false
  if (typeof value.retention_time_min !== 'number' || !Number.isFinite(value.retention_time_min) || value.retention_time_min < 0) return false
  if (value.quantiles === undefined) return true
  if (!value.quantiles || typeof value.quantiles !== 'object' || Array.isArray(value.quantiles)) return false
  return Object.values(value.quantiles as Record<string, unknown>).every((quantile) => typeof quantile === 'number' && Number.isFinite(quantile))
}

/** Validate the immutable prediction model identity used to interpret a GC result. */
export function gcModelSnapshotError(model: unknown): string | null {
  if (!model || typeof model !== 'object' || Array.isArray(model)) return 'GC 模型快照缺少必填身份字段。'
  const snapshot = model as Record<string, unknown>
  const allowedKeys = new Set([
    'id', 'version', 'business_scope_id', 'artifact_sha256', 'input_schema_version', 'output_schema_version',
    'preprocessing_profile_id', 'preprocessing_profile_sha256', 'score_semantics', 'threshold',
  ])
  if (Object.keys(snapshot).some((key) => !allowedKeys.has(key))) return 'GC 模型快照包含未声明的身份字段。'
  const boundedString = (value: unknown, maximum: number) => typeof value === 'string' && value.trim().length > 0 && value.length <= maximum
  if (!boundedString(snapshot.id, 96) || !boundedString(snapshot.version, 128)
    || typeof snapshot.business_scope_id !== 'string' || !boundedString(snapshot.business_scope_id, 160)
    || !/^\S(?:.*\S)?$/.test(snapshot.business_scope_id)
    || typeof snapshot.artifact_sha256 !== 'string' || !/^[0-9a-f]{64}$/.test(snapshot.artifact_sha256)
    || !boundedString(snapshot.input_schema_version, 64) || !boundedString(snapshot.output_schema_version, 64)) {
    return 'GC 模型快照缺少必填身份字段或字段格式无效。'
  }
  if (snapshot.score_semantics !== undefined && snapshot.score_semantics !== null
    && !boundedString(snapshot.score_semantics, 96)) return 'GC 模型快照包含无效 score_semantics。'
  if (snapshot.threshold !== undefined && snapshot.threshold !== null
    && !boundedString(snapshot.threshold, 32)) return 'GC 模型快照包含无效 threshold。'

  const hasProfileId = Object.prototype.hasOwnProperty.call(snapshot, 'preprocessing_profile_id')
  const hasProfileHash = Object.prototype.hasOwnProperty.call(snapshot, 'preprocessing_profile_sha256')
  if (hasProfileId !== hasProfileHash) return 'GC 模型快照预处理版本身份不完整。'
  if (hasProfileId) {
    const profileId = snapshot.preprocessing_profile_id
    const profileHash = snapshot.preprocessing_profile_sha256
    if (profileId === null && profileHash === null) return null
    if (!boundedString(profileId, 96) || typeof profileHash !== 'string' || !/^[0-9a-f]{64}$/.test(profileHash)) {
      return 'GC 模型快照预处理版本身份格式无效。'
    }
  }
  return null
}

/** Validate snapshot requirements introduced with GC 1.1 while preserving GC 1.0 history. */
export function gcPredictionModelSnapshotError(model: unknown, predictionSchemaVersion: unknown): string | null {
  const snapshotError = gcModelSnapshotError(model)
  if (snapshotError) return snapshotError
  if (predictionSchemaVersion === '1.0') return null
  if (predictionSchemaVersion !== '1.1') return 'GC 预测 schema 身份不受支持。'
  const snapshot = model as Record<string, unknown>
  if (snapshot.input_schema_version !== '1.1') return 'GC 1.1 预测的模型输入 schema 身份不匹配。'
  const profileId = snapshot.preprocessing_profile_id
  const profileHash = snapshot.preprocessing_profile_sha256
  if (typeof profileId !== 'string' || profileId.trim().length === 0 || profileId.length > 96
    || typeof profileHash !== 'string' || !/^[0-9a-f]{64}$/.test(profileHash)) {
    return 'GC 1.1 预测缺少完整的精确预处理版本身份。'
  }
  return null
}

export function resultMetadataError(
  result: PredictionResultPayload | null | undefined,
  expectedType: ContractPrediction['chromatography_type'],
  profileId = activeProfile.id,
): string | null {
  if (!result) return '服务端未返回正式结果数据。'

  if (expectedType === 'tlc') {
    if (result.task_type === 'tlc') {
      if (!hasCommonMetadata(result) || !hasApplicability(result) || !isRecordArray(result.rows) || !result.mobile_phase_original || !result.mobile_phase_normalized || !result.ensemble_weights) return 'TLC 结果缺少必填模型、制品、流动相、行或适用性元数据。'
      return null
    }
    if (result.task_type === 'tlc_condition_comparison') {
      if (!hasCommonMetadata(result) || typeof result.structure_ref !== 'string' || !isRecordArray(result.results)) return 'TLC 条件比较结果缺少必填模型、制品、结构或 results 元数据。'
      return null
    }
    if (result.task_type === 'tlc_reverse_solvent') {
      if (!hasCommonMetadata(result) || typeof result.structure_ref !== 'string' || !isRecordArray(result.candidates) || !Number.isFinite(result.evaluated) || result.evaluated < 0 || !Number.isFinite(result.failed) || result.failed < 0 || typeof result.target_reachable !== 'boolean' || !Array.isArray(result.warnings) || result.warnings.some((warning) => typeof warning !== 'string')) return 'TLC 反推结果缺少必填模型、制品、候选、评估或 warning 元数据。'
      return null
    }
    return `结果类型 ${result.task_type} 与预测类型 ${expectedType} 不匹配。`
  }

  if (result.task_type !== expectedType) return `结果类型 ${result.task_type} 与预测类型 ${expectedType} 不匹配。`
  if (expectedType === 'cc') {
    if (typeof result.schema_version !== 'string' || !result.schema_version.length || !hasApplicability(result)
      || !isRecordArray(result.rows) || !result.rows.every(isCcResultRow)) return 'CC 结果行缺少冻结合同字段或体积、流速、派生时间无效。'
    return null
  }
  if (expectedType === 'sfc') {
    if (typeof result.schema_version !== 'string' || !result.schema_version.length || !hasApplicability(result) || !isRecordArray(result.rows)) return 'SFC 结果缺少必填 schema、rows 或 applicability 元数据。'
    const scoreSemantics = (result as Record<string, unknown>).score_semantics
    if (profileId === 'sfc-v1' && scoreSemantics !== undefined) return 'SFC legacy 结果包含当前契约未定义的顶层 score_semantics 字段。'
    if (scoreSemantics !== undefined && scoreSemantics !== 'positive_class_probability') return 'SFC 结果分数语义与服务端默认约定不匹配。'
    const validSfcResultRow = profileId === 'sfc-v1' ? isLegacySfcResultRow : isSfcResultRow
    if (!result.rows.every(validSfcResultRow)) return 'SFC 候选结果行缺少当前构建契约要求的有效字段。'
    const metadata = result as Record<string, unknown>
    const provenance = metadata.candidate_scope_provenance
    if (provenance != null && !isSfcScopeProvenance(provenance)) return 'SFC 候选范围来源或计数不符合契约。'
    const evaluated = metadata.evaluated_candidate_count
    if (evaluated != null && (typeof evaluated !== 'number' || !Number.isSafeInteger(evaluated) || evaluated < 1 || evaluated > 100_000
      || result.rows.length > evaluated || (isSfcScopeProvenance(provenance) && evaluated > provenance.candidate_count))) return 'SFC 实际评估数量与范围或返回条数不一致。'
    if (profileId !== 'sfc-v1') {
      const unseenRows = result.rows.filter((row) => {
        const conditions = (row as unknown as Record<string, unknown>).conditions
        return conditions !== null && typeof conditions === 'object' && !Array.isArray(conditions)
          && (conditions as Record<string, unknown>).observed_in_source === false
      }).length
      if (unseenRows && (!isSfcScopeProvenance(provenance) || unseenRows > provenance.unobserved_count)) return 'SFC 返回的目录未见组合缺少一致的范围来源。'
      if (isSfcScopeProvenance(provenance)) {
        const observedRows = result.rows.filter((row) => {
          const conditions = (row as unknown as Record<string, unknown>).conditions
          return conditions !== null && typeof conditions === 'object' && !Array.isArray(conditions)
            && (conditions as Record<string, unknown>).observed_in_source === true
        }).length
        if (observedRows > provenance.observed_count) return 'SFC 返回的目录已有组合超出冻结范围计数。'
      }
    }
    return null
  }
  if (expectedType === 'hplc') {
    if (typeof result.schema_version !== 'string' || result.schema_version.length === 0 || !hasApplicability(result)
      || !isRecordArray(result.rows) || !result.rows.every(isHplcResultRow)) return 'HPLC 结果缺少必填 schema、rows 或 applicability 元数据。'
    return null
  }
  if (expectedType === 'gc') {
    if (typeof result.schema_version !== 'string' || result.schema_version.length === 0 || result.schema_version.length > 64
      || !hasApplicability(result) || !isRecordArray(result.rows) || !result.rows.every(isGcResultRow)) {
      return 'GC 结果缺少必填 schema、保留时间行或 applicability 元数据。'
    }
    return null
  }
  if (!hasCommonMetadata(result) || !hasApplicability(result) || !isRecordArray(result.rows)) return '结果缺少必填模型、制品、rows 或 applicability 元数据。'
  return null
}

/** Format scientific cells only; stored results and request payloads keep their precision. */
export function formatResultCell(value: unknown, field = ''): string {
  if (value === null || value === undefined) return '—'
  if (typeof value === 'number') {
    if (!Number.isFinite(value)) return '—'
    if (/(^|_)(id|rank|index|count|evaluated|failed|revision|top_k)$/.test(field)) return String(value)
    const formatted = value.toFixed(2)
    return formatted === '-0.00' ? '0.00' : formatted
  }
  if (typeof value === 'string' || typeof value === 'boolean') return String(value)
  try {
    return JSON.stringify(value, (key, item) => typeof item === 'number' ? formatResultCell(item, key) : item)
  } catch { return '无法显示该结果字段' }
}

export function toDisplayRow(row: object): DisplayRow {
  const entries: [string, DisplayValue][] = []
  for (const [key, value] of Object.entries(row)) {
    if (value === null || typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') entries.push([key, value])
    else entries.push([key, formatResultCell(value, key)])
  }
  return Object.fromEntries(entries)
}

export function toSfcDisplayRow(row: object): DisplayRow {
  const { conditions, ...fields } = row as Record<string, unknown>
  const values = conditions && typeof conditions === 'object' && !Array.isArray(conditions) ? conditions as Record<string, unknown> : {}
  const display = { ...toDisplayRow(fields), ...toDisplayRow(values) }
  if ('observed_in_source' in values) display.observed_in_source = values.observed_in_source === true ? '是' : values.observed_in_source === false ? '否' : '未知'
  return display
}
