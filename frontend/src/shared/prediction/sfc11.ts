/** Candidate-only SFC 1.1 adapters. These types are isolated from frozen core aliases. */
import type { components } from './contracts/sfc-v11-exact-revision20261008-02.generated'
import type {
  ContractSfcCandidateScope,
  ContractSfcCandidateScopeCreateBody,
  ContractSfcConditionsOptions,
} from '@/core/contract-types'
import { createExactModelSelection, type PredictionCandidateModelOption } from './model-selection'
import type { SfcSearchBindingOptions, SfcSearchExpansionMode, SfcSearchModelChoice, SfcSearchToolChoice, SfcCandidateKey } from './sfc-search.types'
import { isSfcScopeProvenance } from './sfc-provenance'

type Schemas = components['schemas']
export type Sfc11ModelOption = Schemas['ModelOption']
export type Sfc11ExactSelection = Schemas['ExactModelSelection']
export type Sfc11ScopeCreate = ContractSfcCandidateScopeCreateBody
export type Sfc11Scope = ContractSfcCandidateScope
export type Sfc11Options = ContractSfcConditionsOptions
export type Sfc11SingleBody = Schemas['SfcSingleColumnPredictionCreate']
export type Sfc11FullBody = Schemas['SfcFullColumnPredictionCreate']
export type Sfc11Tool = {
  id: string
  display_name?: string
  schema_version?: string
  health?: { status?: string; reason?: string | null }
}

const candidateKeys = [
  'column', 'stationary_phase', 'mode', 'length_mm', 'inner_diameter_mm',
  'particle_size_um', 'column_temperature_c', 'flow_rate_ml_min',
] as const satisfies readonly SfcCandidateKey[]
const sha256 = /^[0-9a-f]{64}$/

function object(value: unknown): value is Record<string, unknown> {
  return Boolean(value && typeof value === 'object' && !Array.isArray(value))
}
function healthy(value: unknown): SfcSearchModelChoice['health'] {
  if (value === 'READY' || value === 'DEGRADED' || value === 'UNAVAILABLE') return value
  return 'UNKNOWN'
}

export function mapSfcModelOption(value: Sfc11ModelOption): SfcSearchModelChoice | null {
  if (!value || value.task_type !== 'sfc' || !value.id || !value.version || !value.scope) return null
  const domain = value.sfc_domain
  if (!object(domain) || (domain.model_scope !== 'single_column' && domain.model_scope !== 'all_column')) return null
  if (domain.model_scope === 'all_column' && Object.keys(domain).some((key) => key !== 'model_scope')) return null
  let modelScope: SfcSearchModelChoice['model_scope'] = domain.model_scope
  let groupFields: Partial<SfcSearchModelChoice> = {}
  if (domain.model_scope === 'single_column') {
    if (Object.keys(domain).some((key) => !['model_scope', 'group_id', 'group_label', 'stationary_phase', 'mode', 'allowed_columns'].includes(key))) return null
    const single = domain
    if (typeof single.group_id !== 'string' || !single.group_id.trim()
      || typeof single.group_label !== 'string' || !single.group_label.trim()
      || typeof single.stationary_phase !== 'string' || !single.stationary_phase.trim()
      || typeof single.mode !== 'string' || !single.mode.trim()
      || !Array.isArray(single.allowed_columns) || single.allowed_columns.length === 0
      || single.allowed_columns.some((item) => typeof item !== 'string' || !item.trim())
      || new Set(single.allowed_columns).size !== single.allowed_columns.length) return null
    groupFields = { group_id: single.group_id, group_label: single.group_label, stationary_phase: single.stationary_phase, mode: single.mode, allowed_columns: [...single.allowed_columns] }
  }
  const profileId = value.preprocessing_profile_id
  const profileHash = value.preprocessing_profile_sha256
  // SFC 1.1 is exact-model but this candidate deliberately rejects profile-bound models.
  const hasProfile = profileId !== undefined && profileId !== null || profileHash !== undefined && profileHash !== null
  const profileMalformed = hasProfile && !(typeof profileId === 'string' && profileId.trim() && typeof profileHash === 'string' && sha256.test(profileHash))
  return {
    id: value.id,
    version: value.version,
    label: `${value.task_type} · ${value.scope}`,
    health: healthy(value.health?.status),
    unavailableReason: profileMalformed || hasProfile
      ? '当前 SFC 1.1 不接受带预处理 profile 的模型。'
      : value.input_schema_version !== '1.1' ? '模型输入 schema 不是 SFC 1.1。' : value.health?.reason ?? null,
    newRequestReady: !profileMalformed && !hasProfile && value.input_schema_version === '1.1',
    inputSchemaVersion: value.input_schema_version,
    business_scope_id: value.scope,
    threshold: value.threshold ?? null,
    model_scope: modelScope,
    ...groupFields,
  }
}

