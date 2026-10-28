/**
 * Pure interaction helpers for the TLC reverse-solvent visualisation.
 *
 * These helpers only describe a local UI estimate. They never call a
 * prediction endpoint and their interpolated values must not be presented as
 * a formal model result.
 */
export interface CurvePoint {
  /** Position on the sampled solvent-ratio axis. */
  x: number
  /** Solvent ratio represented by this point, when the backend supplied one. */
  ratio: number | null
  /** Model Rf evidence; null (or a non-finite/out-of-range value) is a failed point. */
  rf: number | null
  label?: string
  /** Separate solvent systems are separate curves, even when their ratio axes match. */
  series?: string
  /** False when the server did not provide adjacent scan evidence for this segment. */
  connectFromPrevious?: boolean
}

export interface CurveRange {
  min: number
  max: number
}

export interface RatioCandidate {
  ratio: number | null
  x: number
  label?: string
  series?: string
  origin: 'sampled' | 'interpolated'
}

export type RatioEstimate =
  | { status: 'insufficient_data' | 'unreachable'; candidates: [] }
  | { status: 'sampled' | 'interpolated'; candidates: [RatioCandidate] }
  | { status: 'ambiguous'; candidates: RatioCandidate[] }

const EPSILON = 1e-9

/** Rf values are meaningful only inside the physical [0, 1] interval. */
export function isValidRf(value: number | null): value is number {
  return value !== null && Number.isFinite(value) && value >= 0 && value <= 1
}

function normaliseRange(range: CurveRange): CurveRange {
  const min = Number.isFinite(range.min) ? Math.max(0, Math.min(1, range.min)) : 0
  const requestedMax = Number.isFinite(range.max) ? Math.max(0, Math.min(1, range.max)) : 1
  return { min, max: Math.max(min, requestedMax) }
}

export function clampRf(value: number, range: CurveRange = { min: 0, max: 1 }): number {
  const safeRange = normaliseRange(range)
  if (Number.isNaN(value)) return safeRange.min
  if (value === Infinity) return safeRange.max
  if (value === -Infinity) return safeRange.min
  return Math.max(safeRange.min, Math.min(safeRange.max, value))
}

export function curveRange(points: readonly CurvePoint[]): CurveRange | null {
  const values = points.flatMap((point) => isValidRf(point.rf) ? [point.rf] : [])
  if (!values.length) return null
  return { min: Math.min(...values), max: Math.max(...values) }
}

export function rfFromPointer(pointerY: number, solventFrontY: number, baselineY: number, range?: CurveRange): number {
  const fallback = range ? clampRf(range.min, range) : 0
  if (![pointerY, solventFrontY, baselineY].every(Number.isFinite) || baselineY <= solventFrontY) return fallback
  return clampRf((baselineY - pointerY) / (baselineY - solventFrontY), range)
}

export function pointerYFromRf(rf: number, solventFrontY: number, baselineY: number, range?: CurveRange): number {
  const value = clampRf(rf, range)
  if (![solventFrontY, baselineY].every(Number.isFinite) || baselineY <= solventFrontY) return solventFrontY
  return baselineY - value * (baselineY - solventFrontY)
}

function candidateKey(candidate: RatioCandidate): string {
  // A segment can meet a sampled vertex from either side. Treat the same
  // location within one solvent system as one candidate while retaining
  // intersections at the same ratio from different systems.
  return `${candidate.series ?? ''}\u0000${candidate.x.toPrecision(12)}\u0000${candidate.label ?? ''}`
}

function addCandidate(candidates: RatioCandidate[], candidate: RatioCandidate): void {
  if (!Number.isFinite(candidate.x)) return
  if (!candidates.some((item) => candidateKey(item) === candidateKey(candidate))) candidates.push(candidate)
}

/**
 * Estimate crossings using only adjacent successful scan points.
 *
 * A failed point remains in the sorted sequence and therefore breaks the
 * segment. A non-monotonic curve returns every crossing as ambiguous instead
 * of silently selecting the first one. Values returned here are UI estimates;
 * the formal reverse result comes only from POST /api/v1/tlc/reverse-solvent.
 */
export function estimateRatio(targetRf: number, points: readonly CurvePoint[]): RatioEstimate {
  // The API constrains target Rf to [0, 1]. Do not silently turn malformed
  // input into a different target that could look like a model answer.
  if (!Number.isFinite(targetRf) || targetRf < 0 || targetRf > 1) return { status: 'unreachable', candidates: [] }
  const target = targetRf
  const sorted = [...points]
    .filter((point) => Number.isFinite(point.x))
    .sort((left, right) => (left.series ?? '').localeCompare(right.series ?? '') || left.x - right.x)
  const validCount = sorted.filter((point) => isValidRf(point.rf)).length
  if (!validCount) return { status: 'insufficient_data', candidates: [] }

  const candidates: RatioCandidate[] = []
  for (const point of sorted) {
    if (!isValidRf(point.rf) || Math.abs(point.rf - target) >= EPSILON) continue
    addCandidate(candidates, {
      ratio: point.ratio !== null && Number.isFinite(point.ratio) ? point.ratio : null,
      x: point.x,
      label: point.label,
      series: point.series,
      origin: 'sampled',
    })
  }

  // Only adjacent entries may be interpolated. Keeping failures in `sorted`
  // is what prevents a missing model response from being bridged.
  for (let index = 0; index < sorted.length - 1; index += 1) {
    const left = sorted[index]
    const right = sorted[index + 1]
    if (!isValidRf(left.rf) || !isValidRf(right.rf) || left.series !== right.series || right.connectFromPrevious === false) continue
    if (Math.abs(left.rf - right.rf) < EPSILON) continue
    const low = Math.min(left.rf, right.rf)
    const high = Math.max(left.rf, right.rf)
    // Endpoints are handled as sampled points above. Strict bounds also avoid
    // making a second interpolation candidate for a sampled vertex.
    if (target <= low + EPSILON || target >= high - EPSILON) continue
    const fraction = (target - left.rf) / (right.rf - left.rf)
    const ratio = left.ratio === null || right.ratio === null ||
      !Number.isFinite(left.ratio) || !Number.isFinite(right.ratio)
      ? null
      : left.ratio + fraction * (right.ratio - left.ratio)
    addCandidate(candidates, {
      x: left.x + fraction * (right.x - left.x),
      ratio,
      series: left.series,
      origin: 'interpolated',
    })
  }

  if (!candidates.length) {
    return validCount < 2 ? { status: 'insufficient_data', candidates: [] } : { status: 'unreachable', candidates: [] }
  }
  if (candidates.length > 1) {
    candidates.sort((left, right) => left.x - right.x)
    return { status: 'ambiguous', candidates }
  }
  const candidate = candidates[0]!
  return { status: candidate.origin, candidates: [candidate] }
}

export function estimateLabel(estimate: RatioEstimate): string {
  if (estimate.status === 'insufficient_data') return '有效预测点不足，无法估算'
  if (estimate.status === 'unreachable') return '目标 Rf 不在已计算曲线范围内'
  if (estimate.status === 'ambiguous') return `曲线存在 ${estimate.candidates.length} 个可能交点，请以正式反推任务结果为准`
  const candidate = estimate.candidates[0]!
  if (candidate.label) return `${candidate.label}（模型采样点）`
  if (candidate.ratio === null) return '交互估计：比例需由正式反推任务解析'
  return `${estimate.status === 'interpolated' ? '交互插值估计' : '模型采样点'}：约 ${candidate.ratio.toFixed(2)} : 1`
}
