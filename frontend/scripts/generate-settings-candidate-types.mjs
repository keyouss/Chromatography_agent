import { createHash } from 'node:crypto'
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import openapiTS, { astToString } from 'openapi-typescript'

const frontendRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const repositoryRoot = resolve(frontendRoot, '..')
const candidateId = 'universal-v1-config-assistant-revision-20261006-09'
const inputPath = resolve(repositoryRoot, 'contracts/candidates', candidateId, 'universal-v1/openapi.json')
const outputPath = resolve(frontendRoot, 'src/modules/settings/contracts/revision09.generated.ts')
const expectedHash = '1E7D7169E342247315657A51F2137012CC7411B1E0127D82A39450A2A2147F31'
const selectedPaths = [
  '/api/v1/settings/public',
  '/api/v1/settings/llm',
  '/api/v1/settings/llm/configurations',
  '/api/v1/settings/llm/configurations/{configuration_id}/test',
  '/api/v1/settings/llm/tests/{test_id}',
  '/api/v1/settings/llm/configurations/{configuration_id}/activate',
]

const packageJson = JSON.parse(readFileSync(resolve(frontendRoot, 'package.json'), 'utf8'))
if (packageJson.devDependencies?.['openapi-typescript'] !== '7.13.0') {
  throw new Error('Settings candidate type generation requires the pinned openapi-typescript 7.13.0')
}

const source = readFileSync(inputPath)
const sourceHash = createHash('sha256').update(source).digest('hex').toUpperCase()
if (sourceHash !== expectedHash) throw new Error(`Candidate OAS SHA-256 mismatch: ${sourceHash}`)
const document = JSON.parse(source.toString('utf8'))
for (const path of selectedPaths) {
  if (!document.paths?.[path]) throw new Error(`Candidate OAS is missing Settings path: ${path}`)
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

const queue = selectedPaths.flatMap((path) => refsOf(document.paths[path]))
const schemaNames = new Set()
while (queue.length) {
  const reference = queue.shift()
  if (!reference.startsWith('#/components/schemas/')) {
    throw new Error(`Unexpected non-schema reference in Settings projection: ${reference}`)
  }
  const name = reference.slice('#/components/schemas/'.length)
  if (schemaNames.has(name)) continue
  const schema = document.components?.schemas?.[name]
  if (!schema) throw new Error(`Candidate OAS is missing referenced schema: ${name}`)
  schemaNames.add(name)
  queue.push(...refsOf(schema))
}

const projection = {
  ...document,
  paths: Object.fromEntries(selectedPaths.map((path) => [path, document.paths[path]])),
  components: {
    schemas: Object.fromEntries([...schemaNames].sort().map((name) => [name, document.components.schemas[name]])),
  },
}
const generated = astToString(await openapiTS(projection, { defaultNonNullable: false }))
const header = `/**
 * Candidate-local generated types for the universal-v1 Settings API projection.
 * Candidate: ${candidateId} (PREVIEW_NOT_FROZEN).
 * Input SHA-256: ${sourceHash}.
 * Generated with openapi-typescript 7.13.0; never import from core aliases or formal contracts.
 */
`

mkdirSync(dirname(outputPath), { recursive: true })
writeFileSync(outputPath, header + generated, 'utf8')
console.log(`Generated ${selectedPaths.length} Settings paths and ${schemaNames.size} recursively referenced schemas.`)
