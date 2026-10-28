import type { components, operations, paths } from '@/generated/contracts/api-schema'

export type FrozenComponents = components['schemas']
export type FrozenPaths = paths
export type FrozenOperations = operations
export type FrozenSchema<Name extends string> = Name extends keyof FrozenComponents ? FrozenComponents[Name] : never
export type FrozenOperation<Name extends string> = Name extends keyof FrozenOperations ? FrozenOperations[Name] : never
export type ContractResourceRef = FrozenComponents['ResourceRef']
export type ContractWarning = FrozenComponents['WarningDTO']
export type ContractCapability = FrozenComponents['CapabilityView']
export type ContractCapabilities = FrozenComponents['Capabilities']
export type ContractVersionInfo = FrozenComponents['VersionInfo']
export type ContractAccessContext = FrozenComponents['AccessContextView']
export type ContractReadiness = FrozenComponents['Readiness']
export type ContractDependencyHealth = FrozenComponents['DependencyHealth']
export type ContractProject = FrozenComponents['ProjectView']
export type ContractStructure = FrozenComponents['StructureView']
export type ContractNormalizeStructureRequest = FrozenComponents['NormalizeStructureRequest']
export type ContractErrorEnvelope = FrozenComponents['ErrorEnvelope']

// These aliases deliberately point at the generated OpenAPI schema.  They are
// the stable names used by the client/store layer; transport helpers remain in
// core/types.ts and are not presented as contract DTOs.
export type ContractPageInfo = FrozenComponents['PageInfo']
export type ContractArtifact = FrozenComponents['ArtifactView']
export type ContractArtifactEnvelope = FrozenComponents['ArtifactEnvelope']
export type ContractArtifactAccepted = FrozenComponents['ArtifactAccepted']
export type ContractArtifactAcceptedEnvelope = FrozenComponents['ArtifactAcceptedEnvelope']
export type ContractArtifactDeleteAccepted = FrozenComponents['ArtifactDeleteAccepted']
export type ContractArtifactDeleteEnvelope = FrozenComponents['ArtifactDeleteEnvelope']
export type ContractUploadCreate = FrozenComponents['UploadCreate']
export type ContractUpload = FrozenComponents['UploadView']
export type ContractUploadEnvelope = FrozenComponents['UploadEnvelope']
export type ContractUploadReceipt = FrozenComponents['UploadReceipt']
export type ContractUploadReceiptEnvelope = FrozenComponents['UploadReceiptEnvelope']
export type ContractCompleteUploadRequest = FrozenComponents['CompleteUploadRequest']

export type ContractJob = FrozenComponents['JobView']
export type ContractJobStatus = FrozenComponents['JobStatus']
export type ContractJobCancelRequest = FrozenComponents['JobCancelRequest']
export type ContractJobRetryRequest = FrozenComponents['JobRetryRequest']
export type ContractJobAttempt = FrozenComponents['JobAttemptView']
export type ContractJobEvent = FrozenComponents['JobEventView']
export type ContractEventPage = FrozenSchema<'chrom_platform__task_core__schemas__EventPage'>
export type ContractJobEnvelope = FrozenComponents['SuccessEnvelope_JobView_']
export type ContractJobListEnvelope = FrozenComponents['ListEnvelope_JobView_']
export type ContractJobAttemptListEnvelope = FrozenComponents['ListEnvelope_JobAttemptView_']

export type ContractConfirmation = FrozenComponents['ConfirmationView']
export type ContractConfirmationPreview = FrozenComponents['ConfirmationPreview']
export type ContractConfirmationSummaryItem = FrozenComponents['ConfirmationSummaryItem']
export type ContractConfirmationCreateRequest = FrozenComponents['ConfirmationCreateRequest']
export type ContractConfirmationPreviewRequest = FrozenComponents['ConfirmationPreviewRequest']
export type ContractConfirmationRevokeRequest = FrozenComponents['ConfirmationRevokeRequest']
export type ContractConfirmationEnvelope = FrozenComponents['SuccessEnvelope_ConfirmationView_']
export type ContractConfirmationPreviewEnvelope = FrozenComponents['SuccessEnvelope_ConfirmationPreview_']

