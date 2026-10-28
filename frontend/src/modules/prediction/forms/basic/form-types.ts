export interface SelectOption { value: string; label: string }

export interface CcFormValue {
  column_spec_id: string
  eluent_system_id: string
  loading_solvent_id: string
  sample_mass_mg: number
  loading_solvent_volume_ul: number
  flow_rate_ml_min: number
}

export interface CcNumericRange {
  minimum: number
  maximum: number
  unit: string
}

export interface CcFormCapability {
  columns: SelectOption[]
  eluents: SelectOption[]
  loadingSolvents: SelectOption[]
  ranges: {
    sample_mass_mg: CcNumericRange
    loading_solvent_volume_ul: CcNumericRange
    flow_rate_ml_min: CcNumericRange
  }
}

type UnknownRecord = Record<string, unknown>

function isRecord(value: unknown): value is UnknownRecord {
  return Boolean(value && typeof value === 'object' && !Array.isArray(value))
}

function stringOptions(value: unknown): SelectOption[] | null {
  if (!Array.isArray(value) || value.length === 0
    || value.some((item) => typeof item !== 'string' || !item.trim())
    || new Set(value).size !== value.length) return null
  return value.map((item) => ({ value: item as string, label: item as string }))
}

function matchingOptions(schemaDefinition: unknown, constraintValues: unknown): SelectOption[] | null {
  if (!isRecord(schemaDefinition)) return null
  const values = stringOptions(schemaDefinition.enum)
  const constrained = stringOptions(constraintValues)
  const constrainedIds = new Set(constrained?.map((item) => item.value) ?? [])
  if (!values || !constrained || values.length !== constrained.length
    || values.some((item) => !constrainedIds.has(item.value))) return null
  return values
}

function matchingRange(schemaDefinition: unknown, constraintDefinition: unknown, expectedUnit: string): CcNumericRange | null {
  if (!isRecord(schemaDefinition) || !isRecord(constraintDefinition)) return null
  const minimum = schemaDefinition.minimum
  const maximum = schemaDefinition.maximum
  const constraintMinimum = constraintDefinition.minimum
  const constraintMaximum = constraintDefinition.maximum
  const unit = schemaDefinition.unit
  if (typeof minimum !== 'number' || !Number.isFinite(minimum) || minimum <= 0
    || typeof maximum !== 'number' || !Number.isFinite(maximum) || maximum < minimum
    || typeof constraintMinimum !== 'number' || constraintMinimum !== minimum
    || typeof constraintMaximum !== 'number' || constraintMaximum !== maximum
    || typeof unit !== 'string' || unit !== expectedUnit || constraintDefinition.unit !== unit) return null
  return { minimum, maximum, unit }
}

/** Parse CC candidates and marginal numeric bounds from the matching schema and model constraints. */
export function ccFormCapabilityFromSchema(
  inputSchema: Record<string, unknown> | undefined,
  modelConstraints: Record<string, unknown> | undefined,
): CcFormCapability | null {
  if (!inputSchema || !modelConstraints) return null
  const schemaProperties = inputSchema.properties
  if (!isRecord(schemaProperties) || !isRecord(schemaProperties.input) || !isRecord(schemaProperties.input.properties)) return null
  const inputProperties = schemaProperties.input.properties
  const numericRanges = modelConstraints.numeric_input_ranges
  if (!isRecord(numericRanges)) return null

  const columns = matchingOptions(inputProperties.column_spec_id, modelConstraints.column_spec_ids)
  const eluents = matchingOptions(inputProperties.eluent_system_id, modelConstraints.eluent_system_ids)
  const loadingSolvents = matchingOptions(inputProperties.loading_solvent_id, modelConstraints.loading_solvent_ids)
  const ranges = {
    sample_mass_mg: matchingRange(inputProperties.sample_mass_mg, numericRanges.sample_mass_mg, 'mg'),
    loading_solvent_volume_ul: matchingRange(inputProperties.loading_solvent_volume_ul, numericRanges.loading_solvent_volume_ul, 'uL'),
    flow_rate_ml_min: matchingRange(inputProperties.flow_rate_ml_min, numericRanges.flow_rate_ml_min, 'mL/min'),
  }
  if (!columns || !eluents || !loadingSolvents
    || !ranges.sample_mass_mg || !ranges.loading_solvent_volume_ul || !ranges.flow_rate_ml_min) return null
  return {
    columns, eluents, loadingSolvents,
    ranges: ranges as CcFormCapability['ranges'],
  }
}

