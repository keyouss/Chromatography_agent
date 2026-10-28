import type { components, paths } from '@/generated/contracts/api-schema'

type Schemas = components['schemas']

export type BatchChromatographyType = 'tlc' | 'cc' | 'hplc' | 'gc' | 'sfc'
export type BatchTemplateFormat = Extract<Schemas['BatchExportCreate']['format'], 'csv' | 'xlsx'>

export type BatchTypeCapability = Schemas['BatchAwareTypeCapability']
export type BatchToolCapability = Schemas['TlcBatchToolCapability'] | Schemas['NonTlcBatchToolCapability'] | Schemas['NonBatchToolCapability']
export type TlcBatchToolCapability = Schemas['TlcBatchToolCapability']
export type NonTlcBatchToolCapability = Schemas['NonTlcBatchToolCapability']
export type NonBatchToolCapability = Schemas['NonBatchToolCapability']
export type BatchModelOption = Schemas['ModelOption']
export type TlcBatchCompatibleModel = Schemas['TlcBatchCompatibleModel']
export type NonTlcBatchCompatibleModel = Schemas['NonTlcBatchCompatibleModel']
export type BatchTemplateDefinition = Schemas['BatchTemplateDefinition']
export type BatchResourceBudget = Schemas['BatchResourceBudget']

export type TlcBatchCommonInput = Schemas['TlcBatchCommonInput']
export type CcBatchCommonInput = Schemas['CcBatchCommonInput']
export type HplcBatchCommonInput = Schemas['HplcBatchCommonInput']
export type GcBatchCommonInput = Schemas['GcBatchCommonInput']
export type SfcBatchCommonInput = Schemas['SfcBatchCommonInput']
export type BatchCommonInput = TlcBatchCommonInput | CcBatchCommonInput | HplcBatchCommonInput | GcBatchCommonInput | SfcBatchCommonInput

export type BatchDraftCreate = NonNullable<paths['/api/v1/batch-predictions/drafts']['post']['requestBody']>['content']['application/json']
export type BatchDraftPatch = NonNullable<paths['/api/v1/batch-predictions/{batch_id}']['patch']['requestBody']>['content']['application/json']
export type BatchValidateBody = NonNullable<paths['/api/v1/batch-predictions/{batch_id}/validate']['post']['requestBody']>['content']['application/json']
export type BatchSubmitBody = NonNullable<paths['/api/v1/batch-predictions/{batch_id}/submit']['post']['requestBody']>['content']['application/json']
export type BatchCancelBody = NonNullable<paths['/api/v1/batch-predictions/{batch_id}/cancel']['post']['requestBody']>['content']['application/json']
export type BatchRetryPlanBody = NonNullable<paths['/api/v1/batch-predictions/{batch_id}/retry-plans']['post']['requestBody']>['content']['application/json']
export type BatchRetryFailedBody = NonNullable<paths['/api/v1/batch-predictions/{batch_id}/retry-failed']['post']['requestBody']>['content']['application/json']
export type BatchExportCreate = NonNullable<paths['/api/v1/batch-predictions/{batch_id}/exports']['post']['requestBody']>['content']['application/json']

export type BatchDraft = Schemas['BatchDraft']
export type BatchValidationAccepted = Schemas['BatchValidationAccepted']
export type BatchValidation = Schemas['BatchValidation']
export type RowValidation = Schemas['RowValidation']
export type BatchValidationRowsPage = Schemas['BatchValidationRowsPage']
export type BatchAccepted = Schemas['BatchAccepted']
export type BatchRowResult = Schemas['BatchRowResult']
export type BatchRowsPage = Schemas['BatchRowsPage']
export type BatchRetryPlan = Schemas['BatchRetryPlan']
export type BatchExportAccepted = Schemas['BatchExportAccepted']
export type BatchExportView = Schemas['BatchExportView']

export type TlcBatchResolvedInputV1 = Schemas['TlcBatchResolvedInputV1']
export type TlcBatchResolvedInputV2 = Schemas['TlcBatchResolvedInputV2']
export type CcBatchResolvedInputV2 = Schemas['CcBatchResolvedInputV2']
export type HplcBatchResolvedInputV2 = Schemas['HplcBatchResolvedInputV2']
export type GcBatchResolvedInputV1 = Schemas['GcBatchResolvedInputV1']
export type SfcBatchResolvedInputV2 = Schemas['SfcBatchResolvedInputV2']
export type BatchResolvedInput = TlcBatchResolvedInputV1 | TlcBatchResolvedInputV2 | CcBatchResolvedInputV2 | HplcBatchResolvedInputV2 | GcBatchResolvedInputV1 | SfcBatchResolvedInputV2
