import { createHash } from 'node:crypto'
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import openapiTS, { astToString } from 'openapi-typescript'

const frontendRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const repositoryRoot = resolve(frontendRoot, '..')
const candidateId = 'universal-v1-hplc11-revision-20261007-15'
const inputPath = resolve(repositoryRoot, 'contracts/candidates', candidateId, 'universal-v1/openapi.json')
const outputPath = resolve(frontendRoot, 'src/shared/prediction/contracts/hplc11-revision15.generated.ts')
const expectedHash = '4D9558DBD339A168D3A8F2FC06AAF72F41CE0202FD3C2062F2592BAD97A0E475'
const rootSchemas = [
  'ExactModelSelection',
  'HplcInput',
  'HplcInputV10',
  'HplcPredictionCreate',
  'ModelOption',
  'Prediction',
  'PredictionAccepted',
  'PredictionDraftView',
  'PredictionDraftExactModelSelection',
  'PredictionDraftSourceModelSnapshot',
  'PredictionInputSnapshot',
  'chrom_platform__modules__prediction__schemas__ModelSnapshot',
]
const verify = process.argv.includes('--verify')

const packageJson = JSON.parse(readFileSync(resolve(frontendRoot, 'package.json'), 'utf8'))
if (packageJson.devDependencies?.['openapi-typescript'] !== '7.13.0') {
  throw new Error('HPLC 1.1 candidate type generation requires the pinned openapi-typescript 7.13.0')
}

const source = readFileSync(inputPath)
const sourceHash = createHash('sha256').update(source).digest('hex').toUpperCase()
if (sourceHash !== expectedHash) throw new Error(`HPLC 1.1 candidate OAS SHA-256 mismatch: ${sourceHash}`)
const document = JSON.parse(source.toString('utf8'))
const schemaComponents = document.components?.schemas
if (!schemaComponents) throw new Error('Candidate OAS is missing component schemas')

const postSchema = document.paths?.['/api/v1/predictions']?.post?.requestBody?.content?.['application/json']?.schema
if (!postSchema?.oneOf?.some((item) => item.$ref === '#/components/schemas/HplcPredictionCreate')) {
  throw new Error('Candidate POST /predictions no longer selects HplcPredictionCreate')
}
const hplcCreate = schemaComponents.HplcPredictionCreate
const hplcInput = schemaComponents.HplcInput
const hplcInputV10 = schemaComponents.HplcInputV10
const refs = hplcCreate?.properties?.structure_refs
const rawSmiles = hplcInput?.properties?.model_input_smiles
const rawItems = rawSmiles?.items
if (hplcCreate?.properties?.schema_version?.const !== '1.1'
  || refs?.minItems !== 2 || refs?.maxItems !== 2
  || hplcCreate?.properties?.model_selection?.$ref !== '#/components/schemas/ExactModelSelection'
  || rawSmiles?.minItems !== 2 || rawSmiles?.maxItems !== 2
  || rawItems?.minLength !== 1 || rawItems?.maxLength !== 20000
  || !Array.isArray(hplcCreate.required) || !hplcCreate.required.includes('model_selection')
  || !Array.isArray(hplcInput.required) || !hplcInput.required.includes('model_input_smiles')
  || Object.hasOwn(hplcInputV10?.properties ?? {}, 'model_input_smiles')) {
  throw new Error('Pinned HPLC 1.1 DTO boundary changed; review the projection before regenerating')
}
const snapshotInputRefs = schemaComponents.PredictionInputSnapshot?.properties?.input?.anyOf?.map((item) => item.$ref) ?? []
if (!snapshotInputRefs.includes('#/components/schemas/HplcInput') || !snapshotInputRefs.includes('#/components/schemas/HplcInputV10')) {
  throw new Error('PredictionInputSnapshot must preserve both HPLC 1.1 and historical HPLC 1.0 inputs')
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

const schemaNames = new Set()
const queue = rootSchemas.map((name) => `#/components/schemas/${name}`)
while (queue.length) {
  const reference = queue.shift()
  if (!reference.startsWith('#/components/schemas/')) throw new Error(`Unexpected non-schema ref: ${reference}`)
  const name = reference.slice('#/components/schemas/'.length)
  if (schemaNames.has(name)) continue
  const schema = schemaComponents[name]
  if (!schema) throw new Error(`Candidate OAS is missing referenced schema: ${name}`)
  schemaNames.add(name)
  queue.push(...refsOf(schema))
}

const projection = {
  openapi: document.openapi,
  info: document.info,
  components: { schemas: Object.fromEntries([...schemaNames].sort().map((name) => [name, schemaComponents[name]])) },
}
const generated = astToString(await openapiTS(projection, { defaultNonNullable: false }))
const constraints = {
  rawSmiles: { minItems: rawSmiles.minItems, maxItems: rawSmiles.maxItems, minLength: rawItems.minLength, maxLength: rawItems.maxLength },
  structureRefs: { minItems: refs.minItems, maxItems: refs.maxItems, pattern: refs.items.pattern },
  preprocessingProfileSha256Pattern: schemaComponents.ExactModelSelection.properties.preprocessing_profile_sha256.anyOf.find((item) => item.type === 'string')?.pattern,
}
if (constraints.preprocessingProfileSha256Pattern !== '^[0-9a-f]{64}$') throw new Error('HPLC exact model selection profile hash pattern changed')
const output = `/**
 * Candidate-local generated HPLC 1.1 types.
 * Candidate: ${candidateId} (PREVIEW_NOT_FROZEN).
 * Input SHA-256: ${sourceHash}.
 * Generated with openapi-typescript 7.13.0; never imported by formal core contract aliases.
 */
${generated}\n\nexport const hplc11CandidateConstraints = ${JSON.stringify(constraints, null, 2)} as const\n`

if (verify) {
  if (readFileSync(outputPath, 'utf8') !== output) throw new Error('HPLC 1.1 candidate type projection is stale')
  console.log(`Verified ${schemaNames.size} HPLC candidate schemas from ${candidateId}.`)
} else {
  mkdirSync(dirname(outputPath), { recursive: true })
  writeFileSync(outputPath, output, 'utf8')
  console.log(`Generated ${schemaNames.size} HPLC candidate schemas from ${candidateId}.`)
}
