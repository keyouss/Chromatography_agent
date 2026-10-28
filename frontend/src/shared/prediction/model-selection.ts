import {
  revision12ModelSelectionConstraints,
  type components,
} from './contracts/revision12.generated'

type CandidateSchemas = components['schemas']
export type PredictionCandidateModelOption = CandidateSchemas['ModelOption']
export type PredictionCandidateExactModelSelection = CandidateSchemas['ExactModelSelection']
export type PredictionCandidateDraftExactModelSelection = CandidateSchemas['PredictionDraftExactModelSelection']
export type PredictionCandidateModelSnapshot = CandidateSchemas['chrom_platform__modules__prediction__schemas__ModelSnapshot']
export type PredictionCandidateDraftSourceModelSnapshot = CandidateSchemas['PredictionDraftSourceModelSnapshot']
export type PredictionCandidateDraftView = CandidateSchemas['PredictionDraftView']

type ProfileFields = {
  preprocessing_profile_id?: string | null
  preprocessing_profile_sha256?: string | null
}
type ModelIdentity = ProfileFields & { id: string; version: string }
type SelectionIdentity = ProfileFields & { model_id: string; model_version: string }
type ProfilePair = { present: false } | { present: true; id: string; sha256: string }

const sha256Pattern = new RegExp(revision12ModelSelectionConstraints.preprocessingProfileSha256Pattern)

export class CandidateModelSelectionError extends Error {
  constructor(readonly code: 'INVALID_MODEL_IDENTITY' | 'INVALID_PROFILE_PAIR' | 'CC_PROFILE_REQUIRED', message: string) {
    super(message)
    this.name = 'CandidateModelSelectionError'
  }
}

function validIdentity(id: unknown, version: unknown): id is string {
  return typeof id === 'string'
    && id.length >= revision12ModelSelectionConstraints.modelId.minLength
    && id.length <= revision12ModelSelectionConstraints.modelId.maxLength
    && typeof version === 'string'
    && version.length >= revision12ModelSelectionConstraints.modelVersion.minLength
    && version.length <= revision12ModelSelectionConstraints.modelVersion.maxLength
}

function readProfilePair(value: ProfileFields): ProfilePair | null {
  const id = value.preprocessing_profile_id
  const sha256 = value.preprocessing_profile_sha256
  const hasId = id !== null && id !== undefined
  const hasSha256 = sha256 !== null && sha256 !== undefined
  if (!hasId && !hasSha256) return { present: false }
  if (!hasId || !hasSha256) return null
  if (typeof id !== 'string'
    || id.length < revision12ModelSelectionConstraints.preprocessingProfileId.minLength
    || id.length > revision12ModelSelectionConstraints.preprocessingProfileId.maxLength
    || typeof sha256 !== 'string'
    || !sha256Pattern.test(sha256)) return null
  return { present: true, id, sha256 }
}

export function createExactModelSelection(model: PredictionCandidateModelOption, chromatographyType: string): PredictionCandidateExactModelSelection {
  if (!validIdentity(model.id, model.version)) {
    throw new CandidateModelSelectionError('INVALID_MODEL_IDENTITY', '当前模型身份不符合候选契约，已停止提交。')
  }
  const profile = readProfilePair(model)
  if (!profile) throw new CandidateModelSelectionError('INVALID_PROFILE_PAIR', '模型预处理版本字段不成对或格式无效，已停止提交。')
  if (chromatographyType === 'cc' && !profile.present) {
    throw new CandidateModelSelectionError('CC_PROFILE_REQUIRED', '此 CC 草稿没有绑定预处理版本，请重新创建草稿后再提交。')
  }
  return {
    mode: 'exact',
    model_id: model.id,
    model_version: model.version,
    ...(profile.present ? {
      preprocessing_profile_id: profile.id,
      preprocessing_profile_sha256: profile.sha256,
    } : {}),
  }
}

/** Compare complete model identity, including an optional paired preprocessing profile. */
export function exactModelIdentityMatches(
  selection: PredictionCandidateExactModelSelection | PredictionCandidateDraftExactModelSelection,
  model: PredictionCandidateModelOption | PredictionCandidateModelSnapshot | PredictionCandidateDraftSourceModelSnapshot,
): boolean {
  if (!validIdentity(selection.model_id, selection.model_version) || !validIdentity(model.id, model.version)) return false
  if (selection.model_id !== model.id || selection.model_version !== model.version) return false
  const selectionProfile = readProfilePair(selection)
  const modelProfile = readProfilePair(model)
  if (!selectionProfile || !modelProfile || selectionProfile.present !== modelProfile.present) return false
  return !selectionProfile.present || !modelProfile.present
    ? selectionProfile.present === modelProfile.present
    : selectionProfile.id === modelProfile.id && selectionProfile.sha256 === modelProfile.sha256
}
