/**
 * Candidate-local generated HPLC 1.1 types.
 * Candidate: universal-v1-hplc11-revision-20261007-15 (PREVIEW_NOT_FROZEN).
 * Input SHA-256: 4D9558DBD339A168D3A8F2FC06AAF72F41CE0202FD3C2062F2592BAD97A0E475.
 * Generated with openapi-typescript 7.13.0; never imported by formal core contract aliases.
 */
export type paths = Record<string, never>;
export type webhooks = Record<string, never>;
export interface components {
    schemas: {
        /** Applicability */
        Applicability: {
            /** Supported Scope */
            supported_scope: string;
            /** Warnings */
            warnings?: components["schemas"]["Warning"][];
        };
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
        /** CcInput */
        CcInput: {
            /** Column Spec Id */
            column_spec_id: string;
            /** Eluent System Id */
            eluent_system_id: string;
            /** Flow Rate Ml Min */
            flow_rate_ml_min: number;
            /** Loading Solvent Id */
            loading_solvent_id: string;
            /** Loading Solvent Volume Ul */
            loading_solvent_volume_ul: number;
            /** Sample Mass Mg */
            sample_mass_mg: number;
        };
        /** CcPredictionResult */
        CcPredictionResult: {
            applicability: components["schemas"]["Applicability"];
            /** Metrics */
            metrics?: components["schemas"]["Metric"][];
            /** Rows */
            rows: components["schemas"]["CcResultRow"][];
            /** Schema Version */
            schema_version: string;
            /**
             * @description discriminator enum property added by openapi-typescript
             * @enum {string}
             */
            task_type: "cc";
            /** Visualizations */
            visualizations?: components["schemas"]["Visualization"][];
        };
        /** CcResultRow */
        CcResultRow: {
            /** Flow Rate Ml Min */
            flow_rate_ml_min: number;
            /** Time Seconds */
            readonly time_seconds: number;
            /** Volume Ml */
            volume_ml: number;
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
             * @description 能力声明要求的精确预处理版本标识；必须与 SHA-256 字段同时出现。
             */
            preprocessing_profile_id?: string | null;
            /**
             * Preprocessing Profile Sha256
             * @description 能力声明要求的精确预处理版本 SHA-256；必须与标识字段同时出现。
             */
            preprocessing_profile_sha256?: string | null;
        };
        /** GcInput */
        GcInput: {
            /** Temperature Program */
            temperature_program: components["schemas"]["GcLinearTemperatureProgram"] | components["schemas"]["GcSeriesTemperatureProgram"];
        };
        /** GcLinearTemperatureProgram */
        GcLinearTemperatureProgram: {
            /** Final Temperature C */
            final_temperature_c: number;
            /** Heating Rate C Per Min */
            heating_rate_c_per_min: number;
            /** Initial Hold Min */
            initial_hold_min: number;
            /** Initial Temperature C */
            initial_temperature_c: number;
            /**
             * @description discriminator enum property added by openapi-typescript
             * @enum {string}
             */
            mode: "linear";
        };
        /** GcPredictionResult */
        GcPredictionResult: {
            applicability: components["schemas"]["Applicability"];
            /** Metrics */
            metrics?: components["schemas"]["Metric"][];
            /** Rows */
            rows: components["schemas"]["GcResultRow"][];
            /** Schema Version */
            schema_version: string;
            /**
             * @description discriminator enum property added by openapi-typescript
             * @enum {string}
             */
            task_type: "gc";
            /** Visualizations */
            visualizations?: components["schemas"]["Visualization"][];
        };
        /** GcResultRow */
        GcResultRow: {
            /** Quantiles */
            quantiles?: {
                [key: string]: number;
            };
            /** Retention Time Min */
            retention_time_min: number;
        };
        /** GcSeriesTemperatureProgram */
        GcSeriesTemperatureProgram: {
            /**
             * @description discriminator enum property added by openapi-typescript
             * @enum {string}
             */
            mode: "series";
            /** Temperature Series C */
            temperature_series_c: number[];
        };
        /**
         * HplcInput
         * @description HPLC schema 1.1 input; raw strings are the author's actual inputs.
         */
        HplcInput: {
            /** Column Ids */
            column_ids: string[];
            /** Flow Rate Ml Min */
            flow_rate_ml_min: number;
            /** Isopropanol Fraction */
            isopropanol_fraction: number;
            /** Model Input Smiles */
            model_input_smiles: string[];
        };
        /**
         * HplcInputV10
         * @description Read-only decoder for canonical-only historical HPLC snapshots.
         */
        HplcInputV10: {
            /** Column Ids */
            column_ids: string[];
            /** Flow Rate Ml Min */
            flow_rate_ml_min: number;
            /** Isopropanol Fraction */
            isopropanol_fraction: number;
        };
        /** HplcPredictionCreate */
        HplcPredictionCreate: {
            /**
             * Chromatography Type
             * @constant
             */
            chromatography_type: "hplc";
            input: components["schemas"]["HplcInput"];
            model_selection: components["schemas"]["ExactModelSelection"];
            /** Project Id */
            project_id?: string | null;
            /**
             * Schema Version
             * @constant
             */
            schema_version: "1.1";
            /**
             * Structure Refs
             * @description Exactly two distinct structures bound in order to the raw HPLC SMILES pair.
             */
            structure_refs: string[];
            /**
             * Tool
             * @constant
             */
            tool: "single";
        };
        /** HplcPredictionResult */
        HplcPredictionResult: {
            applicability: components["schemas"]["Applicability"];
            /** Metrics */
            metrics?: components["schemas"]["Metric"][];
            /** Rows */
            rows: components["schemas"]["HplcResultRow"][];
            /** Schema Version */
            schema_version: string;
            /**
             * @description discriminator enum property added by openapi-typescript
             * @enum {string}
             */
            task_type: "hplc";
            /** Visualizations */
            visualizations?: components["schemas"]["Visualization"][];
        };
        /** HplcResultRow */
        HplcResultRow: {
            /** Candidate Id */
            candidate_id: string;
            /** Separation */
            separation?: number | null;
        };
        /**
         * JobStatus
         * @enum {string}
         */
        JobStatus: "QUEUED" | "RUNNING" | "CANCEL_REQUESTED" | "SUCCEEDED" | "PARTIAL" | "FAILED" | "CANCELED" | "TIMED_OUT" | "INTERRUPTED";
        /** Metric */
        Metric: {
            /** Key */
            key: string;
            /** Label */
            label: string;
            /**
             * Origin
             * @enum {string}
             */
            origin: "model" | "derived";
            /** Unit */
            unit?: string | null;
            /** Value */
            value: number | string | null;
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
             * @description 模型能力绑定的精确预处理版本标识；与 SHA-256 成对提供。
             */
            preprocessing_profile_id?: string | null;
            /**
             * Preprocessing Profile Sha256
             * @description 模型能力绑定的预处理版本 SHA-256；必须与标识字段成对提供。
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
        /** ModifierComponent */
        ModifierComponent: {
            /** Fraction */
            fraction: number;
            /** Structure Ref */
            structure_ref: string;
        };
        /** Prediction */
        Prediction: {
            /** Agent Run Id */
            agent_run_id?: string | null;
            /**
             * Chromatography Type
             * @enum {string}
             */
            chromatography_type: "cc" | "gc" | "hplc" | "sfc" | "tlc";
            /** Completed At */
            completed_at?: string | null;
            /** Conversation Id */
            conversation_id?: string | null;
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /**
             * Data Origin
             * @enum {string}
             */
            data_origin: "production" | "fixture";
            error?: components["schemas"]["PublicErrorSummary"] | null;
            /** Etag */
            etag: string;
            /** Id */
            id: string;
            input_snapshot: components["schemas"]["PredictionInputSnapshot"];
            /** Job Id */
            job_id: string;
            model: components["schemas"]["chrom_platform__modules__prediction__schemas__ModelSnapshot"];
            /** Prediction Record Id */
            prediction_record_id: string;
            /** Project Id */
            project_id: string;
            /** Result */
            result?: (components["schemas"]["CcPredictionResult"] | components["schemas"]["GcPredictionResult"] | components["schemas"]["HplcPredictionResult"] | components["schemas"]["SfcPredictionResult"] | components["schemas"]["TlcPredictionResult"]) | null;
            /** Revision */
            revision: number;
            /** Schema Version */
            schema_version: string;
            /**
             * Source
             * @enum {string}
             */
            source: "web" | "assistant" | "service";
            status: components["schemas"]["JobStatus"];
            /** Tool */
            tool: string;
            /**
             * Updated At
             * Format: date-time
             */
            updated_at: string;
            /** Warnings */
            warnings?: components["schemas"]["Warning"][];
        };
        /** PredictionAccepted */
        PredictionAccepted: {
            /** Id */
            id: string;
            /** Job Id */
            job_id: string;
            /** Prediction Record Id */
            prediction_record_id: string;
            resource_ref: components["schemas"]["ResourceRef"];
            status: components["schemas"]["JobStatus"];
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
        /** PredictionInputSnapshot */
        PredictionInputSnapshot: {
            /** Input */
            input: components["schemas"]["CcInput"] | components["schemas"]["GcInput"] | components["schemas"]["HplcInput"] | components["schemas"]["HplcInputV10"] | components["schemas"]["SfcFullColumnInput"] | components["schemas"]["SfcSingleColumnInput"] | components["schemas"]["TlcInput"] | components["schemas"]["_SfcConditionInput"];
            /** Normalized Structures */
            normalized_structures: string[];
            /** Structure Refs */
            structure_refs: string[];
        };
        /** PublicErrorSummary */
        PublicErrorSummary: {
            /** Code */
            code: string;
            /** Message */
            message: string;
            /**
             * Retryable
             * @default false
             */
            retryable?: boolean;
        };
        /** ResourceRef */
        ResourceRef: {
            /** Id */
            id: string;
            /** Type */
            type: string;
        };
        /** SfcCandidateConditions */
        SfcCandidateConditions: {
            /** Column */
            column: string;
            /** Column Temperature C */
            column_temperature_c: number;
            /** Flow Rate Ml Min */
            flow_rate_ml_min: number;
            /** Inner Diameter Mm */
            inner_diameter_mm: number;
            /** Length Mm */
            length_mm: number;
            /** Mode */
            mode: string;
            /** Particle Size Um */
            particle_size_um: number;
            /** Stationary Phase */
            stationary_phase: string;
        };
        /**
         * SfcFullColumnInput
         * @description Full-column selection uses a server-declared candidate scope.
         */
        SfcFullColumnInput: {
            /** Candidate Scope Id */
            candidate_scope_id: string;
            /** Modifier Components */
            modifier_components: components["schemas"]["ModifierComponent"][];
            /** Modifier Total Fraction */
            modifier_total_fraction: number;
            /** Top K */
            top_k: number;
        };
        /** SfcPredictionResult */
        SfcPredictionResult: {
            applicability: components["schemas"]["Applicability"];
            /** Metrics */
            metrics?: components["schemas"]["Metric"][];
            /** Rows */
            rows: components["schemas"]["SfcResultRow"][];
            /** Schema Version */
            schema_version: string;
            /**
             * Score Semantics
             * @default positive_class_probability
             * @constant
             */
            score_semantics?: "positive_class_probability";
            /**
             * @description discriminator enum property added by openapi-typescript
             * @enum {string}
             */
            task_type: "sfc";
            /** Visualizations */
            visualizations?: components["schemas"]["Visualization"][];
        };
        /** SfcResultRow */
        SfcResultRow: {
            /** Candidate Id */
            candidate_id: string;
            conditions: components["schemas"]["SfcCandidateConditions"];
            /** Rank */
            rank: number;
            /**
             * Score Semantics
             * @constant
             */
            score_semantics: "positive_class_probability";
            /** Separation */
            separation?: number | null;
        };
        /** SfcSingleColumnInput */
        SfcSingleColumnInput: {
            /** Candidate Scope Id */
            candidate_scope_id: string;
            /** Column Id */
            column_id: string;
            /** Modifier Components */
            modifier_components: components["schemas"]["ModifierComponent"][];
            /** Modifier Total Fraction */
            modifier_total_fraction: number;
            /** Top K */
            top_k: number;
        };
        /** TlcInput */
        TlcInput: {
            /** Solvent Components */
            solvent_components: components["schemas"]["TlcSolventComponent"][];
        };
        /** TlcPredictionResult */
        TlcPredictionResult: {
            applicability: components["schemas"]["Applicability"];
            /** Artifact Sha256 */
            artifact_sha256: string;
            /** Ensemble Weights */
            ensemble_weights: {
                [key: string]: number;
            };
            /** Metrics */
            metrics?: components["schemas"]["Metric"][];
            /** Mobile Phase Normalized */
            mobile_phase_normalized: {
                [key: string]: number;
            };
            /** Mobile Phase Original */
            mobile_phase_original: {
                [key: string]: number;
            };
            /** Model Id */
            model_id: string;
            /** Model Version */
            model_version: string;
            /** Rows */
            rows: components["schemas"]["TlcResultRow"][];
            /** Schema Version */
            schema_version: string;
            /**
             * @description discriminator enum property added by openapi-typescript
             * @enum {string}
             */
            task_type: "tlc";
            /** Visualizations */
            visualizations?: components["schemas"]["Visualization"][];
        };
        /** TlcResultRow */
        TlcResultRow: {
            /** Rf */
            rf: number;
        };
        /**
         * TlcSolventComponent
         * @description A public TLC solvent fraction bound to the verified model vocabulary.
         *
         *     The HTTP contract accepts fractions only after the caller has explicitly
         *     converted any UI ratio (for example, ``8:2``) to values in ``[0, 1]``
         *     whose total is one.  Internal model feature construction may still use a
         *     separate ratio normalization boundary, but it must never repair this DTO.
         */
        TlcSolventComponent: {
            /** Fraction */
            fraction: number;
            /**
             * Solvent Id
             * @enum {string}
             */
            solvent_id: "hexane" | "ethyl_acetate" | "dichloromethane" | "methanol" | "diethyl_ether";
        };
        /** Visualization */
        Visualization: {
            /** Data */
            data: {
                [key: string]: unknown;
            };
            /** Kind */
            kind: string;
            /**
             * Origin
             * @enum {string}
             */
            origin: "predicted" | "simulated" | "measured";
            /** Schema Version */
            schema_version: string;
        };
        /** Warning */
        Warning: {
            /** Code */
            code: string;
            /** Field */
            field?: string | null;
            /** Message */
            message: string;
        };
        /** _SfcConditionInput */
        _SfcConditionInput: {
            /** Candidate Scope Id */
            candidate_scope_id: string;
            /** Modifier Components */
            modifier_components: components["schemas"]["ModifierComponent"][];
            /** Modifier Total Fraction */
            modifier_total_fraction: number;
            /** Top K */
            top_k: number;
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
             * @description 需精确选择预处理版本的模型使用该字段；必须与 SHA-256 成对提供。
             */
            preprocessing_profile_id?: string | null;
            /**
             * Preprocessing Profile Sha256
             * @description 需精确选择预处理版本的模型使用该字段；必须与标识成对提供。
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


export const hplc11CandidateConstraints = {
  "rawSmiles": {
    "minItems": 2,
    "maxItems": 2,
    "minLength": 1,
    "maxLength": 20000
  },
  "structureRefs": {
    "minItems": 2,
    "maxItems": 2,
    "pattern": "^[A-Za-z][A-Za-z0-9_.:-]{0,95}$"
  },
  "preprocessingProfileSha256Pattern": "^[0-9a-f]{64}$"
} as const
