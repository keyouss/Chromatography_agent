type FixtureMode = 'many-ready' | 'no-ready' | 'scope-mismatch'

const healthReady = { status: 'READY', reason: null, checked_at: '2026-10-20T00:00:00Z' }
const healthUnavailable = { status: 'UNAVAILABLE', reason: 'SYNTHETIC_FIXTURE_UNAVAILABLE', checked_at: '2026-10-20T00:00:00Z' }
const modelSpecs = [
  { id: 'SYNTHETIC_MODEL_A', version: '1.1-A', threshold: '1.2' },
  { id: 'SYNTHETIC_MODEL_B', version: '1.1-B', threshold: '1.5' },
]
let mode: FixtureMode = 'many-ready'
let pendingModelA: Array<(response: Response) => void> = []
const nativeFetch = window.fetch.bind(window)

function envelope(data: unknown): Response {
  return new Response(JSON.stringify({ data, request_id: 'synthetic-browser-fixture', warnings: [] }), {
    status: 200,
    headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' },
  })
}

function syntheticModels() {
  const unavailable = mode === 'no-ready'
  return modelSpecs.map((model) => ({
    id: model.id,
    version: model.version,
    task_type: 'sfc',
    scope: 'SYNTHETIC_BUSINESS_SCOPE',
    input_schema_version: '1.1',
    output_schema_version: 'synthetic-sfc-output-1.1',
    threshold: model.threshold,
    sfc_domain: { model_scope: 'all_column' },
    health: unavailable ? healthUnavailable : healthReady,
  }))
}

function syntheticTools() {
  const ids = mode === 'scope-mismatch' ? ['single_column', 'full_column'] : ['full_column', 'single_column']
  return ids.map((id) => ({
    id,
    display_name: id === 'single_column' ? 'SYNTHETIC 单柱搜索' : 'SYNTHETIC 全柱搜索',
    schema_version: '1.1',
    health: healthReady,
    limits: { max_candidates: 20, max_evaluations: 20, max_rows: 20 },
    model_constraints: {},
  }))
}

function toolSchema(toolId: string) {
  const tool = syntheticTools().find((item) => item.id === toolId)
  if (!tool) throw new Error(`Unknown synthetic tool: ${toolId}`)
  return {
    ...tool,
    input_schema: {
      type: 'object',
      properties: {
        top_k: { type: 'integer', minimum: 1, maximum: 20 },
        modifier_components: { type: 'array', minItems: 1, maxItems: 3, items: { type: 'object', properties: { fraction: { type: 'number', minimum: 0, maximum: 1 } } } },
        modifier_total_fraction: { type: 'number', minimum: 0, maximum: 1 },
      },
    },
    output_schema: {},
    structure_count: { min: 2, max: 2, semantics: 'pair' },
    units: {},
    supported_visualizations: [],
  }
}

function optionFixture(modelId: string) {
  const model = modelSpecs.find((candidate) => candidate.id === modelId)
  if (!model) throw new Error('Unknown synthetic model options request')
  const marker = model.id === 'SYNTHETIC_MODEL_A' ? 'SYNTHETIC_STALE_A' : 'SYNTHETIC_CURRENT_B'
  const keys = ['column', 'stationary_phase', 'mode', 'length_mm', 'inner_diameter_mm', 'particle_size_um', 'column_temperature_c', 'flow_rate_ml_min'] as const
  const values: Record<(typeof keys)[number], Array<string | number>> = {
    column: [`${marker}_COLUMN`], stationary_phase: [`${marker}_PHASE`], mode: [`${marker}_MODE`], length_mm: [150],
    inner_diameter_mm: [4.6], particle_size_um: [5], column_temperature_c: [35], flow_rate_ml_min: [2.5],
  }
  return {
    project_id: 'SYNTHETIC_PROJECT', model_id: model.id, model_version: model.version,
    scope_id: null,
    candidate_count: 1, max_candidates: 20, threshold: model.threshold,
    sfc_domain: { model_scope: 'all_column' },
    fields: keys.map((key) => ({ key, label: `SYNTHETIC ${key}`, unit: key === 'flow_rate_ml_min' ? 'mL/min' : null, values: values[key] })),
  }
}

export function setFixtureMode(next: FixtureMode) { mode = next; pendingModelA = [] }

export function releaseSlowModelA() {
  const waiting = pendingModelA
  pendingModelA = []
  waiting.forEach((resolve) => resolve(envelope(optionFixture('SYNTHETIC_MODEL_A'))))
}

export async function readLocalProbeCount() {
  const response = await nativeFetch('/__fixture/probe-count', { method: 'GET', cache: 'no-store' })
  if (!response.ok) throw new Error('Local loopback probe counter is unavailable')
  return await response.json() as { localImageProbeRequests: number }
}

export function installSyntheticFetch() {
  window.fetch = async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const rawUrl = input instanceof Request ? input.url : String(input)
    const url = new URL(rawUrl, window.location.href)
    const method = (init.method ?? (input instanceof Request ? input.method : 'GET')).toUpperCase()
    if (url.origin !== window.location.origin || !['127.0.0.1', 'localhost'].includes(url.hostname)) {
      throw new Error(`SYNTHETIC_FIXTURE_BLOCKED_EXTERNAL_REQUEST ${url.origin}`)
    }
    if (method !== 'GET' && method !== 'HEAD') throw new Error(`SYNTHETIC_FIXTURE_BLOCKED_${method} ${url.pathname}`)
    if (!url.pathname.startsWith('/api/v1/')) return nativeFetch(input, init)
    if (method !== 'GET') throw new Error(`SYNTHETIC_FIXTURE_BLOCKED_${method} ${url.pathname}`)

    if (url.pathname === '/api/v1/prediction/types') return envelope([{
      id: 'sfc', display_name: 'SYNTHETIC SFC', tools: syntheticTools().map((tool) => tool.id),
      health: healthReady,
    }])
    if (url.pathname === '/api/v1/prediction/types/sfc') return envelope({
      id: 'sfc', display_name: 'SYNTHETIC SFC', tools: syntheticTools(), models: syntheticModels(),
      limits: { max_candidates: 20, max_evaluations: 20, max_rows: 20 }, health: healthReady,
    })
    const schemaMatch = url.pathname.match(/^\/api\/v1\/prediction\/types\/sfc\/tools\/([^/]+)\/schema$/)
    if (schemaMatch) return envelope(toolSchema(decodeURIComponent(schemaMatch[1]!)))
    if (url.pathname === '/api/v1/sfc/conditions/options') {
      const modelId = url.searchParams.get('model_id')
      if (!modelId || !url.searchParams.get('model_version') || url.searchParams.get('project_id') !== 'SYNTHETIC_PROJECT') {
        throw new Error('SYNTHETIC_FIXTURE_REJECTED_UNBOUND_SFC_OPTIONS_GET')
      }
      if (modelId === 'SYNTHETIC_MODEL_A') return await new Promise<Response>((resolve) => pendingModelA.push(resolve))
      if (modelId === 'SYNTHETIC_MODEL_B') return envelope(optionFixture(modelId))
      throw new Error('SYNTHETIC_FIXTURE_UNKNOWN_MODEL')
    }
    throw new Error(`SYNTHETIC_FIXTURE_BLOCKED_UNLISTED_GET ${url.pathname}`)
  }
}
