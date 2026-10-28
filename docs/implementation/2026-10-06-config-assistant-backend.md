# Assistant LLM configuration lifecycle — S1

## Scope and ownership

S1 adds an assistant-owned configuration lifecycle using the existing `llm_configuration` and `llm_test_result` tables. The settings API is registered only when the assistant module is selected. `GET /settings/public` remains available from the same settings router.

This change owns `assistant/settings_schemas.py`, `assistant/settings_service.py`, `assistant/settings_router.py`, `assistant/secret_store.py`, the relevant mappings in `assistant/persistence.py` and `assistant/schemas.py`, `alembic/versions/0020_assistant_llm_configuration_lifecycle.py`, the settings router composition in `assembly/m4.py`, this record, the §19.1 policy text, and `tests/test_assistant_settings.py`. Assistant-only package trimming already copies the assistant module recursively and selects assistant-named migrations; no `profile.py` edit was needed. `service.py`, `production_worker.py`, `worker_cli.py`, and `security.py` are owned by the Run binding/worker owner.

## API and state

The public DTOs use strict `extra=forbid` validation. `LlmSettingsView` is `{active_configuration,active_revision,candidate_configurations}` with an integer active revision. `LlmTestAccepted` is `{id,job_id,status,resource_ref}`. Test results expose exactly four checks—`connectivity`, `streaming`, `tool_calling`, and `structured_output`—whose values are `PASSED`, `FAILED`, or `NOT_SUPPORTED`.

The routes are:

| Operation | Result behavior |
| --- | --- |
| `GET /settings/public` | Public build and input/export limits; no credentials. |
| `GET /settings/llm` | Current pointer and candidates; `ETag` is the active pointer ETag, initially `"active-0"`. |
| `POST /settings/llm/configurations` | `201`, `Location: /api/v1/settings/llm`, and configuration `ETag`; accepts `Idempotency-Key`. |
| `POST /settings/llm/configurations/{id}/test` | `202`, accepted test with durable `job_id`, and `Location: /api/v1/settings/llm/tests/{test_id}`; accepts `Idempotency-Key`. |
| `GET /settings/llm/tests/{id}` | Durable queued/running/final test state. |
| `POST /settings/llm/configurations/{id}/activate` | Requires `Idempotency-Key`, `If-Match`, `expected_active_revision`, and a successful test for the exact configuration revision and hash; returns new settings and active-pointer `ETag`. |

Reusing an idempotency key with a different request returns `409 IDEMPOTENCY_KEY_REUSED`; an in-progress replay returns `409 OPERATION_IN_PROGRESS`. Missing `Idempotency-Key` returns 400. Missing `If-Match` returns 428, a stale revision returns 409, and a mismatched ETag returns 412. Permission errors are 403, unseen project resources are 404, and invalid provider-policy inputs are 422. Successful response envelopes are recorded in the shared idempotency store for replay.

Candidate creation writes a new immutable revision. Testing creates `llm_test_result`, shared `Job`, `JobAttempt`, and `job.execute` Outbox rows in the request transaction. The Outbox payload remains exactly `{job_id,attempt,dispatch_id}`. The API never marks a test successful. The separate Worker owner registered the production runner as `assistant_llm_configuration_test` / `llm_test`; it uses the configured adapter, protected key reference, and approved endpoint. Only that handler can write final checks and promote a matching configuration to `TESTED`. Failure changes only that test result and cannot change the active pointer. Tests may report `structured_output: NOT_SUPPORTED`; connectivity, streaming, and tool-calling must pass for activation.

`external_send_policy` is `deny_external` or `approved_endpoint_only`. The settings service, test submission, and activation all reuse `security.trusted_provider_url` with the inherited, protected process setting `LLM_PROVIDER_APPROVED_ENDPOINTS`. Only an exact full HTTPS base endpoint match is accepted; comparison includes scheme, host, effective port, and base path, ignoring only a trailing slash. Missing policy configuration, `deny_external`, unknown policy, or an unmatched endpoint fails closed. The setting is not loaded from `.env` by this code. The worker/adapter owner uses the same helper at execution time and disables redirects. This change did not call an external provider.

## Secret storage and Run snapshots

New API keys are accepted once as `SecretStr`, encrypted with Windows CurrentUser DPAPI, and atomically stored under the absolute directory supplied by `CHROM_ASSISTANT_SECRET_ROOT`. The directory must be named `assistant-llm-secrets`, reside under `%LOCALAPPDATA%\ChromatographyPlatform`, and have no reparse points in the checked path. Files and the secret directory receive a protected DACL for the current user and SYSTEM. Missing, relative, escaping, or invalid paths make new-key writes unavailable; there is no fallback to the old `priority-local` path. The database stores only an opaque `dpapi:v1:<uuidhex>` reference. The code does not read or reuse `SECRET_KEY_FILE`, does not load `.env`, and does not echo keys. It writes encrypted bytes before persisting a DB reference; a DB failure can leave an orphan ciphertext, which requires controlled manual cleanup. Activation, failure, and rollback never delete old ciphertext.

Migration 0020 adds nullable `agent_run.provider_configuration_hash` for historical compatibility. New Runs must bind the exact configuration ID, revision, and 64-character configuration hash. Historical Runs missing any binding element fail closed; they must not use whichever configuration is active later. The Run owner changed the message creation path and worker validation in its owned files to resolve the immutable binding and permit a retired configuration only when the exact project, revision, hash, and secret reference match.

## Migration behavior and operational boundary

Migration 0020 expands configuration status to include `TESTED`, adds test job/hash fields and the active-pointer table, and adds `agent_run.provider_configuration_hash`. Before any DDL it checks each project's existing `ACTIVE` row count. Exactly one existing active row is backfilled to pointer revision 1 with ETag `"active-1"`; more than one causes a clear failure before DDL instead of choosing by timestamp. MySQL DDL is not transactional: if another failure occurs after DDL begins, preserve the database evidence and migration state for review; do not blindly rerun or auto-clean.

The active pointer is locked during activation and updated with a monotonically increasing integer revision and ETag. The API checks both `If-Match` and `expected_active_revision`. SQLite route tests cover a stale contender after a winner; this does not establish MySQL locking or production concurrency acceptance.

## Verification and remaining acceptance

`D:\Anaconda\envs\SFC\python.exe -m pytest tests/test_assistant_settings.py -q` passed 8 tests. Coverage includes DTO strictness and secret redaction, exact endpoint policy checks, create/test idempotency and replay conflict, ETag/Location and OpenAPI header behavior, durable test Job/Attempt/Outbox state, active pointer/CAS behavior, protected-root resolution, and Windows same-identity DPAPI round-trip and corrupted ciphertext failure. No external provider was contacted. Worker unit tests may inject a fake runner to test queue/fence/status writes; the registered production runner uses the real adapter, and no provider request was executed in this turn.

An additional compatibility run of `test_assistant_router_contract.py`, `test_m4_assembly_integration.py`, `test_m4_integration.py`, and `test_prediction_profile_compat.py` had failures while the Run owner was editing overlapping runtime files. Five M4 integration fixtures lacked the now-required active pointer; profile hash determinism also observed `production_worker.py` changing between two copies during the run. These are reported to that owner for fixture updates and a rerun; this settings owner did not change those files.

Still outside this S1 verification: the worker's actual provider request sequence and recovery after restart, MySQL migration 0020 execution, and real MySQL concurrent activation. The API enqueues durable work and polls the persisted record; it must not be described as a completed provider test until the Worker handler is exercised against an explicitly approved provider. Production E2E, browser, and deployment acceptance remain pending.
