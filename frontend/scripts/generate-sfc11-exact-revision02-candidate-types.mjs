import { createHash } from 'node:crypto'
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import openapiTS, { astToString } from 'openapi-typescript'

const frontendRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const repositoryRoot = resolve(frontendRoot, '..')
const candidateId = 'sfc-v11-exact-backend-revision-20261008-02'
const inputPath = resolve(repositoryRoot, 'contracts/candidates', candidateId, 'universal-v1/openapi.json')
const evidencePath = resolve(repositoryRoot, 'contracts/candidates', candidateId, 'typed-contract-check.json')
const outputPath = resolve(frontendRoot, 'src/shared/prediction/contracts/sfc-v11-exact-revision20261008-02.generated.ts')
const rootSchemas = [
  'ExactModelSelection', 'ModelOption', 'Prediction', 'PredictionAccepted',
  'PredictionInputSnapshot', 'SfcAllColumnDomain', 'SfcCandidateConditions',
  'SfcCandidateScopeCreate', 'SfcCandidateScopeView', 'SfcConditionsOptions',
  'SfcFullColumnPredictionCreate', 'SfcPredictionResult', 'SfcSingleColumnPredictionCreate',
  'SfcSingleColumnDomain', 'ToolCapability', 'TypeCapability',
]
const verify = process.argv.includes('--verify')

const packageJson = JSON.parse(readFileSync(resolve(frontendRoot, 'package.json'), 'utf8'))
if (packageJson.devDependencies?.['openapi-typescript'] !== '7.13.0') {
  throw new Error('SFC 1.1 candidate type generation requires the pinned openapi-typescript 7.13.0')
}

const source = readFileSync(inputPath)
const sourceHash = createHash('sha256').update(source).digest('hex').toUpperCase()
const evidence = JSON.parse(readFileSync(evidencePath, 'utf8'))
const expectedHash = evidence?.sha256?.['universal-v1\\openapi.json']
if (evidence?.candidate !== candidateId || evidence?.status !== 'PREVIEW_NOT_FROZEN'
  || typeof expectedHash !== 'string' || sourceHash !== expectedHash) {
  throw new Error(`SFC revision-02 candidate does not match its typed contract evidence (${sourceHash})`)
}

const document = JSON.parse(source.toString('utf8'))
const schemas = document.components?.schemas
if (!schemas) throw new Error('Candidate OAS is missing component schemas')

const selectorParameters = document.paths?.['/api/v1/sfc/conditions/options']?.get?.parameters ?? []
const selectorNames = selectorParameters.filter((item) => item.in === 'query').map((item) => item.name)
for (const name of ['scope_id', 'model_id', 'model_version', 'project_id']) {
  if (!selectorNames.includes(name)) throw new Error(`SFC options OpenAPI is missing query parameter: ${name}`)
}
for (const name of ['SfcSingleColumnPredictionCreate', 'SfcFullColumnPredictionCreate']) {
  const schema = schemas[name]
  if (schema?.properties?.schema_version?.const !== '1.1'
    || schema.properties?.model_selection?.$ref !== '#/components/schemas/ExactModelSelection'
    || schema.properties?.structure_refs?.minItems !== 2
    || schema.properties?.structure_refs?.maxItems !== 2) {
    throw new Error(`${name} does not match the exact SFC 1.1 request boundary`)
  }
}
if (schemas.SfcCandidateScopeCreate?.properties?.model_selection?.$ref !== '#/components/schemas/ExactModelSelection') {
  throw new Error('SFC candidate scope must bind an ExactModelSelection')
}
const optionDomain = schemas.SfcConditionsOptions?.properties?.sfc_domain
if (optionDomain?.discriminator?.propertyName !== 'model_scope'
  || !Array.isArray(optionDomain.oneOf)
  || optionDomain.oneOf.length !== 2
  || !optionDomain.oneOf.some((item) => item.$ref === '#/components/schemas/SfcAllColumnDomain')
  || !optionDomain.oneOf.some((item) => item.$ref === '#/components/schemas/SfcSingleColumnDomain')) {
  throw new Error('SFC revision-02 options domain is missing its model_scope discriminated union')
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
 * Candidate-local generated SFC 1.1 exact-selection types.
 * Candidate: ${candidateId} (PREVIEW_NOT_FROZEN).
 * Input SHA-256: ${sourceHash} (matched against typed-contract-check.json).
 * Generated with openapi-typescript 7.13.0; do not import from formal core aliases.
 */
${generated}`

if (verify) {
  const existing = readFileSync(outputPath, 'utf8')
  if (existing !== output) throw new Error('SFC revision-02 candidate type projection is stale')
  console.log(`Verified ${names.size} schemas from ${candidateId}.`)
} else {
  mkdirSync(dirname(outputPath), { recursive: true })
  writeFileSync(outputPath, output, 'utf8')
  console.log(`Generated ${names.size} schemas from ${candidateId}.`)
}
