/** Parse a user-entered temperature list without dropping empty or invalid entries. */
export interface GcTemperatureSeriesParseResult {
  values: number[]
  error: string | null
}

export type GcNumericCapability = {
  minimum?: number
  maximum?: number
  exclusiveMinimum?: number
  multipleOf?: number
}
export type GcFormCapability = {
  initial_temperature_c: GcNumericCapability
  final_temperature_c: GcNumericCapability
  heating_rate_c_per_min: GcNumericCapability
  initial_hold_min: GcNumericCapability
  series: { minItems: number; maxItems: number; temperaturePoints: number }
}
export type GcSeriesPreset = 'fast' | 'medium' | 'slow'
export type GcFormDraftValue =
  | { temperature_program: { mode: 'linear'; initial_temperature_c: number; final_temperature_c: number; heating_rate_c_per_min: number; initial_hold_min: number } }
  | { temperature_program: { mode: 'series'; temperature_series_c: number[] }; seriesText: string }

const gcReferenceRange = { initial: 40, final: 200 } as const

type UnknownRecord = Record<string, unknown>
const linearFields = ['initial_temperature_c', 'final_temperature_c', 'heating_rate_c_per_min', 'initial_hold_min'] as const

function isRecord(value: unknown): value is UnknownRecord {
  return Boolean(value && typeof value === 'object' && !Array.isArray(value))
}

