import { gc11CandidateConstraints, type components } from './contracts/gc11-revision18.generated'
import { createExactModelSelection, type PredictionCandidateModelOption } from './model-selection'

type Gc11Schemas = components['schemas']
export type Gc11CreateBody = Gc11Schemas['GcPredictionCreate']
export type Gc11Input = Gc11Schemas['GcInput']
export type Gc11Prediction = Gc11Schemas['Prediction']
export type Gc11Accepted = Gc11Schemas['PredictionAccepted']
export type Gc11ModelOption = Gc11Schemas['ModelOption']
export type Gc11ExactProfileSelection = Gc11Schemas['GcExactProfileModelSelection']

const profileHashPattern = new RegExp(gc11CandidateConstraints.profileSha256Pattern)
const structureRefPattern = new RegExp(gc11CandidateConstraints.structureRefs.pattern)

export function createGc11Selection(model: Gc11ModelOption): Gc11ExactProfileSelection {
  if (model.health.status !== 'READY' || model.input_schema_version !== '1.1') {
    throw new Error('当前 GC 模型尚未声明可用的 1.1 输入能力；预测已禁用。')
  }
  const profileId = model.preprocessing_profile_id
  const profileHash = model.preprocessing_profile_sha256
  if (typeof profileId !== 'string' || profileId.length < gc11CandidateConstraints.profileId.minLength
    || profileId.length > gc11CandidateConstraints.profileId.maxLength
    || typeof profileHash !== 'string' || !profileHashPattern.test(profileHash)) {
    throw new Error('当前 GC 模型没有完整的精确预处理版本；预测已禁用。')
  }
  const selection = createExactModelSelection(model as unknown as PredictionCandidateModelOption, 'gc')
  if (selection.preprocessing_profile_id !== profileId || selection.preprocessing_profile_sha256 !== profileHash) {
    throw new Error('GC 模型预处理版本与精确选择不一致；预测已禁用。')
  }
  return {
    mode: 'exact',
    model_id: selection.model_id,
    model_version: selection.model_version,
    preprocessing_profile_id: profileId,
    preprocessing_profile_sha256: profileHash,
  }
}

export function isGc11Input(value: unknown): value is Gc11Input {
  if (!isRecord(value) || Object.keys(value).length !== 1 || !('temperature_program' in value)) return false
  const program = value.temperature_program
  if (!isRecord(program)) return false
  if (program.mode === 'linear') {
    const keys = ['mode', 'initial_temperature_c', 'final_temperature_c', 'heating_rate_c_per_min', 'initial_hold_min']
    return hasOnlyKeys(program, keys)
      && keys.slice(1).every((key) => finite(program[key]))
      && Number(program.final_temperature_c) >= Number(program.initial_temperature_c)
      && Number(program.heating_rate_c_per_min) > 0
      && Number(program.initial_hold_min) >= gc11CandidateConstraints.linearHoldMinimum
  }
  if (program.mode === 'series') {
    const values = program.temperature_series_c
    return hasOnlyKeys(program, ['mode', 'temperature_series_c'])
      && Array.isArray(values)
      && values.length >= gc11CandidateConstraints.seriesItems.minItems
      && values.length <= gc11CandidateConstraints.seriesItems.maxItems
      && values.every(finite)
  }
  return false
}

export function isGc11StructureRefs(value: unknown): value is [string] {
  return Array.isArray(value)
    && value.length === gc11CandidateConstraints.structureRefs.minItems
    && value.length === gc11CandidateConstraints.structureRefs.maxItems
    && typeof value[0] === 'string'
    && structureRefPattern.test(value[0])
}

export function isGc11DraftSelection(value: unknown): value is Gc11ExactProfileSelection {
  if (!isRecord(value)
    || Object.keys(value).length !== 5
    || value.mode !== 'exact'
    || typeof value.model_id !== 'string'
    || value.model_id.length < gc11CandidateConstraints.modelId.minLength
    || value.model_id.length > gc11CandidateConstraints.modelId.maxLength
    || typeof value.model_version !== 'string'
    || value.model_version.length < gc11CandidateConstraints.modelVersion.minLength
    || value.model_version.length > gc11CandidateConstraints.modelVersion.maxLength
    || typeof value.preprocessing_profile_id !== 'string'
    || value.preprocessing_profile_id.length < gc11CandidateConstraints.profileId.minLength
    || value.preprocessing_profile_id.length > gc11CandidateConstraints.profileId.maxLength
    || typeof value.preprocessing_profile_sha256 !== 'string'
    || !profileHashPattern.test(value.preprocessing_profile_sha256)) return false
  return true
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value && typeof value === 'object' && !Array.isArray(value))
}
function hasOnlyKeys(value: Record<string, unknown>, keys: string[]): boolean {
  return Object.keys(value).length === keys.length && keys.every((key) => key in value)
}
function finite(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value)
}