export type ContractCreateArtifactUploadOperation = FrozenOperations['create_artifact_upload']
export type ContractUploadContentOperation = FrozenOperations['receive_artifact_upload_content']
export type ContractCompleteArtifactUploadOperation = FrozenOperations['complete_artifact_upload']
export type ContractDownloadArtifactOperation = FrozenOperations['download_artifact']
export type ContractCreateConfirmationOperation = FrozenOperations['create_confirmation']
export type ContractPreviewConfirmationOperation = FrozenOperations['preview_confirmation']
export type ContractGetConfirmationOperation = FrozenOperations['get_confirmation']
export type ContractListJobsOperation = FrozenOperations['list_jobs']
export type ContractGetJobOperation = FrozenOperations['get_job']
export type ContractCancelJobOperation = FrozenOperations['cancel_job']
export type ContractRetryJobOperation = FrozenOperations['retry_job']

// Operation request-body projections keep the endpoint binding visible at the
// call site while still deriving every field from generated OpenAPI output.
export type ContractCreateUploadBody = ContractCreateArtifactUploadOperation['requestBody']['content']['application/json']
export type ContractUploadContentBody = ContractUploadContentOperation['requestBody']['content']['application/octet-stream']
export type ContractCompleteUploadBody = ContractCompleteArtifactUploadOperation['requestBody']['content']['application/json']
export type ContractCreateConfirmationBody = ContractCreateConfirmationOperation['requestBody']['content']['application/json']
export type ContractPreviewConfirmationBody = ContractPreviewConfirmationOperation['requestBody']['content']['application/json']
export type ContractCancelJobBody = ContractCancelJobOperation['requestBody']['content']['application/json']

// M2 aliases remain conditional so minimal-structure-v1 can compile the
// shared transport without importing schemas that its contract does not have.
export type ContractTypeSummary = FrozenSchema<'ChromatographyTypeSummary'>
export type ContractTypeCapability = EnvelopeData<ContractTypeCapabilityEnvelope>
export type ContractToolCapability = EnvelopeData<ContractToolCapabilityEnvelope>
export type ContractModelOption = FrozenSchema<'ModelOption'>
export type ContractPrediction = FrozenSchema<'Prediction'>
export type ContractPredictionAccepted = FrozenSchema<'PredictionAccepted'>
export type ContractPredictionRecord = FrozenSchema<'PredictionRecord'>
export type ContractPredictionDraft = FrozenSchema<'PredictionDraftView'>
export type ContractPredictionLimits = FrozenSchema<'PredictionLimits'>
export type ContractPredictionCreateBody = OperationJsonBody<'create_prediction'>
export type ContractExportAccepted = FrozenSchema<'ExportAccepted'>
export type ContractExportView = FrozenSchema<'ExportView'>
export type ContractCreateRecordExportOperation = FrozenOperation<'create_prediction_record_export'>
export type ContractCreateRecordExportBody = OperationJsonBody<'create_prediction_record_export'>
export type ContractRetryJobBody = FrozenOperations['retry_job']['requestBody']['content']['application/json']
export type ContractPredictionEnvelope = FrozenSchema<'SuccessEnvelope_Prediction_'>
export type ContractPredictionAcceptedEnvelope = FrozenSchema<'SuccessEnvelope_PredictionAccepted_'>
export type ContractPredictionRecordListEnvelope = FrozenSchema<'ListEnvelope_PredictionRecord_'>
export type ContractPredictionRecordEnvelope = FrozenSchema<'SuccessEnvelope_PredictionRecord_'>
export type ContractPredictionDraftEnvelope = FrozenSchema<'SuccessEnvelope_PredictionDraftView_'>
export type ContractArchiveArtifactBody = OperationJsonBody<'archive_artifact'>
export type ContractArchiveRecordBody = OperationJsonBody<'archive_prediction_record'>
export type ContractReplayRecordEnvelope = FrozenSchema<'SuccessEnvelope_PredictionDraftView_'>
export type ContractReplayRecordBody = OperationJsonBody<'replay_prediction_record'>
export type ContractDataDomain = FrozenSchema<'DataDomainView'>
export type ContractDataDomainListEnvelope = FrozenSchema<'ListEnvelope_DataDomainView_'>
export type ContractDataDomainEnvelope = FrozenSchema<'SuccessEnvelope_DataDomainView_'>
export type ContractDataImport = FrozenSchema<'DataImport'>
export type ContractDataImportCreateBody = OperationJsonBody<'create_data_import'>
export type ContractDataImportPatchBody = OperationJsonBody<'patch_data_import'>
export type ContractDataImportValidation = FrozenSchema<'DataImportValidation'>
export type ContractDataImportValidationEnvelope = FrozenSchema<'SuccessEnvelope_DataImportValidation_'>
export type ContractDataImportEnvelope = FrozenSchema<'SuccessEnvelope_DataImport_'>
export type ContractAsyncAccepted = FrozenSchema<'AsyncAccepted'>
export type ContractAsyncAcceptedEnvelope = FrozenSchema<'SuccessEnvelope_AsyncAccepted_'>
export type ContractEmptyPayload = FrozenSchema<'_EmptyPayload'>
export type ContractDataVersion = FrozenSchema<'DataVersion'>
export type ContractDataVersionListEnvelope = FrozenSchema<'ListEnvelope_DataVersion_'>
export type ContractDataVersionEnvelope = FrozenSchema<'SuccessEnvelope_DataVersion_'>
export type ContractDataImportValidateBody = OperationJsonBody<'validate_data_import'>
export type ContractDataImportCommitBody = OperationJsonBody<'commit_data_import'>
export type ContractDataVersionArchiveBody = OperationJsonBody<'archive_data_version'>
export type ContractArchiveDataVersionBody = ContractDataVersionArchiveBody

