import type {
  ContractAccessContext,
  ContractArtifact,
  ContractArtifactAccepted,
  ContractArtifactAcceptedEnvelope,
  ContractArtifactEnvelope,
  ContractCapabilities,
  ContractCapability,
  ContractConfirmation,
  ContractConfirmationPreview,
  ContractConfirmationSummaryItem,
  ContractErrorEnvelope,
  ContractJob,
  ContractJobEvent,
  ContractJobStatus,
  ContractPageInfo,
  ContractResourceRef,
  ContractStructure,
  ContractUpload,
  ContractUploadCreate,
  ContractUploadEnvelope,
  ContractUploadReceipt,
  ContractUploadReceiptEnvelope,
  ContractVersionInfo,
  ContractWarning,
  FrozenComponents,
} from '@/core/contract-types'

export type ApiStatus = ContractCapability['status']

/** Frozen OpenAPI aliases for the currently frozen public routes. */
export type ResourceRef = ContractResourceRef
export type Warning = ContractWarning
export type ApiErrorBody = ContractErrorEnvelope
export type FieldError = NonNullable<ApiErrorBody['error']['field_errors']>[number]
export type FrozenErrorEnvelope = ContractErrorEnvelope
export type FrozenCapabilitiesEnvelope = FrozenComponents['SuccessEnvelope_Capabilities_']
export type FrozenAccessContextEnvelope = FrozenComponents['SuccessEnvelope_AccessContextView_']
export type FrozenVersionEnvelope = FrozenComponents['SuccessEnvelope_VersionInfo_']
export type FrozenReadinessEnvelope = FrozenComponents['SuccessEnvelope_Readiness_']
export type FrozenDependencyListEnvelope = FrozenComponents['ListEnvelope_DependencyHealth_']
export type FrozenLiveEnvelope = FrozenComponents['SuccessEnvelope_dict_str__str__']
export type FrozenProjectListEnvelope = FrozenComponents['ListEnvelope_ProjectView_']
/** Client transport helper; this generic is not a frozen OpenAPI alias. */
export interface ClientTransportEnvelope<T> { data: T; warnings: Warning[]; request_id: string }
export type ApiEnvelope<T> = ClientTransportEnvelope<T>
/** Client transport helper; this generic is not a frozen OpenAPI alias. */
export interface ApiPage<T> { data: T[]; page: ContractPageInfo; warnings: Warning[]; request_id: string }

export type Capability = ContractCapability
export type Capabilities = ContractCapabilities
export type VersionInfo = ContractVersionInfo
export type AccessContextView = ContractAccessContext
export type JobStatus = ContractJobStatus
export type Job = ContractJob
export type JobEvent = ContractJobEvent
export type ConfirmationSummaryItem = ContractConfirmationSummaryItem
export type ConfirmationPreview = ContractConfirmationPreview
export type Confirmation = ContractConfirmation
export type Artifact = ContractArtifact
export type ArtifactAccepted = ContractArtifactAccepted
export type ArtifactAcceptedEnvelope = ContractArtifactAcceptedEnvelope
export type ArtifactEnvelope = ContractArtifactEnvelope
export type UploadCreate = ContractUploadCreate
export type Upload = ContractUpload
export type UploadEnvelope = ContractUploadEnvelope
export type UploadReceipt = ContractUploadReceipt
export type UploadReceiptEnvelope = ContractUploadReceiptEnvelope
/** Frozen OpenAPI alias for POST /structures/normalize. */
export type Structure = ContractStructure
export interface StreamEvent<T = Record<string, unknown>> {
  seq: string; type: string; schema_version: string; timestamp: string; agent_run_id?: string; job_id?: string; data: T
}
