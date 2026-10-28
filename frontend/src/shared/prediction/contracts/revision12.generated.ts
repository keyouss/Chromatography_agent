/**
 * Candidate-local generated prediction selection types.
 * Candidate: universal-v1-config-assistant-revision-20261007-12 (PREVIEW_NOT_FROZEN).
 * Input SHA-256: 7554504F12BDFFBE6AC0656A3A6625D1003DA8FA58DF498A7D8558F7246DA724.
 * Generated with openapi-typescript 7.13.0; never import from core aliases or formal contracts.
 */
export type paths = Record<string, never>;
export type webhooks = Record<string, never>;
export interface components {
    schemas: {
        /** CapabilityHealth */
        CapabilityHealth: {
            /**
             * Checked At
             * Format: date-time
             */
            checked_at: string;
            /** Reason */
            reason?: string | null;
            /**
             * Status
             * @enum {string}
             */
            status: "READY" | "DEGRADED" | "UNAVAILABLE" | "UNKNOWN";
        };
        /** ExactModelSelection */
        ExactModelSelection: {
            /**
             * Mode
             * @constant
             */
            mode: "exact";
            /** Model Id */
            model_id: string;
            /** Model Version */
            model_version: string;
            /**
             * Preprocessing Profile Id
             * @description CC 精确模型要求的预处理版本标识；必须与 SHA-256 字段同时出现。
             */
            preprocessing_profile_id?: string | null;
            /**
             * Preprocessing Profile Sha256
             * @description CC 精确模型要求的预处理版本 SHA-256；必须与标识字段同时出现。
             */
            preprocessing_profile_sha256?: string | null;
        };
        /** ModelOption */
        ModelOption: {
            /** Artifact Sha256 */
            artifact_sha256?: string | null;
            health: components["schemas"]["CapabilityHealth"];
            /** Id */
            id: string;
            /** Input Schema Version */
            input_schema_version: string;
            /** Output Schema Version */
            output_schema_version: string;
            /**
             * Preprocessing Profile Id
             * @description CC 模型能力绑定的预处理版本标识；与 SHA-256 成对提供，其他类型为空。
             */
            preprocessing_profile_id?: string | null;
            /**
             * Preprocessing Profile Sha256
             * @description CC 模型能力绑定的预处理版本 SHA-256；必须与标识字段成对提供。
             */
            preprocessing_profile_sha256?: string | null;
            /** Scope */
            scope: string;
            /** Score Semantics */
            score_semantics?: string | null;
            /** Task Type */
            task_type: string;
            /** Threshold */
            threshold?: string | null;
            /** Version */
            version: string;
        };
        /** PredictionDraftExactModelSelection */
        PredictionDraftExactModelSelection: {
            /**
             * Mode
             * @constant
             */
            mode: "exact";
            /** Model Id */
            model_id: string;
            /** Model Version */
            model_version: string;
            /**
             * Preprocessing Profile Id
             * @description CC 重放来源的预处理版本标识；必须与 SHA-256 字段成对提供。
             */
            preprocessing_profile_id?: string | null;
            /**
             * Preprocessing Profile Sha256
             * @description CC 重放来源的预处理版本 SHA-256；必须与标识字段成对提供。
             */
            preprocessing_profile_sha256?: string | null;
        };
        /** PredictionDraftSourceModelSnapshot */
        PredictionDraftSourceModelSnapshot: {
            /** Artifact Sha256 */
            artifact_sha256: string;
            /** Business Scope Id */
            business_scope_id: string;
            /** Id */
            id: string;
            /** Input Schema Version */
            input_schema_version: string;
            /** Output Schema Version */
            output_schema_version: string;
            /**
             * Preprocessing Profile Id
             * @description 源模型的 CC 预处理版本标识；必须与 SHA-256 字段成对提供。
             */
            preprocessing_profile_id?: string | null;
            /**
             * Preprocessing Profile Sha256
             * @description 源模型的 CC 预处理版本 SHA-256；必须与标识字段成对提供。
             */
            preprocessing_profile_sha256?: string | null;
            /** Score Semantics */
            score_semantics?: string | null;
            /** Threshold */
            threshold?: string | null;
            /** Version */
            version: string;
        };
        /** PredictionDraftStructureSnapshot */
        PredictionDraftStructureSnapshot: {
            /** Canonical Smiles */
            canonical_smiles: string;
            /** Id */
            id: string;
            /** Project Id */
            project_id: string;
            /**
             * Stereochemistry Status
             * @enum {string}
             */
            stereochemistry_status: "specified" | "unspecified" | "not_applicable";
        };
        /** PredictionDraftView */
        PredictionDraftView: {
            /**
             * Chromatography Type
             * @enum {string}
             */
            chromatography_type: "tlc" | "cc" | "hplc" | "gc" | "sfc" | "chiral_lc";
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /** Etag */
            etag: string;
            /** Id */
            id: string;
            /** Input */
            input: {
                [key: string]: unknown;
            };
            model_selection: components["schemas"]["PredictionDraftExactModelSelection"];
            /** Modifier Structure Snapshots */
            modifier_structure_snapshots?: components["schemas"]["PredictionDraftStructureSnapshot"][];
            /** Project Id */
            project_id: string;
            /** Revision */
            revision: number;
            /** Schema Version */
            schema_version: string;
            source_model_snapshot: components["schemas"]["PredictionDraftSourceModelSnapshot"];
            /** Source Prediction Id */
            source_prediction_id: string;
            /** Source Record Id */
            source_record_id: string;
            /** Source Record Revision */
            source_record_revision: number;
            /** Structure Refs */
            structure_refs?: components["schemas"]["ResourceRef"][];
            /** Structure Snapshots */
            structure_snapshots: components["schemas"]["PredictionDraftStructureSnapshot"][];
            /** Tool */
            tool: string;
            /**
             * Updated At
             * Format: date-time
             */
            updated_at: string;
        };
        /** ResourceRef */
        ResourceRef: {
            /** Id */
            id: string;
            /** Type */
            type: string;
        };
        /** ModelSnapshot */
        chrom_platform__modules__prediction__schemas__ModelSnapshot: {
            /** Artifact Sha256 */
            artifact_sha256: string;
            /** Business Scope Id */
            business_scope_id: string;
            /** Id */
            id: string;
            /** Input Schema Version */
            input_schema_version: string;
            /** Output Schema Version */
            output_schema_version: string;
            /**
             * Preprocessing Profile Id
             * @description CC 专用预处理版本标识；必须与 preprocessing_profile_sha256 成对提供。
             */
            preprocessing_profile_id?: string | null;
            /**
             * Preprocessing Profile Sha256
             * @description CC 专用预处理版本 SHA-256；必须与 preprocessing_profile_id 成对提供。
             */
            preprocessing_profile_sha256?: string | null;
            /** Score Semantics */
            score_semantics?: string | null;
            /** Threshold */
            threshold?: string | null;
            /** Version */
            version: string;
        };
    };
    responses: never;
    parameters: never;
    requestBodies: never;
    headers: never;
    pathItems: never;
}
export type $defs = Record<string, never>;
export type operations = Record<string, never>;


export const revision12ModelSelectionConstraints = {
  "modelId": {
    "minLength": 1,
    "maxLength": 96
  },
  "modelVersion": {
    "minLength": 1,
    "maxLength": 128
  },
  "preprocessingProfileId": {
    "minLength": 1,
    "maxLength": 96
  },
  "preprocessingProfileSha256Pattern": "^[0-9a-f]{64}$"
} as const