function resolveSchema(value: unknown, root: UnknownRecord): UnknownRecord | null {
  if (!isRecord(value)) return null
  if (typeof value.$ref !== 'string') return value
  const match = value.$ref.match(/^#\/(\$defs|definitions|components\/schemas)\/(.+)$/)
  if (!match) return null
  let target: unknown = root[match[1]!]
  for (const segment of match[2]!.split('/').map((part) => part.replace(/~1/g, '/').replace(/~0/g, '~'))) {
    if (!isRecord(target)) return null
    target = target[segment]
  }
  return isRecord(target) ? target : null
}

function numericCapability(value: unknown): GcNumericCapability | null {
  if (!isRecord(value) || value.type !== 'number') return null
  const result: GcNumericCapability = {}
  for (const key of ['minimum', 'maximum', 'exclusiveMinimum', 'multipleOf'] as const) {
    if (key in value) {
      const number = value[key]
      if (typeof number !== 'number' || !Number.isFinite(number)) return null
      result[key] = number
    }
  }
  if (result.minimum !== undefined && result.maximum !== undefined && result.maximum < result.minimum) return null
  if (result.multipleOf !== undefined && result.multipleOf <= 0) return null
  return result
}

/** Reads only the current selected-model JSON Schema; unresolved or incomplete schemas fail closed. */
export function gcFormCapabilityFromSchema(
  inputSchema: unknown,
  modelConstraints: unknown,
  selectedModel: { id: string; scope: string } | null,
): GcFormCapability | null {
  if (!isRecord(inputSchema) || !isRecord(modelConstraints) || !selectedModel
    || modelConstraints.model_id !== selectedModel.id
    || modelConstraints.business_scope_id !== selectedModel.scope) return null
  const root = inputSchema
  const rootProperties = isRecord(root.properties) ? root.properties : null
  const nestedInput = rootProperties ? resolveSchema(rootProperties.input, root) : null
  const inputNode = nestedInput ?? root
  const inputProperties = isRecord(inputNode.properties) ? inputNode.properties : null
  const programNode = inputProperties ? resolveSchema(inputProperties.temperature_program, root) : null
  const branches = programNode?.oneOf
  if (inputNode.additionalProperties !== false || !Array.isArray(inputNode.required) || !inputNode.required.includes('temperature_program')
    || !inputProperties || !programNode || !Array.isArray(branches) || branches.length !== 2) return null

  const resolved = branches.map((branch) => resolveSchema(branch, root))
  if (resolved.some((branch) => !branch)) return null
  const byMode = new Map<string, UnknownRecord>()
  for (const branch of resolved as UnknownRecord[]) {
    if (branch.additionalProperties !== false || !isRecord(branch.properties) || !Array.isArray(branch.required) || !branch.required.includes('mode')) return null
    const mode = branch.properties.mode
    if (!isRecord(mode) || (mode.const !== 'linear' && mode.const !== 'series')) return null
    if (byMode.has(mode.const)) return null
    byMode.set(mode.const, branch)
  }
  const linear = byMode.get('linear')
  const series = byMode.get('series')
  if (!linear || !series) return null
  const linearProperties = linear.properties as UnknownRecord
  const linearRequired = linear.required as unknown[]
  const numeric = Object.fromEntries(linearFields.map((field) => [field, numericCapability(linearProperties[field])])) as Record<typeof linearFields[number], GcNumericCapability | null>
  if (linearFields.some((field) => !linearRequired.includes(field) || !numeric[field])) return null
  if (numeric.heating_rate_c_per_min!.exclusiveMinimum === undefined
    || numeric.initial_hold_min!.minimum === undefined
    || numeric.heating_rate_c_per_min!.exclusiveMinimum < 0
    || numeric.initial_hold_min!.minimum < 0) return null

  const seriesProperties = series.properties as UnknownRecord
  const seriesRequired = series.required as unknown[]
  const values = seriesProperties.temperature_series_c
  if (!seriesRequired.includes('temperature_series_c') || !isRecord(values) || values.type !== 'array'
    || !isRecord(values.items) || values.items.type !== 'number') return null
  const minItems = values.minItems
  const maxItems = values.maxItems
  if (typeof minItems !== 'number' || !Number.isInteger(minItems) || minItems < 1
    || typeof maxItems !== 'number' || !Number.isInteger(maxItems) || maxItems < minItems) return null
  const tempPoints = modelConstraints.temperature_points
  if (typeof tempPoints !== 'number' || !Number.isInteger(tempPoints) || tempPoints < minItems || tempPoints > maxItems) return null
  return {
    initial_temperature_c: numeric.initial_temperature_c!,
    final_temperature_c: numeric.final_temperature_c!,
    heating_rate_c_per_min: numeric.heating_rate_c_per_min!,
    initial_hold_min: numeric.initial_hold_min!,
    series: { minItems, maxItems, temperaturePoints: tempPoints },
  }
}

/** Build a temperature-only quick fill using the active model's declared point count. */
export function gcTemperatureSeriesPreset(preset: GcSeriesPreset, pointCount: number): number[] | null {
  if (!Number.isInteger(pointCount) || pointCount < 3 || pointCount > 10000) return null
  const profile = { fast: [2, 6], medium: [5, 13], slow: [8, 20] }[preset]
  let [holdPoints, rampIntervals] = profile
  if (pointCount !== 30) {
    holdPoints = Math.max(1, Math.round((pointCount - 1) * holdPoints / 29))
    rampIntervals = Math.max(1, Math.round((pointCount - 2) * rampIntervals / 28))
  }
  if (holdPoints + rampIntervals + 1 > pointCount) rampIntervals = pointCount - holdPoints - 1
  if (rampIntervals < 1) return null
  const values: number[] = Array.from({ length: holdPoints }, () => gcReferenceRange.initial)
  for (let step = 1; step <= rampIntervals; step += 1) {
    values.push(Number((gcReferenceRange.initial + (gcReferenceRange.final - gcReferenceRange.initial) * step / rampIntervals).toFixed(1)))
  }
  while (values.length < pointCount) values.push(gcReferenceRange.final)
  return values.length === pointCount ? values : null
}

export function gcTemperatureValueError(value: number, constraint: GcNumericCapability): string | null {
  if (!Number.isFinite(value)) return '请填写有限数值。'
  if (constraint.minimum !== undefined && value < constraint.minimum) return `数值不得低于 ${constraint.minimum}。`
  if (constraint.maximum !== undefined && value > constraint.maximum) return `数值不得高于 ${constraint.maximum}。`
  if (constraint.exclusiveMinimum !== undefined && value <= constraint.exclusiveMinimum) return `数值必须大于 ${constraint.exclusiveMinimum}。`
  if (constraint.multipleOf !== undefined) {
    const quotient = value / constraint.multipleOf
    if (Math.abs(quotient - Math.round(quotient)) > 1e-9) return `数值必须是 ${constraint.multipleOf} 的倍数。`
  }
  return null
}

export function gcFormValueError(value: unknown, capability: GcFormCapability | null): string | null {
  if (!capability || !isRecord(value) || !isRecord(value.temperature_program)) return 'GC 输入能力尚未完整加载。'
  const program = value.temperature_program
  if (program.mode === 'linear') {
    for (const field of linearFields) {
      const number = program[field]
      if (typeof number !== 'number') return 'GC 线性温度程序字段不完整。'
      const error = gcTemperatureValueError(number, capability[field])
      if (error) return `${field}：${error}`
    }
    return Number(program.final_temperature_c) < Number(program.initial_temperature_c)
      ? '终温须不低于初温。' : null
  }
  if (program.mode === 'series' && Array.isArray(program.temperature_series_c)) {
    const values = program.temperature_series_c
    if (values.some((item) => typeof item !== 'number' || !Number.isFinite(item))) return '温度序列含非有限数值。'
    if (values.length < capability.series.minItems || values.length > capability.series.maxItems) {
      return `温度序列需满足 ${capability.series.minItems}–${capability.series.maxItems} 项的模型约束。`
    }
    return null
  }
  return 'GC 温度程序模式不在当前模型能力范围内。'
}

export function parseGcTemperatureSeries(value: string): GcTemperatureSeriesParseResult {
  if (!value.trim()) return { values: [], error: '请输入温度序列。' }

  const groups = value.split(/\r\n|[,;\r\n]/)
  const values: number[] = []
  let entry = 0
  for (const group of groups) {
    const tokens = group.trim() ? group.trim().split(/\s+/) : ['']
    for (const token of tokens) {
      entry += 1
      if (!token) return { values: [], error: `第 ${entry} 项为空，请补齐后再提交。` }
      const parsed = Number(token)
      if (!Number.isFinite(parsed)) return { values: [], error: `第 ${entry} 项不是有限数值。` }
      values.push(parsed)
    }
  }
  return { values, error: values.length ? null : '请输入温度序列。' }
}
