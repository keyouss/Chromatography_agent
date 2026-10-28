/**
 * Candidate-local generated types for the universal-v1 Settings API projection.
 * Candidate: universal-v1-config-assistant-revision-20261006-09 (PREVIEW_NOT_FROZEN).
 * Input SHA-256: 1E7D7169E342247315657A51F2137012CC7411B1E0127D82A39450A2A2147F31.
 * Generated with openapi-typescript 7.13.0; never import from core aliases or formal contracts.
 */
export interface paths {
    "/api/v1/settings/public": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Public */
        get: operations["get_public_settings"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/settings/llm": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Llm */
        get: operations["get_llm_settings"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/settings/llm/configurations": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Create Llm */
        post: operations["create_llm_configuration"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/settings/llm/configurations/{configuration_id}/test": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Test Llm */
        post: operations["test_llm_configuration"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/settings/llm/tests/{test_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Llm Test */
        get: operations["get_llm_test"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/v1/settings/llm/configurations/{configuration_id}/activate": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Activate Llm */
        post: operations["activate_llm_configuration"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
}
export type webhooks = Record<string, never>;
export interface components {
    schemas: {
        /** ErrorDetail */
        ErrorDetail: {
            /** Code */
            code: string;
            /** Details */
            details?: {
                [key: string]: unknown;
            };
            /** Field Errors */
            field_errors?: {
                [key: string]: string;
            }[];
            /** Message */
            message: string;
            /**
             * Retryable
             * @default false
             */
            retryable?: boolean;
        };
        /** ErrorEnvelope */
        ErrorEnvelope: {
            error: components["schemas"]["ErrorDetail"];
            /** Request Id */
            request_id: string;
        };
        /** LlmConfigurationActivate */
        LlmConfigurationActivate: {
            /** Expected Active Revision */
            expected_active_revision: number;
            /** Test Id */
            test_id: string;
        };
        /** LlmConfigurationCreate */
        LlmConfigurationCreate: {
            /**
             * Api Format
             * @default openai_chat
             * @constant
             */
            api_format?: "openai_chat";
            /** Api Key */
            api_key?: string | null;
            /** Base Url */
            base_url: string;
            /**
             * External Send Policy
             * @default deny_external
             * @enum {string}
             */
            external_send_policy?: "deny_external" | "approved_endpoint_only";
            /**
             * Max Output Tokens
             * @default 4096
             */
            max_output_tokens?: number;
            /**
             * Max Retries
             * @default 2
             */
            max_retries?: number;
            /** Model Id */
            model_id: string;
            /** Name */
            name: string;
            /** Reuse Secret From Configuration Id */
            reuse_secret_from_configuration_id?: string | null;
            /**
             * Timeout Seconds
             * @default 90
             */
            timeout_seconds?: number;
        };
        /** LlmConfigurationTestRequest */
        LlmConfigurationTestRequest: Record<string, never>;
        /** LlmConfigurationView */
        LlmConfigurationView: {
            /**
             * Api Format
             * @constant
             */
            api_format: "openai_chat";
            /** Base Url */
            base_url: string;
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /** Etag */
            etag: string;
            /**
             * External Send Policy
             * @enum {string}
             */
            external_send_policy: "deny_external" | "approved_endpoint_only";
            /** Has Api Key */
            has_api_key: boolean;
            /** Id */
            id: string;
            /** Last Test Id */
            last_test_id?: string | null;
            /** Limits */
            limits: {
                [key: string]: number;
            };
            /** Model Id */
            model_id: string;
            /** Name */
            name: string;
            /** Revision */
            revision: string;
            /**
             * Status
             * @enum {string}
             */
            status: "CANDIDATE" | "TESTED" | "ACTIVE" | "RETIRED";
        };
        /** LlmSettingsView */
        LlmSettingsView: {
            active_configuration?: components["schemas"]["LlmConfigurationView"] | null;
            /** Active Revision */
            active_revision: number;
            /** Candidate Configurations */
            candidate_configurations: components["schemas"]["LlmConfigurationView"][];
        };
        /** LlmTestAccepted */
        LlmTestAccepted: {
            /** Id */
            id: string;
            /** Job Id */
            job_id: string;
            resource_ref: components["schemas"]["ResourceRef"];
            /**
             * Status
             * @constant
             */
            status: "QUEUED";
        };
        /** LlmTestChecks */
        LlmTestChecks: {
            /**
             * Connectivity
             * @enum {string}
             */
            connectivity: "PASSED" | "FAILED" | "NOT_SUPPORTED";
            /**
             * Streaming
             * @enum {string}
             */
            streaming: "PASSED" | "FAILED" | "NOT_SUPPORTED";
            /**
             * Structured Output
             * @enum {string}
             */
            structured_output: "PASSED" | "FAILED" | "NOT_SUPPORTED";
            /**
             * Tool Calling
             * @enum {string}
             */
            tool_calling: "PASSED" | "FAILED" | "NOT_SUPPORTED";
        };
        /** LlmTestResult */
        LlmTestResult: {
            checks: components["schemas"]["LlmTestChecks"];
            /** Configuration Id */
            configuration_id: string;
            /** Configuration Revision */
            configuration_revision: string;
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /** Error */
            error?: {
                [key: string]: unknown;
            } | null;
            /** Finished At */
            finished_at?: string | null;
            /** Id */
            id: string;
            /**
             * Status
             * @enum {string}
             */
            status: "QUEUED" | "RUNNING" | "SUCCEEDED" | "FAILED";
        };
        /** PublicSettingsView */
        PublicSettingsView: {
            /** Allowed Modes */
            allowed_modes: string[];
            /** Build Id */
            build_id: string;
            /** Built Modules */
            built_modules: string[];
            /** Default Project Id */
            default_project_id?: string | null;
            /** Export Limits */
            export_limits: {
                [key: string]: number;
            };
            /** Input Limits */
            input_limits: {
                [key: string]: number;
            };
            /** Product Name */
            product_name: string;
        };
        /** ResourceRef */
        ResourceRef: {
            /** Id */
            id: string;
            /** Type */
            type: string;
        };
        /** SuccessEnvelope[LlmConfigurationView] */
        SuccessEnvelope_LlmConfigurationView_: {
            data: components["schemas"]["LlmConfigurationView"];
            /** Request Id */
            request_id: string;
            /** Warnings */
            warnings?: components["schemas"]["WarningDTO"][];
        };
        /** SuccessEnvelope[LlmSettingsView] */
        SuccessEnvelope_LlmSettingsView_: {
            data: components["schemas"]["LlmSettingsView"];
            /** Request Id */
            request_id: string;
            /** Warnings */
            warnings?: components["schemas"]["WarningDTO"][];
        };
        /** SuccessEnvelope[LlmTestAccepted] */
        SuccessEnvelope_LlmTestAccepted_: {
            data: components["schemas"]["LlmTestAccepted"];
            /** Request Id */
            request_id: string;
            /** Warnings */
            warnings?: components["schemas"]["WarningDTO"][];
        };
        /** SuccessEnvelope[LlmTestResult] */
        SuccessEnvelope_LlmTestResult_: {
            data: components["schemas"]["LlmTestResult"];
            /** Request Id */
            request_id: string;
            /** Warnings */
            warnings?: components["schemas"]["WarningDTO"][];
        };
        /** SuccessEnvelope[PublicSettingsView] */
        SuccessEnvelope_PublicSettingsView_: {
            data: components["schemas"]["PublicSettingsView"];
            /** Request Id */
            request_id: string;
            /** Warnings */
            warnings?: components["schemas"]["WarningDTO"][];
        };
        /** WarningDTO */
        WarningDTO: {
            /** Code */
            code: string;
            /** Field */
            field?: string | null;
            /** Message */
            message: string;
            resource_ref?: components["schemas"]["ResourceRef"] | null;
        };
    };
    responses: never;
    parameters: never;
    requestBodies: never;
    headers: never;
    pathItems: never;
}
export type $defs = Record<string, never>;
export interface operations {
    get_public_settings: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SuccessEnvelope_PublicSettingsView_"];
                };
            };
            /** @description 统一 API 错误 */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description 统一 API 错误 */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description 统一 API 错误 */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description 统一 API 错误 */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description 统一 API 错误 */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description 统一 API 错误 */
            410: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description 统一 API 错误 */
            412: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description 统一 API 错误 */
            413: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description 统一 API 错误 */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description 统一 API 错误 */
            428: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description 统一 API 错误 */
            429: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description 统一 API 错误 */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description 统一 API 错误 */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
        };
    };
    get_llm_settings: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    ETag: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SuccessEnvelope_LlmSettingsView_"];
                };
            };
            /** @description 统一 API 错误 */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description 统一 API 错误 */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description 统一 API 错误 */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description 统一 API 错误 */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description 统一 API 错误 */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description 统一 API 错误 */
            410: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description 统一 API 错误 */
            412: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description 统一 API 错误 */
            413: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description 统一 API 错误 */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description 统一 API 错误 */
            428: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description 统一 API 错误 */
            429: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description 统一 API 错误 */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description 统一 API 错误 */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
        };
    };
    create_llm_configuration: {
        parameters: {
            query?: never;
            header: {
                "Idempotency-Key": string;
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["LlmConfigurationCreate"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    ETag: string;
                    Location: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SuccessEnvelope_LlmConfigurationView_"];
                };
            };
            /** @description 统一 API 错误 */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description 统一 API 错误 */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description 统一 API 错误 */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description 统一 API 错误 */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description 统一 API 错误 */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description 统一 API 错误 */
            410: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description 统一 API 错误 */
            412: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description 统一 API 错误 */
            413: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description 统一 API 错误 */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description 统一 API 错误 */
            428: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description 统一 API 错误 */
            429: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description 统一 API 错误 */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description 统一 API 错误 */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
        };
    };
    test_llm_configuration: {
        parameters: {
            query?: never;
            header: {
                "Idempotency-Key": string;
            };
            path: {
                configuration_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["LlmConfigurationTestRequest"];
            };
        };
        responses: {
            /** @description 测试任务已接受 */
            202: {
                headers: {
                    Location: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SuccessEnvelope_LlmTestAccepted_"];
                };
            };
            /** @description 统一 API 错误 */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description 统一 API 错误 */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description 统一 API 错误 */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description 统一 API 错误 */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description 统一 API 错误 */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description 统一 API 错误 */
            410: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description 统一 API 错误 */
            412: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description 统一 API 错误 */
            413: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description 统一 API 错误 */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description 统一 API 错误 */
            428: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description 统一 API 错误 */
            429: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description 统一 API 错误 */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description 统一 API 错误 */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
        };
    };
    get_llm_test: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                test_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SuccessEnvelope_LlmTestResult_"];
                };
            };
            /** @description 统一 API 错误 */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description 统一 API 错误 */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description 统一 API 错误 */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description 统一 API 错误 */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description 统一 API 错误 */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description 统一 API 错误 */
            410: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description 统一 API 错误 */
            412: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description 统一 API 错误 */
            413: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description 统一 API 错误 */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description 统一 API 错误 */
            428: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description 统一 API 错误 */
            429: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description 统一 API 错误 */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description 统一 API 错误 */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
        };
    };
    activate_llm_configuration: {
        parameters: {
            query?: never;
            header: {
                "Idempotency-Key": string;
                "If-Match": string;
            };
            path: {
                configuration_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["LlmConfigurationActivate"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    ETag: string;
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SuccessEnvelope_LlmSettingsView_"];
                };
            };
            /** @description 统一 API 错误 */
            400: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description 统一 API 错误 */
            401: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description 统一 API 错误 */
            403: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description 统一 API 错误 */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description 统一 API 错误 */
            409: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description 统一 API 错误 */
            410: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description 统一 API 错误 */
            412: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description 统一 API 错误 */
            413: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description 统一 API 错误 */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description 统一 API 错误 */
            428: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description 统一 API 错误 */
            429: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description 统一 API 错误 */
            500: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
            /** @description 统一 API 错误 */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorEnvelope"];
                };
            };
        };
    };
}