export function mapSfcToolChoice(value: Sfc11Tool): SfcSearchToolChoice | null {
  if (!value || (value.id !== 'single_column' && value.id !== 'full_column')) return null
  const status = healthy(value.health?.status)
  return {
    id: value.id,
    label: value.display_name?.trim() || (value.id === 'single_column' ? '单柱搜索' : '全柱搜索'),
    health: status,
    unavailableReason: value.schema_version === '1.1' ? value.health?.reason ?? null : '当前仅支持通过 1.1 schema 创建新搜索；该旧版本仅保留历史读取。',
    requiresColumn: value.id === 'single_column',
    newRequestReady: value.schema_version === '1.1',
    schemaVersion: value.schema_version,
  }
}

export function createSfc11ExactSelection(model: Sfc11ModelOption): Sfc11ExactSelection {
  const mapped = mapSfcModelOption(model)
  if (!mapped || mapped.health !== 'READY' || !mapped.newRequestReady) throw new Error('SFC 模型 metadata 不完整、schema 不是 1.1 或当前不可用。')
  const candidate = model as unknown as PredictionCandidateModelOption
  const selection = createExactModelSelection(candidate, 'sfc')
  if (selection.preprocessing_profile_id || selection.preprocessing_profile_sha256) throw new Error('SFC 1.1 当前不接受带 preprocessing profile 的模型。')
  return selection as Sfc11ExactSelection
}

export function sfcDomainMatchesOptions(model: Sfc11ModelOption, domain: unknown): boolean {
  if (!object(domain) || !model.sfc_domain || !object(model.sfc_domain)) return false
  const expected = model.sfc_domain as unknown as Record<string, unknown>
  const keys = Object.keys(expected).sort()
  const actualKeys = Object.keys(domain).sort()
  if (keys.length !== actualKeys.length || !keys.every((key, index) => key === actualKeys[index])) return false
  return keys.every((key) => {
    const wanted = expected[key]
    const got = domain[key]
    if (Array.isArray(wanted)) return Array.isArray(got) && wanted.length === got.length
      && [...wanted].sort().every((value, index) => value === [...got].sort()[index])
    return got === wanted
  })
}

export function optionsForSfcSearch(
  value: Sfc11Options,
  model: Sfc11ModelOption,
  tool: SfcSearchToolChoice,
  projectId: string,
  limits: SfcSearchBindingOptions['limits'],
  expectedScopeId: string | null = null,
): SfcSearchBindingOptions | null {
  const mapped = mapSfcModelOption(model)
  if (!mapped || mapped.health !== 'READY' || !mapped.newRequestReady || tool.health !== 'READY' || !tool.newRequestReady || !projectId || value.project_id !== projectId
    || value.model_id !== model.id || value.model_version !== model.version || value.scope_id !== expectedScopeId
    || !Number.isInteger(value.candidate_count) || value.candidate_count < 1
    || !Number.isInteger(value.max_candidates) || value.max_candidates < value.candidate_count
    || !sfcDomainMatchesOptions(model, value.sfc_domain)
    || tool.id !== (mapped.model_scope === 'single_column' ? 'single_column' : 'full_column')
    || tool.requiresColumn !== (mapped.model_scope === 'single_column')) return null
  if (model.threshold === undefined || model.threshold === null || value.threshold !== model.threshold) return null
  const supportedModes = value.supported_expansion_modes
  const normalizedModes: SfcSearchExpansionMode[] = supportedModes === undefined
    ? ['observed_distinct_rows']
    : Array.isArray(supportedModes) && supportedModes.every((mode) => mode === 'observed_distinct_rows' || mode === 'coupled_discrete_conditions')
      && new Set(supportedModes).size === supportedModes.length && supportedModes.includes('observed_distinct_rows')
      ? [...supportedModes] : []
  if (!normalizedModes.length) return null
  const scopeProvenance = value.scope_provenance
  if (scopeProvenance != null && (!isSfcScopeProvenance(scopeProvenance)
    || scopeProvenance.scope_id !== expectedScopeId)) return null
  if (expectedScopeId === null && scopeProvenance != null) return null
  if (!Array.isArray(value.fields) || value.fields.length !== candidateKeys.length) return null
  const fields = new Map(value.fields.map((field) => [field.key, field]))
  if (fields.size !== candidateKeys.length || candidateKeys.some((key) => !fields.has(key))) return null
  const axes = candidateKeys.map((key) => {
    const field = fields.get(key)!
    if (typeof field.label !== 'string' || !field.label.trim()
      || (field.unit !== undefined && field.unit !== null && typeof field.unit !== 'string')
      || !Array.isArray(field.values) || !field.values.length || field.values.some((item) => !(typeof item === 'string' && item.trim()) && !(typeof item === 'number' && Number.isFinite(item)))) return null
    return { key, label: field.label, unit: field.unit ?? null, values: field.values }
  })
  if (axes.some((axis) => !axis)) return null
  if (mapped.model_scope === 'single_column') {
    const columnValues = fields.get('column')!.values.map(String)
    if (!mapped.allowed_columns?.some((column) => columnValues.includes(column))
      || columnValues.some((column) => !mapped.allowed_columns?.includes(column))) return null
  }
  return {
    projectId,
    toolId: tool.id,
    modelId: model.id,
    modelVersion: model.version,
    business_scope_id: model.scope,
    model_scope: mapped.model_scope!,
    group_id: mapped.model_scope === 'single_column' ? mapped.group_id ?? null : null,
    threshold: model.threshold,
    scopeId: null,
    supportedExpansionModes: normalizedModes,
    scopeProvenance: scopeProvenance ?? null,
    axes: axes as NonNullable<SfcSearchBindingOptions['axes']>,
    limits,
  }
}

