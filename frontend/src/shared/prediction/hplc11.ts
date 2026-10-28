import { hplc11CandidateConstraints, type components } from './contracts/hplc11-revision15.generated'
import { createExactModelSelection, type PredictionCandidateModelOption } from './model-selection'

type Hplc11Schemas = components['schemas']
export type Hplc11CreateBody = Hplc11Schemas['HplcPredictionCreate']
export type Hplc11Input = Hplc11Schemas['HplcInput']
export type Hplc11HistoricalInput = Hplc11Schemas['HplcInputV10']
export type Hplc11Prediction = Hplc11Schemas['Prediction']
export type Hplc11Accepted = Hplc11Schemas['PredictionAccepted']
export type Hplc11ModelOption = Hplc11Schemas['ModelOption']
export type Hplc11ExactModelSelection = Hplc11Schemas['ExactModelSelection']

const profileSha256 = new RegExp(hplc11CandidateConstraints.preprocessingProfileSha256Pattern)
const structureRefPattern = typeof hplc11CandidateConstraints.structureRefs.pattern === 'string'
  ? new RegExp(hplc11CandidateConstraints.structureRefs.pattern)
  : null

export function isHplc11RawPair(value: unknown): value is [string, string] {
  if (!Array.isArray(value)
    || value.length !== hplc11CandidateConstraints.rawSmiles.minItems
    || value.length !== hplc11CandidateConstraints.rawSmiles.maxItems) return false
  return value.every((item) => typeof item === 'string'
    && item.length >= hplc11CandidateConstraints.rawSmiles.minLength
    && item.length <= hplc11CandidateConstraints.rawSmiles.maxLength
    && item.trim().length > 0)
}

export function isHplc11Input(value: unknown): value is Hplc11Input {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false
  const input = value as Record<string, unknown>
  return Object.keys(input).length === 4
    && ['column_ids', 'flow_rate_ml_min', 'isopropanol_fraction', 'model_input_smiles'].every((key) => key in input)
    && Array.isArray(input.column_ids)
    && input.column_ids.every((column) => typeof column === 'string')
    && typeof input.flow_rate_ml_min === 'number' && Number.isFinite(input.flow_rate_ml_min)
    && typeof input.isopropanol_fraction === 'number' && Number.isFinite(input.isopropanol_fraction)
    && isHplc11RawPair(input.model_input_smiles)
}

export function isHplc11HistoricalInput(value: unknown): value is Hplc11HistoricalInput {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false
  const input = value as Record<string, unknown>
  return Object.keys(input).length === 3
    && ['column_ids', 'flow_rate_ml_min', 'isopropanol_fraction'].every((key) => key in input)
    && Array.isArray(input.column_ids)
    && input.column_ids.every((column) => typeof column === 'string')
    && typeof input.flow_rate_ml_min === 'number' && Number.isFinite(input.flow_rate_ml_min)
    && typeof input.isopropanol_fraction === 'number' && Number.isFinite(input.isopropanol_fraction)
}

export function createHplc11Selection(model: Hplc11ModelOption): Hplc11ExactModelSelection {
  if (model.input_schema_version !== '1.1') throw new Error('当前 HPLC 模型没有声明 1.1 输入能力；预测已禁用。')
  const profileId = model.preprocessing_profile_id
  const profileHash = model.preprocessing_profile_sha256
  if (typeof profileId !== 'string' || !profileId.length || typeof profileHash !== 'string' || !profileSha256.test(profileHash)) {
    throw new Error('当前 HPLC 模型没有完整的精确预处理版本；预测已禁用。')
  }
  return createExactModelSelection(model as PredictionCandidateModelOption, 'hplc') as Hplc11ExactModelSelection
}

export function validHplc11StructureRefs(value: unknown): value is [string, string] {
  if (!Array.isArray(value)
    || value.length !== hplc11CandidateConstraints.structureRefs.minItems
    || value.length !== hplc11CandidateConstraints.structureRefs.maxItems) return false
  return value.every((item) => typeof item === 'string'
    && item.length > 0
    && structureRefPattern?.test(item) === true)
    && value[0] !== value[1]
}