export type ContractCcInput = FrozenSchema<'CcInput'>
export type ContractTlcInput = FrozenSchema<'TlcInput'>
export type ContractTlcSolventComponent = FrozenSchema<'TlcSolventComponent'>
export type ContractTlcConditionOptions = FrozenSchema<'TlcConditionOptions'>
export type ContractTlcConditionComparisonBody = FrozenSchema<'TlcConditionComparisonCreate'>
export type ContractTlcReverseBody = FrozenSchema<'TlcReverseCreate'>
export type ContractSfcCandidateScope = FrozenSchema<'SfcCandidateScopeView'>
export type ContractSfcCandidateScopePolicy = FrozenSchema<'SfcCandidateScopePolicy'>
export type ContractSfcCandidateScopeProvenance = FrozenSchema<'SfcCandidateScopeProvenance'>
export type ContractSfcCandidateConditions = FrozenSchema<'SfcCandidateConditions'>
export type ContractSfcCandidateScopeCreateBody = OperationJsonBody<'create_sfc_candidate_scope'>
export type ContractSfcCandidateScopePreview = FrozenSchema<'SfcCandidateScopePreview'>
export type ContractSfcCandidateScopePreviewBody = OperationJsonBody<'preview_sfc_candidate_scope'>
export type ContractSfcConditionsOptions = FrozenSchema<'SfcConditionsOptions'>
export type ContractSfcSingleColumnBody = FrozenSchema<'SfcSingleColumnPredictionCreate'>
export type ContractSfcFullColumnBody = FrozenSchema<'SfcFullColumnPredictionCreate'>
export type ContractModifierComponent = FrozenSchema<'ModifierComponent'>
export type ContractSfcCandidateScopeOperation = FrozenOperation<'create_sfc_candidate_scope'>
export type ContractSfcCandidateScopePreviewOperation = FrozenOperation<'preview_sfc_candidate_scope'>
export type ContractSfcConditionOptionsOperation = FrozenOperation<'get_sfc_condition_options'>
export type ContractTlcConditionOptionsOperation = FrozenOperation<'get_tlc_condition_options'>
export type ContractTlcConditionComparisonOperation = FrozenOperation<'create_tlc_condition_comparison'>
export type ContractTlcReverseOperation = FrozenOperation<'create_tlc_reverse_solvent'>
export type ContractPriorityTool = FrozenSchema<'PriorityToolView'>
export type ContractPriorityTools = FrozenSchema<'PriorityToolsView'>
export type ContractDataVersionSummary = FrozenSchema<'DataVersionSummary'>
export type ContractPredictionRecordSummary = FrozenSchema<'PredictionRecordSummary'>
export type ContractPriorityRegistrationDraftRequest = FrozenSchema<'PriorityRegistrationDraftRequest'>
export type ContractAssistantModelRegistrationDraft = FrozenSchema<'ModelRegistration'>
export type ContractAssistantToolsOperation = FrozenOperation<'list_priority_assistant_tools'>
export type ContractAssistantDataVersionSummaryOperation = FrozenOperation<'get_assistant_data_version_summary'>
export type ContractAssistantPredictionSummaryOperation = FrozenOperation<'get_assistant_prediction_record_summary'>
export type ContractAssistantRegistrationDraftOperation = FrozenOperation<'create_assistant_model_registration_draft'>
export type ContractAssistantRegistrationDraftQueryOperation = FrozenOperation<'get_assistant_model_registration_draft'>

