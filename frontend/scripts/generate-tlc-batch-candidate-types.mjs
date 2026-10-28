import { mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import openapiTS, { astToString } from 'openapi-typescript'

const frontendRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const repositoryRoot = resolve(frontendRoot, '..')
const candidateId = 'universal-v1-tlc-batch-revision-20261008-03'
const inputPath = resolve(repositoryRoot, 'contracts/candidates', candidateId, 'universal-v1/openapi.json')
const outputPath = resolve(frontendRoot, 'candidates/tlc-batch-revision20261008-01/generated/tlc-batch.generated.ts')
const rootSchemas = [
  'BatchAccepted', 'BatchCancel', 'BatchCounts', 'BatchDraft', 'BatchDraftPatch', 'BatchResourceBudget',
  'BatchRowResult', 'BatchRowsPage', 'BatchSubmit', 'BatchValidation', 'BatchValidationAccepted',
  'BatchValidationRowsPage', 'ModelOption', 'RowValidation', 'TlcBatchCommonInput', 'TlcBatchDraftCreate', 'TlcPredictionResult',
  'BatchAwareTypeCapability', 'TlcBatchToolCapability', 'NonBatchToolCapability',
]
const verify = process.argv.includes('--verify')

const packageJson = JSON.parse(readFileSync(resolve(frontendRoot, 'package.json'), 'utf8'))
if (packageJson.devDependencies?.['openapi-typescript'] !== '7.13.0') {
  throw new Error('TLC batch candidate type generation requires the pinned openapi-typescript 7.13.0')
}

const document = JSON.parse(readFileSync(inputPath, 'utf8'))
const schemas = document.components?.schemas
if (!schemas?.TlcBatchDraftCreate || !schemas.BatchDraft || !schemas.BatchValidation || !schemas.TlcBatchToolCapability) {
  throw new Error(`Candidate OAS is missing TLC batch lifecycle schemas: ${candidateId}`)
}
if (document.paths?.['/api/v1/batch-predictions/drafts']?.post?.operationId !== 'create_batch_prediction_draft'
  || document.paths?.['/api/v1/batch-predictions/{batch_id}/submit']?.post?.operationId !== 'submit_batch_prediction') {
  throw new Error(`Candidate OAS does not contain the expected batch lifecycle: ${candidateId}`)
}
if (schemas.TlcBatchToolCapability.properties?.id?.const !== 'batch'
  || schemas.TlcBatchToolCapability.properties?.chromatography_type?.const !== 'tlc') {
  throw new Error(`Candidate OAS is missing the TLC batch capability discriminants: ${candidateId}`)
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

const projection = {
  openapi: document.openapi,
  info: document.info,
  components: { schemas: Object.fromEntries([...names].sort().map((name) => [name, schemas[name]])) },
}
const generated = astToString(await openapiTS(projection, { defaultNonNullable: false }))
const output = `/**
 * Candidate-local TLC batch types; not imported by the product runtime.
 * Status: PREVIEW_NOT_FROZEN. Source candidate: ${candidateId}.
 * Regenerate and review the projection when BE publishes the next candidate OAS.
 */
${generated}`

if (verify) {
  if (readFileSync(outputPath, 'utf8') !== output) throw new Error('TLC batch candidate type projection is stale')
  console.log(`Verified ${names.size} schemas from ${candidateId} (PREVIEW_NOT_FROZEN).`)
} else {
  mkdirSync(dirname(outputPath), { recursive: true })
  writeFileSync(outputPath, output, 'utf8')
  console.log(`Generated ${names.size} schemas from ${candidateId} (PREVIEW_NOT_FROZEN).`)
}