export function ccFormValueError(value: CcFormValue, capability: CcFormCapability | null): string | null {
  if (!capability) return 'CC 服务端输入 schema 与模型约束尚未完整匹配。'
  if (!capability.columns.some((item) => item.value === value.column_spec_id)) return '柱规格不在当前服务端能力范围内。'
  if (!capability.eluents.some((item) => item.value === value.eluent_system_id)) return '洗脱体系不在当前服务端能力范围内。'
  if (!capability.loadingSolvents.some((item) => item.value === value.loading_solvent_id)) return '上样溶剂不在当前服务端能力范围内。'
  for (const key of ['sample_mass_mg', 'loading_solvent_volume_ul', 'flow_rate_ml_min'] as const) {
    const number = value[key]
    const range = capability.ranges[key]
    if (typeof number !== 'number' || !Number.isFinite(number) || number < range.minimum || number > range.maximum) {
      return `${key} 超出当前服务端范围 ${range.minimum}–${range.maximum} ${range.unit}。`
    }
  }
  return null
}

export function defaultCcFormValue(capability: CcFormCapability | null): CcFormValue {
  return {
    column_spec_id: capability?.columns[0]?.value ?? '',
    eluent_system_id: capability?.eluents[0]?.value ?? '',
    loading_solvent_id: capability?.loadingSolvents[0]?.value ?? '',
    sample_mass_mg: capability?.ranges.sample_mass_mg.minimum ?? 0,
    loading_solvent_volume_ul: capability?.ranges.loading_solvent_volume_ul.minimum ?? 0,
    flow_rate_ml_min: capability?.ranges.flow_rate_ml_min.minimum ?? 0,
  }
}

export interface HplcFormValue {
  column_ids: string[]
  isopropanol_fraction: number
  flow_rate_ml_min: number
}

export type GcFormValue =
  | { temperature_program: { mode: 'linear'; initial_temperature_c: number; final_temperature_c: number; heating_rate_c_per_min: number; initial_hold_min: number } }
  | { temperature_program: { mode: 'series'; temperature_series_c: number[] } }

/** Editor draft shape; seriesText preserves the user's editable representation. */

export function optionsFromSchema(schema: Record<string, unknown> | undefined, field: string): SelectOption[] {
  const properties = schema?.properties
  if (!properties || typeof properties !== 'object' || Array.isArray(properties)) return []
  const definition = (properties as Record<string, unknown>)[field]
  if (!definition || typeof definition !== 'object' || Array.isArray(definition)) return []
  const values = (definition as Record<string, unknown>).enum
  if (Array.isArray(values)) return values.filter((value): value is string => typeof value === 'string').map((value) => ({ value, label: value }))
  const items = (definition as Record<string, unknown>).items
  if (!items || typeof items !== 'object' || Array.isArray(items)) return []
  const itemValues = (items as Record<string, unknown>).enum
  return Array.isArray(itemValues) ? itemValues.filter((value): value is string => typeof value === 'string').map((value) => ({ value, label: value })) : []
}

export function hplcColumnOptions(modelConstraints: Record<string, unknown> | undefined): SelectOption[] {
  const columns = modelConstraints?.columns
  if (!Array.isArray(columns) || columns.length === 0) return []
  if (columns.some((column) => typeof column !== 'string' || !column.trim()) || new Set(columns).size !== columns.length) return []
  return columns.map((column) => ({ value: column as string, label: column as string }))
}