// M3/M4 universal additions. These names intentionally remain conditional on
// the selected generated contract so non-universal staged packages cannot
// import or silently recover these modules.
export type ContractKnowledgeDocument = FrozenSchema<'KnowledgeDocument'>
export type ContractKnowledgeDocumentCreateBody = FrozenSchema<'KnowledgeDocumentCreate'>
export type ContractKnowledgeSearchBody = FrozenSchema<'KnowledgeSearch'>
export type ContractKnowledgeSearchResult = FrozenSchema<'KnowledgeSearchResult'>
export type ContractKnowledgeChunk = FrozenSchema<'KnowledgeChunk'>
export type ContractCitation = FrozenSchema<'Citation'>
export type ContractKnowledgeAccepted = FrozenSchema<'KnowledgeAccepted'>
export type ContractKnowledgeDocumentListEnvelope = FrozenSchema<'ListEnvelope_KnowledgeDocument_'>
export type ContractKnowledgeSearchEnvelope = FrozenSchema<'SuccessEnvelope_KnowledgeSearchResult_'>
export type ContractCitationEnvelope = FrozenSchema<'SuccessEnvelope_Citation_'>
export type ContractKnowledgeDocumentEnvelope = FrozenSchema<'SuccessEnvelope_KnowledgeDocument_'>
export type ContractKnowledgeAcceptedEnvelope = FrozenSchema<'SuccessEnvelope_KnowledgeAccepted_'>
export type ContractKnowledgeArchiveBody = FrozenSchema<'chrom_platform__modules__knowledge__schemas__ArchiveRequest'>
export type ContractKnowledgeEmptyBody = FrozenSchema<'chrom_platform__modules__knowledge__schemas__EmptyRequest'>

export type ContractTrainingTemplate = FrozenSchema<'TrainingTemplate'>
export type ContractExperiment = FrozenSchema<'Experiment'>
export type ContractExperimentCreateBody = FrozenSchema<'ExperimentCreate'>
export type ContractExperimentPatchBody = FrozenSchema<'ExperimentPatch'>
export type ContractExperimentValidation = FrozenSchema<'ExperimentValidation'>
export type ContractExperimentAccepted = FrozenSchema<'ExperimentAccepted'>
export type ContractExperimentMetrics = FrozenSchema<'ExperimentMetrics'>
export type ContractTrainingEvent = FrozenSchema<'TrainingEvent'>
export type ContractTrainingTemplateListEnvelope = FrozenSchema<'ListEnvelope_TrainingTemplate_'>
export type ContractExperimentListEnvelope = FrozenSchema<'ListEnvelope_Experiment_'>
export type ContractExperimentEnvelope = FrozenSchema<'SuccessEnvelope_Experiment_'>
export type ContractExperimentValidationEnvelope = FrozenSchema<'SuccessEnvelope_ExperimentValidation_'>
export type ContractExperimentAcceptedEnvelope = FrozenSchema<'SuccessEnvelope_ExperimentAccepted_'>
export type ContractExperimentMetricsEnvelope = FrozenSchema<'SuccessEnvelope_ExperimentMetrics_'>
export type ContractTrainingEmptyBody = FrozenSchema<'chrom_platform__modules__training__schemas__EmptyRequest'>
export type ContractSubmitRequestBody = FrozenSchema<'SubmitRequest'>
export type ContractTrainingEvaluationProtocol = FrozenSchema<'chrom_platform__modules__training__schemas__EvaluationProtocol'>
export type ContractListedEvaluationProtocol = FrozenSchema<'chrom_platform__modules__model_center__schemas__EvaluationProtocol'>
export type ContractEvaluationProtocolListEnvelope = FrozenSchema<'ListEnvelope_EvaluationProtocol_'>
export type ContractSplitRandom = FrozenSchema<'SplitRandom'>

