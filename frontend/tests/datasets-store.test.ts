import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { bindDataImportValidationReport, parseDataImportValidationReport, useDatasetStore } from '@/modules/datasets/store'
import type { ContractDataImport, ContractDataImportValidation } from '@/core/contract-types'
import type { Artifact } from '@/core/types'

const version = {
  id: 'version-1', project_id: 'project-1', domain_id: 'domain-tlc', version: 1, sha256: 'a'.repeat(64), status: 'READY',
  record_count: 12, source_import_id: 'import-1', snapshot_artifact_id: 'artifact-1', schema_version: 'tlc-v1',
  quality_report_ref: null, created_at: '2026-10-26T00:00:00Z', referenced_by_count: 0,
}

function response(data: unknown) {
  return new Response(JSON.stringify({ data, warnings: [], request_id: 'datasets-test' }), { status: 200, headers: { 'content-type': 'application/json' } })
}

beforeEach(() => { setActivePinia(createPinia()); vi.restoreAllMocks() })

describe('dataset versions', () => {
  it('lists versions in project/domain scope and reads a version detail by id', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response([version]))
      .mockResolvedValueOnce(response(version))
    vi.stubGlobal('fetch', fetchMock)
    const store = useDatasetStore()

    await store.listVersions('project-1', 'domain-tlc')
    await expect(store.getVersion('version-1')).resolves.toMatchObject({ id: 'version-1', record_count: 12 })
    expect(String(fetchMock.mock.calls[0]?.[0])).toContain('/data-versions?project_id=project-1&domain_id=domain-tlc')
    expect(String(fetchMock.mock.calls[1]?.[0])).toContain('/data-versions/version-1')
    expect(store.versions).toEqual([version])
  })

  it('does not write stale version list or detail responses into the shared store', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(response([version]))
      .mockResolvedValueOnce(response(version))
    vi.stubGlobal('fetch', fetchMock)
    const store = useDatasetStore()
    let current = false

    await store.listVersions('project-1', 'domain-tlc', undefined, () => current)
    expect(store.versions).toEqual([])
    await store.getVersion('version-1')
    expect(store.versions).toEqual([])
  })

  it('rejects a list that contains versions from another project or domain', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response([{ ...version, domain_id: 'domain-gc' }])))
    const store = useDatasetStore()
    await expect(store.listVersions('project-1', 'domain-tlc')).rejects.toThrow('其他项目或数据域')
    expect(store.versions).toEqual([])
  })
})

describe('validation report fixtures', () => {
  const counts = { new: 3, updated: 0, unchanged: 0, invalid: 1, duplicate: 0 }
  const reportFixture = (overrides: Record<string, unknown> = {}) => ({
    report_schema_version: 'data-import-validation-report-1', status: 'VALID', project_id: 'project-1', import_id: 'import-1', revision: 1,
    config_hash: 'b'.repeat(64), domain_id: 'domain-tlc', domain_schema_version: 'tlc-v1', source_artifact_id: 'source-1', source_sha256: 'a'.repeat(64),
    encoding: 'utf-8', parser_revision: 'parser-1', row_count: 3, counts, total_issue_count: 1, issues_truncated: false,
    issues: [{ source_row_number: 1000000, measurement_id: 'measurement-1', field: 'rf', raw_value_preview: '😀'.repeat(256), raw_value_sha256: 'c'.repeat(64), raw_value_truncated: true, code: 'INVALID_VALUE', message: 'Synthetic fixture issue' }],
    ...overrides,
  })

  const importFixture = {
    id: 'import-1', project_id: 'project-1', domain_id: 'domain-tlc', artifact_id: 'source-1', schema_version: 'tlc-v1', import_mode: 'append',
    field_mapping: {}, source_description: 'synthetic fixture', revision: 1, config_hash: 'b'.repeat(64), etag: 'etag-1', status: 'READY',
    job_id: null, validation_ref: 'validation-1', new_data_version_id: null, request_snapshot: null,
    created_at: '2026-10-26T00:00:00Z', updated_at: '2026-10-26T00:00:00Z',
  } satisfies ContractDataImport

  const validationFixture = {
    id: 'validation-1', import_id: 'import-1', revision: 1, config_hash: 'b'.repeat(64), status: 'VALID', counts,
    report_artifact_id: 'report-1', parser_revision: 'parser-1', parser_hash: 'd'.repeat(64), warnings: [], created_at: '2026-10-26T00:00:00Z',
  } satisfies ContractDataImportValidation

  const reportArtifact = {
    id: 'report-1', project_id: 'project-1', owner_id: 'actor-1', kind: 'export_report', file_name: 'report.json', media_type: 'application/json',
    size_bytes: 1024, sha256: 'e'.repeat(64), status: 'AVAILABLE', scan_status: 'NOT_CONFIGURED', source_ref: { type: 'data_import_validation', id: 'validation-1' },
    validation_report_ref: null, download_allowed: true, warnings: [], created_at: '2026-10-26T00:00:00Z', updated_at: '2026-10-26T00:00:00Z',
  } satisfies Artifact

  const sourceArtifact = {
    ...reportArtifact, id: 'source-1', kind: 'dataset_package', file_name: 'input.csv', media_type: 'text/csv', size_bytes: 32,
    sha256: 'a'.repeat(64), status: 'AVAILABLE', scan_status: 'PASSED', source_ref: null, download_allowed: false,
  } satisfies Artifact

  it('accepts physical row numbers beyond record count and 256 Unicode code points', () => {
    expect(parseDataImportValidationReport(reportFixture()).issues[0]?.raw_value_preview).toBe('😀'.repeat(256))
    expect(() => parseDataImportValidationReport(reportFixture({ issues: [{ ...reportFixture().issues[0], raw_value_preview: '😀'.repeat(257) }] }))).toThrow('问题列表')
  })

  it('binds only the validation-owned report and source artifact; NOT_CONFIGURED is not scan approval', () => {
    const parsed = parseDataImportValidationReport(reportFixture())
    expect(bindDataImportValidationReport(parsed, validationFixture, importFixture, reportArtifact, sourceArtifact)).toMatchObject({ historical: false })
    expect(() => bindDataImportValidationReport(parsed, validationFixture, importFixture, { ...reportArtifact, download_allowed: false }, sourceArtifact)).toThrow('不匹配')
    const historicalValidation = { ...validationFixture, status: 'STALE' } satisfies ContractDataImportValidation
    const laterImport = { ...importFixture, revision: 2, config_hash: 'f'.repeat(64), validation_ref: 'validation-2' } satisfies ContractDataImport
    expect(bindDataImportValidationReport(parsed, historicalValidation, laterImport, reportArtifact, sourceArtifact).historical).toBe(true)
  })
})
