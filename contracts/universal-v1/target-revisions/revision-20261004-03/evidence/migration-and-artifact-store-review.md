# Migration and ArtifactStore review notes

**Status: candidate evidence and verification plan only.** No database migration or live service/storage operation was performed.

## Existing database at revision 0013

The current graph has two M2 branches: `0011_m2_batch_prediction` descends from `0010_model_center_job_fencing`; `0012_m2_record_export` descends from `0007_worker_outbox_lease`; `0013_merge_m2_batch_export` merges both parents and is the prior universal M2 head. `0014_batch_resolved_snapshot_export` has a single `down_revision` of `0013_merge_m2_batch_export`, so an existing database whose Alembic version is 0013 advances through one additive revision without replaying either branch.

Revision 0014 adds snapshot/hash/fencing-related columns to existing batch tables. Most new snapshot/binding fields are nullable so rows already present at 0013 remain readable. The new `batch_export.status` is non-null with server default `QUEUED`; old export rows therefore receive that value, but have no `source_snapshot`, `source_snapshot_hash`, or `artifact_id` from the old schema. Such rows are not replayable from the immutable snapshot contract and must be reconciled before dispatch/download; do not infer success from the linked Job or regenerate without an audited source snapshot. Check the old `batch_export` row count/status and linked Jobs before upgrading a non-empty database.

The same rule applies to old prediction rows: 0014 does not fabricate `validation_id`, `resolved_input_snapshot`, per-row resolved hashes, or the aggregate resolved-input hash. `BatchPredictionWorker` requires a READY validation with a complete, hash-consistent validation-item set and matching row-result projections before it marks the job RUNNING. Rows carried forward from 0013 without those bindings fail with `BATCH_LEGACY_DATA_REQUIRES_REVALIDATION`; they are retained unchanged for audit/recovery and cannot be reported as a successful empty batch. Operators must preserve the old rows, create a fresh validation from the approved source artifact and current model/schema snapshot, then submit that new immutable snapshot. No automatic backfill is attempted because the old inputs may not prove the same resolution semantics.

Downgrade from 0014 drops the added fields and destroys resolved-input/export snapshot evidence. Back up the database first and do not use downgrade as a routine rollback after batch execution. The DDL is migration-owned; do not hand-add columns. Offline SQL generation and Alembic head topology are covered by the targeted migration/profile tests, but they do not prove compatibility against a customer database or execute the migration.

## Production ArtifactStore verification plan

Production worker assembly reads `ARTIFACT_ROOT` and constructs `LocalArtifactStore` from that project configuration. Before registering a batch handler it checks the store contract, configured state, size limit, then writes, reads back, and removes a small server-generated probe object. Missing, misconfigured, unwritable, or non-conforming stores fail closed with `BATCH_ARTIFACT_STORE_UNAVAILABLE`; storage paths and backend exceptions are not exposed. The explicit in-memory fake store in the worker CLI test is only a dispatch-wiring fixture; it is not evidence of deployment storage.

Before accepting a real profile, perform these checks in an isolated deployment environment:

1. Read the configured `ARTIFACT_ROOT` path without logging secrets or customer file names; verify it resolves under the approved data volume and the service identity can create/read/delete files there.
2. Through the actual `LocalArtifactStore` instance, write and read a small generated payload under a server-generated attempt/fencing key, compare byte count and SHA-256, then remove that exact temporary object.
3. Submit a batch export through the configured profile and verify the committed `Artifact` row has `kind=batch_export`, `status=AVAILABLE`, matching project/source metadata, a storage key unique to its attempt/fencing identity, and the same SHA-256 as the stored bytes. Confirm project-scoped status/download behavior.
4. In a disposable database and storage root, interleave lease loss and retry. Verify the old unpublished key is removed while the newer attempt's committed key, file, size, and SHA-256 remain available.
5. Record the profile/build ID, non-secret storage-root identity, migration head before/after, artifact ID, storage-key hashes, and observed status codes. Do not treat the SQLite unit test or a healthy container as this production acceptance.

## Current evidence

- Compatibility test `test_0013_legacy_batch_rows_are_preserved_but_cannot_succeed_without_resolved_snapshot` seeds a migrated-schema simulation with NULL 0014 bindings and a pre-0014 row (`validation_id=NULL`). It verifies the worker rejects before adapter execution, preserves row bytes/status, and leaves the queued job unreported as successful. This is a migration-era compatibility simulation, not execution of 0014 against a real 0013 database.
- Unit race test uses a temporary SQLite database and a real `LocalArtifactStore` under pytest's temporary directory. It exercises the requested old-lease/new-attempt commit/old-cleanup sequence and checks the surviving file hash.
- Download tests assert ArtifactService 403, 404, and 409 status/code pass through; an unknown exception maps to a retryable 503.
- Worker CLI wiring tests separately cover an explicitly injected in-memory fake store and fail-closed assembly for missing, `object()`, and unconfigured stores. A LocalArtifactStore probe test uses only a pytest temporary root and verifies no probe file remains.
- No real project `ARTIFACT_ROOT`, deployment identity, database at 0013, migration execution, or live HTTP download was exercised in this task.
