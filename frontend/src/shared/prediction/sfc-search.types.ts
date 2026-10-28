import type { components } from './contracts/sfc-v11-exact-revision20261008-02.generated'
import type { ContractSfcCandidateScopeCreateBody, ContractSfcCandidateScopeProvenance } from '@/core/contract-types'

export type SfcCandidateKey = keyof components['schemas']['SfcCandidateConditions']
export type SfcSearchExpansionMode = NonNullable<ContractSfcCandidateScopeCreateBody['expansion_mode']>
export type SfcSearchHealth = 'READY' | 'DEGRADED' | 'UNAVAILABLE' | 'UNKNOWN'
export type SfcSearchModelScope = 'all_column' | 'single_column' | null

export interface SfcSearchToolChoice {
  id: string
  label: string
  health: SfcSearchHealth
  unavailableReason?: string | null
  requiresColumn: boolean
  newRequestReady?: boolean
  schemaVersion?: string
}

export interface SfcSearchModelChoice {
  id: string
  version: string
  label: string
  health: SfcSearchHealth
  unavailableReason?: string | null
  newRequestReady?: boolean
  inputSchemaVersion?: string
  business_scope_id: string | null
  threshold: string | number | null
  model_scope: SfcSearchModelScope
  group_id?: string | null
  group_label?: string | null
  stationary_phase?: string | null
  mode?: string | null
  allowed_columns?: readonly string[] | null
}

export interface SfcSearchAxisOption {
  key: SfcCandidateKey
  label: string
  unit: string | null
  values: readonly (string | number)[]
}

export interface SfcSearchBindingOptions {
  projectId: string
  toolId: string
  modelId: string
  modelVersion: string
  business_scope_id: string | null
  model_scope: Exclude<SfcSearchModelScope, null>
  group_id: string | null
  threshold: string | number
  scopeId?: string | null
  supportedExpansionModes?: readonly SfcSearchExpansionMode[]
  scopeProvenance?: ContractSfcCandidateScopeProvenance | null
  axes: readonly SfcSearchAxisOption[]
  limits: {
    candidateLimit: number
    topKMinimum: number
    topKMaximum: number
    modifierMinimum: number
    modifierMaximum: number
    componentFractionMinimum: number
    componentFractionMaximum: number
    totalFractionMinimum: number
    totalFractionMaximum: number
  }
}

export interface SfcSearchFormValue {
  projectId: string
  toolId: string
  modelId: string
  modelVersion: string
  business_scope_id: string | null
  threshold: string | number
  model_scope: Exclude<SfcSearchModelScope, null>
  group_id?: string
  column_id?: string
  expansionMode: SfcSearchExpansionMode
  candidateAxes: Record<SfcCandidateKey, (string | number)[]>
  modifiers: { structureRef: string; fraction: number }[]
  modifierTotalFraction: number
  topK: number
  fractionsWereNormalized: boolean
}
