import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useKnowledgeStore } from '@/modules/knowledge/store'
import { useTrainingStore } from '@/modules/training/store'
import { useModelCenterStore } from '@/modules/model_center/store'
import { makeResumeBody, useAssistantStore } from '@/modules/assistant/store'
import type {
  ContractKnowledgeDocumentCreateBody,
  ContractKnowledgeSearchBody,
} from '@/core/contract-types'

function jsonResponse(data: unknown, status = 200, headers: Record<string, string> = {}) {
  return new Response(JSON.stringify({ data, warnings: [], request_id: `server-${status}` }), {
    status,
    headers: { 'content-type': 'application/json', ...headers },
  })
}

function requestAt(fetchMock: { mock: { calls: unknown[][] } }, index: number) {
  const [input, init] = fetchMock.mock.calls[index] as [RequestInfo | URL, RequestInit?]
  return {
    url: String(input),
    headers: new Headers(init?.headers),
    body: init?.body === undefined ? undefined : JSON.parse(String(init.body)),
  }
}

describe('M3/M4 knowledge, training and model-center workflow contracts', () => {
  beforeEach(() => setActivePinia(createPinia()))
  afterEach(() => vi.restoreAllMocks())

  it('keeps knowledge project scope in query/body and retains accepted metadata', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(jsonResponse([]))
      .mockResolvedValueOnce(jsonResponse({ citations: [] }))
      .mockResolvedValueOnce(jsonResponse({ id: 'document-1' }, 201, { Location: '/api/v1/knowledge/documents/document-1' }))

    const store = useKnowledgeStore()
    await store.listDocuments('project/one', 'cursor/1')
    await store.searchKnowledge({ project_id: 'project/one', query: 'silica' } as ContractKnowledgeSearchBody)
    await store.createDocument({
      project_id: 'project/one',
      artifact_id: 'artifact-1',
      title: 'A paper',
      source_type: 'paper',
      source_description: 'A source document',
    } as ContractKnowledgeDocumentCreateBody)

    expect(requestAt(fetchMock, 0).url).toBe('/api/v1/knowledge/documents?project_id=project%2Fone&cursor=cursor%2F1')
    expect(requestAt(fetchMock, 1).url).toBe('/api/v1/knowledge/search')
    expect(requestAt(fetchMock, 1).body).toMatchObject({ project_id: 'project/one', query: 'silica' })
    expect(requestAt(fetchMock, 2).body).toMatchObject({ project_id: 'project/one', artifact_id: 'artifact-1' })
    expect(requestAt(fetchMock, 2).headers.get('Idempotency-Key')).toMatch(/^idem-/)
    expect(store.location).toBe('/api/v1/knowledge/documents/document-1')
  })

  it('sends training project scope, optimistic concurrency headers and confirmation submit body', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(jsonResponse([]))
      .mockResolvedValueOnce(jsonResponse({ id: 'experiment-1' }))
      .mockResolvedValueOnce(jsonResponse({ valid: true }))
      .mockResolvedValueOnce(jsonResponse({ id: 'experiment-1', status: 'QUEUED' }, 202, { Location: '/api/v1/jobs/job-training-1' }))

    const store = useTrainingStore()
    await store.listExperiments('project/one', 'cursor/2')
    await store.getExperiment('experiment/1', 'project/one')
    await store.validateExperiment('experiment-1', 'project/one', '"experiment-v3"')
    await store.submitExperiment('experiment-1', 'project/one', 'confirmation-1')

    expect(requestAt(fetchMock, 0).url).toBe('/api/v1/training/experiments?project_id=project%2Fone&cursor=cursor%2F2')
    expect(requestAt(fetchMock, 1).url).toBe('/api/v1/training/experiments/experiment%2F1?project_id=project%2Fone')
    expect(requestAt(fetchMock, 2).url).toBe('/api/v1/training/experiments/experiment-1/validate?project_id=project%2Fone')
    expect(requestAt(fetchMock, 2).body).toEqual({})
    expect(requestAt(fetchMock, 2).headers.get('If-Match')).toBe('"experiment-v3"')
    expect(requestAt(fetchMock, 2).headers.get('Idempotency-Key')).toMatch(/^idem-/)
    expect(requestAt(fetchMock, 3).url).toBe('/api/v1/training/experiments/experiment-1/submit?project_id=project%2Fone')
    expect(requestAt(fetchMock, 3).body).toEqual({ confirmation_id: 'confirmation-1' })
    expect(requestAt(fetchMock, 3).headers.get('Idempotency-Key')).toMatch(/^idem-/)
    expect(store.location).toBe('/api/v1/jobs/job-training-1')
  })

  it('requires frozen headers and strict bodies for knowledge indexing and archive confirmation', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(jsonResponse({ id: 'knowledge-1', job_id: 'job-index', resource_ref: { id: 'knowledge-1', type: 'knowledge_document' }, status: 'INDEXING' }, 202, { Location: '/api/v1/jobs/job-index' }))
      .mockResolvedValueOnce(jsonResponse({ id: 'knowledge-1', status: 'ARCHIVED' }, 200))
    const store = useKnowledgeStore()
    await store.indexDocument('knowledge-1', 'project/one', '"knowledge-v2"')
    await store.archiveDocument('knowledge-1', 'project/one', 'confirmation-knowledge-1')
    expect(requestAt(fetchMock, 0).url).toBe('/api/v1/knowledge/documents/knowledge-1/index?project_id=project%2Fone')
    expect(requestAt(fetchMock, 0).body).toEqual({})
    expect(requestAt(fetchMock, 0).headers.get('If-Match')).toBe('"knowledge-v2"')
    expect(requestAt(fetchMock, 0).headers.get('Idempotency-Key')).toMatch(/^idem-/)
    expect(requestAt(fetchMock, 1).body).toEqual({ confirmation_id: 'confirmation-knowledge-1' })
    expect(requestAt(fetchMock, 1).headers.get('Idempotency-Key')).toMatch(/^idem-/)
  })

  it('keeps model-center project scope and async validation/deployment locations', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch')
      .mockResolvedValueOnce(jsonResponse([]))
      .mockResolvedValueOnce(jsonResponse([]))
      .mockResolvedValueOnce(jsonResponse({ id: 'registration-1', status: 'VALIDATING' }, 202, { Location: '/api/v1/jobs/job-registration-1' }))
      .mockResolvedValueOnce(jsonResponse({ id: 'deployment-1', status: 'QUEUED' }, 202, { Location: '/api/v1/jobs/job-deployment-1' }))

    const store = useModelCenterStore()
    await store.listModels('project/one', 'cursor/models')
    await store.listDeployments('project/one', 'cursor/deployments')
    await store.validateRegistration('registration-1', 'project/one', '"registration-v2"')
    await store.submitDeployment('draft-1', 'project/one', 'confirmation-deploy-1')

    expect(requestAt(fetchMock, 0).url).toBe('/api/v1/models?project_id=project%2Fone&cursor=cursor%2Fmodels')
    expect(requestAt(fetchMock, 1).url).toBe('/api/v1/model-deployments?project_id=project%2Fone&cursor=cursor%2Fdeployments')
    expect(requestAt(fetchMock, 2).url).toBe('/api/v1/models/registrations/registration-1/validate?project_id=project%2Fone')
    expect(requestAt(fetchMock, 2).headers.get('If-Match')).toBe('"registration-v2"')
    expect(requestAt(fetchMock, 2).headers.get('Idempotency-Key')).toMatch(/^idem-/)
    expect(requestAt(fetchMock, 3).url).toBe('/api/v1/model-deployment-drafts/draft-1/submit?project_id=project%2Fone')
    expect(requestAt(fetchMock, 3).body).toEqual({ confirmation_id: 'confirmation-deploy-1' })
    expect(requestAt(fetchMock, 3).headers.get('Idempotency-Key')).toMatch(/^idem-/)
    expect(store.location).toBe('/api/v1/jobs/job-deployment-1')
  })
})

