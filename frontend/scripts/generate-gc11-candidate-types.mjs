import { createHash } from 'node:crypto'
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import openapiTS, { astToString } from 'openapi-typescript'

const frontendRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const repositoryRoot = resolve(frontendRoot, '..')
const candidateId = 'universal-v1-gc11-revision-20261007-18'
const inputPath = resolve(repositoryRoot, 'contracts/candidates', candidateId, 'universal-v1/openapi.json')
const outputPath = resolve(frontendRoot, 'src/shared/prediction/contracts/gc11-revision18.generated.ts')
const expectedHash = '268923554A761C9C131DD916982094188B5126FACAFD941132838466E8111A15'
const rootSchemas = [
  'GcExactProfileModelSelection', 'GcInput', 'GcLinearTemperatureProgram', 'GcPredictionCreate',
  'GcPredictionResult', 'GcResultRow', 'GcSeriesTemperatureProgram', 'ModelOption',
  'Prediction', 'PredictionAccepted', 'PredictionDraftView', 'PredictionDraftExactModelSelection',
  'PredictionDraftSourceModelSnapshot', 'PredictionInputSnapshot', 'TypeCapability', 'ToolCapability',
  'chrom_platform__modules__prediction__schemas__ModelSnapshot',
]
const verify = process.argv.includes('--verify')

const packageJson = JSON.parse(readFileSync(resolve(frontendRoot, 'package.json'), 'utf8'))
if (packageJson.devDependencies?.['openapi-typescript'] !== '7.13.0') {
  throw new Error('GC 1.1 candidate type generation requires the pinned openapi-typescript 7.13.0')
}
const source = readFileSync(inputPath)
const sourceHash = createHash('sha256').update(source).digest('hex').toUpperCase()
if (sourceHash !== expectedHash) throw new Error(`GC 1.1 candidate OAS SHA-256 mismatch: ${sourceHash}`)
const document = JSON.parse(source.toString('utf8'))
const schemas = document.components?.schemas
if (!schemas) throw new Error('Candidate OAS is missing component schemas')

const post = document.paths?.['/api/v1/predictions']?.post
const postSchema = post?.requestBody?.content?.['application/json']?.schema
const gcCreate = schemas.GcPredictionCreate
const selection = schemas.GcExactProfileModelSelection
const refs = gcCreate?.properties?.structure_refs
const linear = schemas.GcLinearTemperatureProgram
const series = schemas.GcSeriesTemperatureProgram
const requiredSelection = ['mode', 'model_id', 'model_version', 'preprocessing_profile_id', 'preprocessing_profile_sha256']
if (!postSchema?.oneOf?.some((item) => item.$ref === '#/components/schemas/GcPredictionCreate')
  || gcCreate?.properties?.schema_version?.const !== '1.1'
  || gcCreate?.properties?.model_selection?.$ref !== '#/components/schemas/GcExactProfileModelSelection'
  || refs?.minItems !== 1 || refs?.maxItems !== 1
  || !Array.isArray(gcCreate.required) || !gcCreate.required.includes('model_selection')
  || !Array.isArray(gcCreate.required) || !gcCreate.required.includes('structure_refs')
  || !Array.isArray(selection?.required) || requiredSelection.some((key) => !selection.required.includes(key))
  || selection.properties?.preprocessing_profile_sha256?.pattern !== '^[0-9a-f]{64}$'
  || linear?.properties?.initial_hold_min?.minimum !== 0
  || !Number.isFinite(linear?.properties?.heating_rate_c_per_min?.exclusiveMinimum)
  || series?.properties?.temperature_series_c?.minItems !== 1
  || series?.properties?.temperature_series_c?.maxItems !== 10000) {
  throw new Error('Pinned GC 1.1 DTO boundary changed; review the projection before regenerating')
}
const inputRefs = schemas.PredictionInputSnapshot?.properties?.input?.anyOf?.map((item) => item.$ref) ?? []
if (!inputRefs.includes('#/components/schemas/GcInput')) {
  throw new Error('PredictionInputSnapshot must preserve the generated GC input snapshot schema')
}

function refsOf(value) {
  const refs = []
  const scan = (item) => {
    if (!item || typeof item !== 'object') return
    if (typeof item.$ref === 'string') refs.push(item.$ref)
    for (const child of Object.values(item)) scan(child)
  }
  scan(value)
  return refs
}

const names = new Set()
const queue = rootSchemas.map((name) => `#/components/schemas/${name}`)
while (queue.length) {
  const reference = queue.shift()
  if (!reference.startsWith('#/components/schemas/')) throw new Error(`Unexpected non-schema ref: ${reference}`)
  const name = reference.slice('#/components/schemas/'.length)
  if (names.has(name)) continue
  if (!schemas[name]) throw new Error(`Candidate OAS is missing referenced schema: ${name}`)
  names.add(name)
  queue.push(...refsOf(schemas[name]))
}

const projection = { openapi: document.openapi, info: document.info, components: { schemas: Object.fromEntries([...names].sort().map((name) => [name, schemas[name]])) } }
const generated = astToString(await openapiTS(projection, { defaultNonNullable: false }))
const constraints = {
  structureRefs: { minItems: refs.minItems, maxItems: refs.maxItems, pattern: refs.items.pattern },
  modelId: { minLength: selection.properties.model_id.minLength, maxLength: selection.properties.model_id.maxLength },
  modelVersion: { minLength: selection.properties.model_version.minLength, maxLength: selection.properties.model_version.maxLength },
  profileId: { minLength: selection.properties.preprocessing_profile_id.minLength, maxLength: selection.properties.preprocessing_profile_id.maxLength },
  profileSha256Pattern: selection.properties.preprocessing_profile_sha256.pattern,
  linearHoldMinimum: linear.properties.initial_hold_min.minimum,
  seriesItems: { minItems: series.properties.temperature_series_c.minItems, maxItems: series.properties.temperature_series_c.maxItems },
}
const output = `/**
 * Candidate-local generated GC 1.1 types.
 * Candidate: ${candidateId} (PREVIEW_NOT_FROZEN).
 * Input SHA-256: ${sourceHash}.
 * Generated with openapi-typescript 7.13.0; never imported by formal core contract aliases.
 */
${generated}\n\nexport const gc11CandidateConstraints = ${JSON.stringify(constraints, null, 2)} as const\n`

if (verify) {
  if (readFileSync(outputPath, 'utf8') !== output) throw new Error('GC 1.1 candidate type projection is stale')
  console.log(`Verified ${names.size} GC candidate schemas from ${candidateId}.`)
} else {
  mkdirSync(dirname(outputPath), { recursive: true })
  writeFileSync(outputPath, output, 'utf8')
  console.log(`Generated ${names.size} GC candidate schemas from ${candidateId}.`)
}