export type ContractModel = FrozenSchema<'Model'>
export type ContractModelDetail = FrozenSchema<'ModelDetail'>
export type ContractModelRegistration = FrozenSchema<'ModelRegistration'>
export type ContractModelRegistrationCreateBody = FrozenSchema<'ModelRegistrationCreate'>
export type ContractRegistrationAccepted = FrozenSchema<'RegistrationAccepted'>
export type ContractRegistrationValidation = FrozenSchema<'RegistrationValidation'>
export type ContractModelEvaluation = FrozenSchema<'ModelEvaluation'>
export type ContractEvaluationCreateBody = FrozenSchema<'EvaluationCreate'>
export type ContractEvaluationAccepted = FrozenSchema<'EvaluationAccepted'>
export type ContractApprovalRequest = FrozenSchema<'ApprovalRequest'>
export type ContractApprovalRequestCreateBody = FrozenSchema<'ApprovalRequestCreate'>
export type ContractApprovalDecisionBody = FrozenSchema<'ApprovalDecision'>
export type ContractDeployment = FrozenSchema<'Deployment'>
export type ContractDeploymentDraft = FrozenSchema<'DeploymentDraft'>
export type ContractDeploymentDraftCreateBody = FrozenSchema<'DeploymentDraftCreate'>
export type ContractDeploymentValidation = FrozenSchema<'DeploymentValidation'>
export type ContractDeploymentAccepted = FrozenSchema<'DeploymentAccepted'>
export type ContractRollbackDraftCreateBody = FrozenSchema<'RollbackDraftCreate'>
export type ContractConfirmationRequestBody = FrozenSchema<'ConfirmationRequest'>
export type ContractModelListEnvelope = FrozenSchema<'ListEnvelope_Model_'>
export type ContractDeploymentListEnvelope = FrozenSchema<'ListEnvelope_Deployment_'>
export type ContractModelEnvelope = FrozenSchema<'SuccessEnvelope_Model_'>
export type ContractModelDetailEnvelope = FrozenSchema<'SuccessEnvelope_ModelDetail_'>
export type ContractModelRegistrationEnvelope = FrozenSchema<'SuccessEnvelope_ModelRegistration_'>
export type ContractRegistrationAcceptedEnvelope = FrozenSchema<'SuccessEnvelope_RegistrationAccepted_'>
export type ContractEvaluationAcceptedEnvelope = FrozenSchema<'SuccessEnvelope_EvaluationAccepted_'>
export type ContractApprovalRequestEnvelope = FrozenSchema<'SuccessEnvelope_ApprovalRequest_'>
export type ContractDeploymentDraftEnvelope = FrozenSchema<'SuccessEnvelope_DeploymentDraft_'>
export type ContractDeploymentAcceptedEnvelope = FrozenSchema<'SuccessEnvelope_DeploymentAccepted_'>