function schemaNode(value: unknown): Record<string, unknown> | null {
  return object(value) ? value : null
}

function findSchemaProperty(root: unknown, key: string, seen = new Set<object>()): Record<string, unknown> | null {
  const node = schemaNode(root)
  if (!node || seen.has(node)) return null
  seen.add(node)
  const properties = schemaNode(node.properties)
  const direct = properties && schemaNode(properties[key])
  if (direct) return direct
  for (const nestedKey of ['$defs', 'definitions', 'properties', 'items', 'allOf', 'oneOf', 'anyOf']) {
    const nested = node[nestedKey]
    if (Array.isArray(nested)) {
      for (const child of nested) { const found = findSchemaProperty(child, key, seen); if (found) return found }
    } else if (object(nested)) {
      const children = nestedKey === 'items' ? [nested] : Object.values(nested)
      for (const child of children) { const found = findSchemaProperty(child, key, seen); if (found) return found }
    }
  }
  return null
}

function boundedPair(node: Record<string, unknown> | null): [number, number] | null {
  const minimum = node?.minimum
  const maximum = node?.maximum
  return typeof minimum === 'number' && Number.isFinite(minimum) && typeof maximum === 'number' && Number.isFinite(maximum) && maximum >= minimum
    ? [minimum, maximum] : null
}

/** Derive UI limits from the READY tool schema and its published limits. */
export function sfcSearchLimitsFromSchema(
  inputSchema: unknown,
  toolLimits: unknown,
): SfcSearchBindingOptions['limits'] | null {
  const limits = schemaNode(toolLimits)
  const candidateLimit = limits?.max_candidates
  const topK = boundedPair(findSchemaProperty(inputSchema, 'top_k'))
  const components = findSchemaProperty(inputSchema, 'modifier_components')
  const fraction = findSchemaProperty(inputSchema, 'fraction')
  const totalFraction = boundedPair(findSchemaProperty(inputSchema, 'modifier_total_fraction'))
  const modifierMinimum = components?.minItems
  const modifierMaximum = components?.maxItems
  const componentFraction = boundedPair(fraction)
  if (typeof candidateLimit !== 'number' || !Number.isInteger(candidateLimit) || candidateLimit < 1
    || !topK || !componentFraction || !totalFraction
    || typeof modifierMinimum !== 'number' || !Number.isInteger(modifierMinimum) || modifierMinimum < 1
    || typeof modifierMaximum !== 'number' || !Number.isInteger(modifierMaximum) || modifierMaximum < modifierMinimum) return null
  return {
    candidateLimit,
    topKMinimum: topK[0], topKMaximum: topK[1],
    modifierMinimum, modifierMaximum,
    componentFractionMinimum: componentFraction[0], componentFractionMaximum: componentFraction[1],
    totalFractionMinimum: totalFraction[0], totalFractionMaximum: totalFraction[1],
  }
}

export function sfcScopeMatchesSelection(scope: Sfc11Scope, model: Sfc11ModelOption, projectId: string, axes: Record<string, (string | number)[]>): boolean {
  if (scope.project_id !== projectId || scope.model_id !== model.id || scope.model_version !== model.version
    || !scope.id || !Number.isInteger(scope.candidate_count) || scope.candidate_count < 1
    || !Number.isInteger(scope.max_candidates) || scope.max_candidates < scope.candidate_count
    || typeof scope.truncated !== 'boolean' || !scope.conditions.length || scope.conditions.length > scope.candidate_count) return false
  return scope.conditions.every((condition) => Object.entries(axes).every(([key, values]) => values.includes(condition[key as keyof typeof condition] as string | number)))
}

export function sfcOptionsStayWithinAxes(options: SfcSearchBindingOptions, axes: Record<string, (string | number)[]>): boolean {
  const selected = new Map(Object.entries(axes))
  return options.axes.length === selected.size && options.axes.every((axis) => {
    const allowed = selected.get(axis.key)
    return Boolean(allowed && axis.values.length > 0 && axis.values.every((value) => allowed.includes(value)))
  })
}