describe('M4 assistant resume discriminated union', () => {
  beforeEach(() => setActivePinia(createPinia()))
  afterEach(() => vi.restoreAllMocks())

  it('distinguishes recovery from confirmation and blocks a missing confirmation id', () => {
    expect(makeResumeBody('recovery')).toEqual({ reason: 'recovery' })
    expect(makeResumeBody('confirmation', 'confirmation-1')).toEqual({ reason: 'confirmation', confirmation_id: 'confirmation-1' })
    expect(() => makeResumeBody('confirmation')).toThrow('confirmation resume requires confirmation_id')
  })

  it('sends If-Match/idempotency headers and retains 202 Location while resuming', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValueOnce(
      jsonResponse({ id: 'run-1', status: 'QUEUED' }, 202, { Location: '/api/v1/agent-runs/run-1' }),
    )
    const store = useAssistantStore()
    await store.resumeRun('run-1', makeResumeBody('confirmation', 'confirmation-1'), '"run-v4"')

    const request = requestAt(fetchMock, 0)
    expect(request.url).toBe('/api/v1/agent-runs/run-1/resume')
    expect(request.body).toEqual({ reason: 'confirmation', confirmation_id: 'confirmation-1' })
    expect(request.headers.get('If-Match')).toBe('"run-v4"')
    expect(request.headers.get('Idempotency-Key')).toMatch(/^idem-/)
    expect(store.location).toBe('/api/v1/agent-runs/run-1')
  })
})
