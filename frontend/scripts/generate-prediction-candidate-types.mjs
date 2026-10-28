import { createHash } from 'node:crypto'
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import openapiTS, { astToString } from 'openapi-typescript'

const frontendRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const repositoryRoot = resolve(frontendRoot, '..')
const candidateId = 'universal-v1-config-assistant-revision-20261007-12'
const inputPath = resolve(repositoryRoot, 'contracts/candidates', candidateId, 'universal-v1/openapi.json')
const outputPath = resolve(frontendRoot, 'src/shared/prediction/contracts/revision12.generated.ts')
const expectedHash = '7554504F12BDFFBE6AC0656A3A6625D1003DA8FA58DF498A7D8558F7246DA724'
const rootSchemas = [
  'ExactModelSelection',
  'ModelOption',
  'chrom_platform__modules__prediction__schemas__ModelSnapshot',
  'PredictionDraftExactModelSelection',
  'PredictionDraftSourceModelSnapshot',
  'PredictionDraftView',
]
const verify = process.argv.includes('--verify')

const packageJson = JSON.parse(readFileSync(resolve(frontendRoot, 'package.json'), 'utf8'))
if (packageJson.devDependencies?.['openapi-typescript'] !== '7.13.0') {
  throw new Error('Prediction candidate type generation requires the pinned openapi-typescript 7.13.0')
}

const source = readFileSync(inputPath)
const sourceHash = createHash('sha256').update(source).digest('hex').toUpperCase()
if (sourceHash !== expectedHash) throw new Error(`Candidate OAS SHA-256 mismatch: ${sourceHash}`)
const document = JSON.parse(source.toString('utf8'))
const schemaComponents = document.components?.schemas
if (!schemaComponents) throw new Error('Candidate OAS is missing component schemas')

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

const schemaNames = new Set()
const queue = rootSchemas.map((name) => `#/components/schemas/${name}`)
while (queue.length) {
  const reference = queue.shift()
  if (!reference.startsWith('#/components/schemas/')) {
    throw new Error(`Unexpected non-schema reference in prediction projection: ${reference}`)
  }
  const name = reference.slice('#/components/schemas/'.length)
  if (schemaNames.has(name)) continue
  const schema = document.components?.schemas?.[name]
  if (!schema) throw new Error(`Candidate OAS is missing referenced schema: ${name}`)
  schemaNames.add(name)
  queue.push(...refsOf(schema))
}

const projection = {
  openapi: document.openapi,
  info: document.info,
  components: {
    schemas: Object.fromEntries([...schemaNames].sort().map((name) => [name, document.components.schemas[name]])),
  },
}
const generated = astToString(await openapiTS(projection, { defaultNonNullable: false }))
const stringVariant = (schema) => schema?.anyOf?.find((variant) => variant.type === 'string')
const bounds = (schema, name) => {
  if (!Number.isInteger(schema?.minLength) || !Number.isInteger(schema?.maxLength)) throw new Error(`Candidate OAS is missing string limits for ${name}`)
  return { minLength: schema.minLength, maxLength: schema.maxLength }
}
const selectionSchema = schemaComponents.ExactModelSelection
const profileIdSchema = stringVariant(selectionSchema?.properties?.preprocessing_profile_id)
const profileHashSchema = stringVariant(selectionSchema?.properties?.preprocessing_profile_sha256)
const profileHashPattern = profileHashSchema?.pattern
if (profileHashPattern !== '^[0-9a-f]{64}$') throw new Error('Candidate OAS preprocessing profile SHA pattern changed')
for (const name of rootSchemas.filter((item) => item !== 'PredictionDraftView')) {
  const schema = schemaComponents[name]
  const idSchema = name === 'ModelOption' || name === 'PredictionDraftSourceModelSnapshot' || name === 'chrom_platform__modules__prediction__schemas__ModelSnapshot'
    ? schema.properties?.id
    : schema.properties?.model_id
  const versionSchema = name === 'ModelOption' || name === 'PredictionDraftSourceModelSnapshot' || name === 'chrom_platform__modules__prediction__schemas__ModelSnapshot'
    ? schema.properties?.version
    : schema.properties?.model_version
  if (!idSchema || !versionSchema) throw new Error(`Candidate OAS is missing identity fields for ${name}`)
}
const modelIdConstraints = bounds(selectionSchema?.properties?.model_id, 'ExactModelSelection.model_id')
const modelVersionConstraints = bounds(selectionSchema?.properties?.model_version, 'ExactModelSelection.model_version')
const profileIdConstraints = bounds(profileIdSchema, 'ExactModelSelection.preprocessing_profile_id')
const runtimeConstraints = `\n\nexport const revision12ModelSelectionConstraints = ${JSON.stringify({
  modelId: modelIdConstraints,
  modelVersion: modelVersionConstraints,
  preprocessingProfileId: profileIdConstraints,
  preprocessingProfileSha256Pattern: profileHashPattern,
}, null, 2)} as const\n`
const output = `/**
 * Candidate-local generated prediction selection types.
 * Candidate: ${candidateId} (PREVIEW_NOT_FROZEN).
 * Input SHA-256: ${sourceHash}.
 * Generated with openapi-typescript 7.13.0; never import from core aliases or formal contracts.
 */
${generated}${runtimeConstraints}`

if (verify) {
  const existing = readFileSync(outputPath, 'utf8')
  if (existing !== output) throw new Error('Prediction candidate type projection is stale')
  console.log(`Verified ${schemaNames.size} prediction schemas from ${candidateId}.`)
} else {
  mkdirSync(dirname(outputPath), { recursive: true })
  writeFileSync(outputPath, output, 'utf8')
  console.log(`Generated ${schemaNames.size} prediction schemas from ${candidateId}.`)
}