export type ContractAgentRun = FrozenSchema<'AgentRun'>
export type ContractAgentRunEnvelope = FrozenSchema<'SuccessEnvelope_AgentRun_'>
export type ContractAgentRunListEnvelope = FrozenSchema<'ListEnvelope_AgentRun_'>
export type ContractConfirmationResume = FrozenSchema<'ConfirmationResume'>
export type ContractRecoveryResume = FrozenSchema<'RecoveryResume'>
export type ContractResumeBody = FrozenSchema<'ConfirmationResume'> | FrozenSchema<'RecoveryResume'>
export type ContractLlmCallSummary = FrozenSchema<'LlmCallSummary'>
export type ContractLlmCallDetail = FrozenSchema<'LlmCallDetail'>
export type ContractCancelAgentRunBody = FrozenSchema<'CancelAgentRun'>
export type ContractToolCall = FrozenSchema<'ToolCall'>
export type ContractConversation = FrozenSchema<'Conversation'>
export type ContractConversationCreateBody = FrozenSchema<'ConversationCreate'>
export type ContractConversationDelete = FrozenSchema<'ConversationDelete'>
export type ContractConversationDeleted = FrozenSchema<'ConversationDeleted'>
export type ContractMessage = FrozenSchema<'Message'>
export type ContractMessageAccepted = FrozenSchema<'MessageAccepted'>
export type ContractUserMessageCreateBody = FrozenSchema<'UserMessageCreate'>
export type ContractContextSnapshotMetadata = FrozenSchema<'ContextSnapshotMetadata'>
export type ContractContextSnapshotContent = FrozenSchema<'ContextSnapshotContent'>
export type ContractContextSnapshotView = FrozenSchema<'ContextSnapshotView'>
export type ContractContextSnapshotCompareBody = FrozenSchema<'ContextSnapshotCompareRequest'>
export type ContractContextDiff = FrozenSchema<'ContextDiff'>
export type ContractContextSnapshotMetadataEnvelope = FrozenSchema<'SuccessEnvelope_ContextSnapshotMetadata_'>
export type ContractContextSnapshotContentEnvelope = FrozenSchema<'SuccessEnvelope_ContextSnapshotContent_'>
export type ContractContextSnapshotViewEnvelope = FrozenSchema<'SuccessEnvelope_ContextSnapshotView_'>
export type ContractContextDiffEnvelope = FrozenSchema<'SuccessEnvelope_ContextDiff_'>
export type ContractAssistantEventPage = FrozenSchema<'chrom_platform__modules__assistant__schemas__EventPage'>
export type ContractAssistantEventEnvelope = FrozenSchema<'chrom_platform__core__dto__SuccessEnvelope_EventPage___3'>
export type ContractAssistantEvent = FrozenSchema<'Event'>

export type ContractPredictionTypesEnvelope = FrozenSchema<'SuccessEnvelope_list_ChromatographyTypeSummary__'>
export type ContractTypeCapabilityEnvelope = OperationJsonEnvelope<'get_prediction_type'>
export type ContractModelsEnvelope = FrozenSchema<'SuccessEnvelope_list_ModelOption__'>
export type ContractToolCapabilityEnvelope = OperationJsonEnvelope<'get_prediction_tool_schema'>
export type ContractPredictionTypesOperation = FrozenOperation<'list_prediction_types'>
export type ContractPredictionTypeOperation = FrozenOperation<'get_prediction_type'>
export type ContractPredictionModelsOperation = FrozenOperation<'list_prediction_models'>
export type ContractPredictionToolSchemaOperation = FrozenOperation<'get_prediction_tool_schema'>
export type ContractCreatePredictionOperation = FrozenOperation<'create_prediction'>
export type ContractGetPredictionOperation = FrozenOperation<'get_prediction'>
export type ContractListPredictionRecordsOperation = FrozenOperation<'list_prediction_records'>
export type ContractGetPredictionRecordOperation = FrozenOperation<'get_prediction_record'>
export type ContractArchivePredictionRecordOperation = FrozenOperation<'archive_prediction_record'>
export type ContractReplayPredictionRecordOperation = FrozenOperation<'replay_prediction_record'>
export type ContractListDataDomainsOperation = FrozenOperation<'list_data_domains'>
export type ContractGetDataDomainSchemaOperation = FrozenOperation<'get_data_domain_schema'>
export type ContractCreateDataImportOperation = FrozenOperation<'create_data_import'>
export type ContractGetDataImportOperation = FrozenOperation<'get_data_import'>
export type ContractPatchDataImportOperation = FrozenOperation<'patch_data_import'>
export type ContractCommitDataImportOperation = FrozenOperation<'commit_data_import'>
export type ContractValidateDataImportOperation = FrozenOperation<'validate_data_import'>
export type ContractGetDataImportValidationOperation = FrozenOperation<'get_data_import_validation'>
export type ContractListDataVersionsOperation = FrozenOperation<'list_data_versions'>
export type ContractGetDataVersionOperation = FrozenOperation<'get_data_version'>
export type ContractArchiveDataVersionOperation = FrozenOperation<'archive_data_version'>

type EnvelopeData<Envelope> = Envelope extends { data: infer Data } ? Data : never
type OperationJsonEnvelope<Name extends string> = FrozenOperation<Name> extends infer Operation
  ? Operation extends { responses: { 200: { content: { 'application/json': infer Envelope } } } } ? Envelope : never
  : never
type OperationJsonBody<Name extends string> = FrozenOperation<Name> extends infer Operation
  ? Operation extends { requestBody: { content: { 'application/json': infer Body } } }
    ? Body
    : never
  : never
