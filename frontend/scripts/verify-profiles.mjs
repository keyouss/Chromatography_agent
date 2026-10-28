import { execFileSync } from 'node:child_process'
import { existsSync, readFileSync, readdirSync, rmSync, writeFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const frontendRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const buildsRoot = resolve(frontendRoot, '..', 'builds')
const contractsRoot = resolve(frontendRoot, '..', 'contracts')
const freezePath = resolve(frontendRoot, '..', 'docs', 'V1_M3_M4_CONTRACT_FREEZE.json')
const tempRoot = resolve(frontendRoot, '.profile-check')
const profiles = ['universal-v1', 'sfc-v1', 'cc-v1', 'minimal-structure-v1']
const incompleteModuleIds = new Set(['prediction', 'records', 'datasets', 'knowledge', 'training', 'model_center', 'assistant'])
const httpMethods = new Set(['get', 'put', 'post', 'delete', 'options', 'head', 'patch', 'trace'])
const freeze = JSON.parse(readFileSync(freezePath, 'utf8'))
const frozenByProfile = new Map(freeze.profiles.map((entry) => [entry.profile, entry]))
const sharedRoots = ['src/core', 'src/stores', 'src/components', 'src/shared']
const sharedForbidden = [/src[\\/]modules/, /@\/modules\//, /ModuleUnavailableView/]
const sortedRecord = (value = {}) => JSON.stringify(Object.fromEntries(Object.entries(value).sort(([left], [right]) => left.localeCompare(right))))
const sourceFiles = (root) => {
  const files = []
  if (!existsSync(root)) return files
  const visit = (directory) => {
    for (const entry of readdirSync(directory, { withFileTypes: true })) {
      const path = resolve(directory, entry.name)
      if (entry.isDirectory()) visit(path)
      else if (/\.(?:ts|tsx|js|jsx|vue)$/.test(entry.name)) files.push(path)
    }
  }
  visit(root)
  return files
}
try {
  execFileSync(process.execPath, [resolve(frontendRoot, 'scripts/verify-assembly-boundaries.mjs')], { stdio: 'inherit' })
  for (const profile of profiles) {
    const manifest = JSON.parse(readFileSync(resolve(buildsRoot, `${profile}.json`), 'utf8'))
    const frozen = frozenByProfile.get(profile)
    const contract = JSON.parse(readFileSync(resolve(contractsRoot, profile, 'openapi.json'), 'utf8'))
    const contractIndex = JSON.parse(readFileSync(resolve(contractsRoot, 'index.json'), 'utf8'))
    if (String(frozen?.normalized_openapi_sha256).toLowerCase() !== String(contractIndex.contracts?.[profile]?.sha256).toLowerCase()) throw new Error(`${profile} M3/M4 normalized SHA does not match contracts/index.json`)
    const pathCount = Object.keys(contract.paths ?? {}).length
    const operationCount = Object.values(contract.paths ?? {}).reduce((count, pathItem) => count + Object.keys(pathItem).filter((method) => httpMethods.has(method.toLowerCase())).length, 0)
    if (!frozen || frozen.paths !== pathCount || frozen.operations !== operationCount) throw new Error(`${profile} M3/M4 freeze count mismatch: ${pathCount} paths/${operationCount} operations`)
    for (const moduleId of manifest.modules) {
      if (!existsSync(resolve(frontendRoot, 'src/modules', moduleId, 'module.ts'))) throw new Error(`${profile} missing module entry: ${moduleId}`)
    }
    const output = resolve(tempRoot, profile)
    execFileSync(process.execPath, [resolve(frontendRoot, 'scripts/assemble-profile.mjs'), '--profile', profile, '--output', output], { stdio: 'inherit' })
    if (!existsSync(resolve(output, 'src/styles.css'))) throw new Error(`${profile} staged package is missing required src/styles.css`)
    for (const asset of ['index.html', 'iframe.html', 'ASSET_MANIFEST.json', 'licenses/LICENSE', 'licenses/NOTICE']) if (!existsSync(resolve(output, 'public/ketcher', asset))) throw new Error(`${profile} staged package is missing Ketcher asset ${asset}`)
    if (!existsSync(resolve(output, 'public/ketcher/static'))) throw new Error(`${profile} staged package is missing Ketcher static assets`)
    if (!existsSync(resolve(output, 'package-lock.json'))) throw new Error(`${profile} staged package is missing package-lock.json`)
    const stagedPackage = JSON.parse(readFileSync(resolve(output, 'package.json'), 'utf8'))
    const stagedLock = JSON.parse(readFileSync(resolve(output, 'package-lock.json'), 'utf8'))
    const stagedLockRoot = stagedLock.packages?.['']
    if (!stagedLockRoot) throw new Error(`${profile} staged package-lock.json is missing the root entry`)
    if (sortedRecord(stagedLockRoot.dependencies) !== sortedRecord(stagedPackage.dependencies)) throw new Error(`${profile} staged lock dependencies do not match package.json`)
    if (sortedRecord(stagedLockRoot.devDependencies) !== sortedRecord(stagedPackage.devDependencies)) throw new Error(`${profile} staged lock devDependencies do not match package.json`)
    for (const [name, version] of Object.entries({ ...stagedPackage.dependencies, ...stagedPackage.devDependencies })) {
      const entry = stagedLock.packages?.[`node_modules/${name}`]
      if (!entry || entry.version !== version) throw new Error(`${profile} staged lock top-level ${name} does not match ${version}`)
    }
    if (stagedLock.packages?.['']?.devDependencies?.['openapi-typescript'] || stagedLock.packages?.['node_modules/openapi-typescript']) throw new Error(`${profile} staged lock retains generation-only openapi-typescript`)
    if (Object.keys(stagedLock.packages ?? {}).some((key) => key.includes('/openapi-typescript'))) throw new Error(`${profile} staged lock retains nested openapi-typescript`)
    if (stagedPackage.devDependencies?.['openapi-typescript']) throw new Error(`${profile} staged package retains generation-only openapi-typescript`)
    for (const script of ['generate:registry', 'generate:contracts', 'verify:contracts', 'verify:profiles', 'verify:assembly-boundaries', 'assemble:profile']) if (stagedPackage.scripts?.[script]) throw new Error(`${profile} staged package retains unavailable script ${script}`)
    const stagedContracts = resolve(output, 'src/generated/contracts')
    if (!existsSync(resolve(stagedContracts, 'api-schema.ts')) || !existsSync(resolve(stagedContracts, profile, 'api-schema.ts'))) throw new Error(`${profile} staged package is missing selected frozen contract`)
    const contractEntries = readdirSync(stagedContracts, { withFileTypes: true }).filter((entry) => entry.isDirectory()).map((entry) => entry.name).sort()
    if (JSON.stringify(contractEntries) !== JSON.stringify([profile])) throw new Error(`${profile} staged package contains unselected contracts: ${contractEntries.join(',')}`)
    const generated = readFileSync(resolve(output, 'src/app/generated/registry.ts'), 'utf8')
    const stagedModules = readdirSync(resolve(output, 'src/modules'), { withFileTypes: true }).filter((entry) => entry.isDirectory()).map((entry) => entry.name).sort()
    if (JSON.stringify(stagedModules) !== JSON.stringify([...manifest.modules].sort())) throw new Error(`${profile} staged modules differ: ${stagedModules.join(',')} vs ${manifest.modules.join(',')}`)
    const expectedViews = new Set(['AppHomeView.vue'])
    const viewsByModule = { utilities: ['StructureView.vue'], settings: ['SettingsView.vue'] }
    for (const moduleId of manifest.modules) for (const view of viewsByModule[moduleId] || []) expectedViews.add(view)
    const stagedViews = readdirSync(resolve(output, 'src/views')).filter((entry) => entry.endsWith('.vue')).sort()
    if (JSON.stringify(stagedViews) !== JSON.stringify([...expectedViews].sort())) throw new Error(`${profile} staged views differ: ${stagedViews.join(',')} vs ${[...expectedViews].sort().join(',')}`)
    const stagedTests = readdirSync(resolve(output, 'tests')).filter((entry) => entry.endsWith('.test.ts')).sort()
    const expectedTests = ['confirmations.test.ts', 'jobs.test.ts', 'sse.test.ts']
    if (manifest.modules.includes('records')) expectedTests.push('api-client.test.ts')
    if (manifest.modules.includes('prediction') || manifest.chromatography_types.includes('tlc') && manifest.utilities.includes('tlc_reverse_solvent')) expectedTests.push('navigation.test.ts')
    if (manifest.modules.includes('prediction')) expectedTests.push('prediction-type-availability.test.ts', 'hplc-options.test.ts')
    if (manifest.modules.includes('prediction') && manifest.chromatography_types.includes('sfc')) expectedTests.push('sfc-pinned-prediction.test.ts')
    if (manifest.modules.includes('prediction') && manifest.modules.includes('assistant')) expectedTests.push('rev04-contract-migration.test.ts')
    if (manifest.modules.includes('utilities') && manifest.chromatography_types.includes('tlc') && (manifest.prediction_tools?.tlc?.includes('reverse_solvent') || manifest.utilities.includes('tlc_reverse_solvent'))) expectedTests.push('access-context-loading.test.ts')
    if (manifest.modules.includes('knowledge') && manifest.modules.includes('training') && manifest.modules.includes('assistant')) expectedTests.push('m3-m4-workflows.test.ts')
    if (manifest.chromatography_types.includes('tlc')) expectedTests.push('tlc-interaction.test.ts')
    if (manifest.chromatography_types.includes('tlc') && manifest.modules.includes('prediction')) expectedTests.push('tlc-reverse-page.test.ts')
    if (manifest.chromatography_types.includes('tlc') && manifest.modules.includes('prediction')) expectedTests.push('prediction-project-isolation.test.ts')
    if (manifest.modules.includes('records')) expectedTests.push('records-detail.test.ts', 'record-export.test.ts')
    if (manifest.modules.includes('assistant')) expectedTests.push('assistant-view.test.ts')
    if (manifest.modules.includes('settings')) expectedTests.push('settings-view.test.ts')
    expectedTests.sort()
    if (JSON.stringify(stagedTests) !== JSON.stringify(expectedTests)) throw new Error(`${profile} staged tests differ: ${stagedTests.join(',')} vs ${expectedTests.join(',')}`)
    if (!manifest.modules.includes('settings') && stagedViews.includes('SettingsView.vue')) throw new Error(`${profile} contains unselected SettingsView.vue`)
    if (!manifest.modules.includes('assistant') && stagedViews.includes('ModuleUnavailableView.vue')) throw new Error(`${profile} contains unselected ModuleUnavailableView.vue`)
    if (generated.includes('import.meta.glob') || /import\s*\(/.test(generated)) throw new Error(`${profile} generated registry uses runtime module discovery`)
    if (!manifest.chromatography_types.includes('tlc') && generated.includes('prediction/tlc-entry')) throw new Error(`${profile} contains TLC registry entry`)
    if (!manifest.chromatography_types.includes('tlc') && generated.includes('/prediction/tlc')) throw new Error(`${profile} contains TLC route`)
    if (!manifest.chromatography_types.includes('tlc') && existsSync(resolve(output, 'src/modules/prediction/tlc-entry.ts'))) throw new Error(`${profile} contains unselected TLC source entry`)
    const hasTlcReverse = manifest.chromatography_types.includes('tlc') && (manifest.prediction_tools?.tlc?.includes('reverse_solvent') || manifest.utilities.includes('tlc_reverse_solvent'))
    const hasUtilityTlcReverse = hasTlcReverse && manifest.modules.includes('utilities') && manifest.utilities.includes('tlc_reverse_solvent')
    if (existsSync(resolve(output, 'src/shared/tlc-reverse')) !== Boolean(hasTlcReverse)) throw new Error(`${profile} TLC reverse workspace crop does not match selected feature`)
    if (existsSync(resolve(output, 'src/modules/utilities/tlc-reverse-entry.ts')) !== Boolean(hasUtilityTlcReverse)) throw new Error(`${profile} TLC utility entry crop does not match selected feature`)
    if (hasUtilityTlcReverse !== generated.includes('/utilities/tlc-reverse')) throw new Error(`${profile} TLC utility route does not match selected feature`)
    if (!manifest.modules.includes('prediction') && existsSync(resolve(output, 'src/modules/prediction'))) throw new Error(`${profile} contains prediction module sources without selecting prediction`)
    const assembled = JSON.parse(readFileSync(resolve(output, 'ASSEMBLED_PROFILE.json'), 'utf8'))
    if (assembled.package_kind !== 'static-assembly' || assembled.release_ready !== false) throw new Error(`${profile} is incorrectly marked as a release-ready package`)
    const expectedIncomplete = manifest.modules.filter((moduleId) => incompleteModuleIds.has(moduleId)).sort()
    if (JSON.stringify(assembled.incomplete_modules?.slice().sort()) !== JSON.stringify(expectedIncomplete)) throw new Error(`${profile} incomplete module manifest is inaccurate`)
    for (const sharedRoot of sharedRoots) {
      for (const file of sourceFiles(resolve(output, sharedRoot))) {
        const content = readFileSync(file, 'utf8')
        if (sharedForbidden.some((pattern) => pattern.test(content))) throw new Error(`${profile} shared source retains disabled module reference: ${file}`)
      }
    }
  }
  const utilityOnlyRoot = resolve(tempRoot, 'tlc-reverse-utility-only')
  const utilityOnlyManifest = resolve(tempRoot, 'tlc-reverse-utility-only.json')
  const universalManifest = JSON.parse(readFileSync(resolve(buildsRoot, 'universal-v1.json'), 'utf8'))
  writeFileSync(utilityOnlyManifest, `${JSON.stringify({ ...universalManifest, build_id: 'universal-v1', modules: ['utilities'], utilities: ['structure_converter', 'tlc_reverse_solvent'], assistant_features: undefined }, null, 2)}\n`, 'utf8')
  execFileSync(process.execPath, [resolve(frontendRoot, 'scripts/assemble-profile.mjs'), '--profile', 'universal-v1', '--manifest', utilityOnlyManifest, '--contract-profile', 'universal-v1', '--output', utilityOnlyRoot], { stdio: 'inherit' })
  const utilityOnlyRegistry = readFileSync(resolve(utilityOnlyRoot, 'src/app/generated/registry.ts'), 'utf8')
  if (existsSync(resolve(utilityOnlyRoot, 'src/modules/prediction'))) throw new Error('TLC utility-only fixture contains prediction module sources')
  if (!existsSync(resolve(utilityOnlyRoot, 'src/shared/tlc-reverse/TlcReverseWorkspace.vue')) || !existsSync(resolve(utilityOnlyRoot, 'src/shared/prediction/store.ts'))) throw new Error('TLC utility-only fixture is missing shared reverse workspace/store')
  const utilityOnlyEntry = readFileSync(resolve(utilityOnlyRoot, 'src/modules/utilities/tlc-reverse-entry.ts'), 'utf8')
  if (!existsSync(resolve(utilityOnlyRoot, 'src/modules/utilities/tlc-reverse-entry.ts')) || !utilityOnlyEntry.includes("path: '/utilities/tlc-reverse'") || !utilityOnlyRegistry.includes("@/modules/utilities/tlc-reverse-entry")) throw new Error('TLC utility-only fixture is missing its utility route')
  if (utilityOnlyRegistry.includes("@/modules/prediction/module") || utilityOnlyRegistry.includes("path: '/prediction")) throw new Error('TLC utility-only fixture includes prediction navigation/routes')
  for (const form of ['CcSingleForm.vue', 'HplcSingleForm.vue', 'GcSingleForm.vue', 'SfcPredictionForm.vue']) if (existsSync(resolve(utilityOnlyRoot, 'src/modules/prediction/forms', form))) throw new Error(`TLC utility-only fixture contains ${form}`)
  execFileSync(process.execPath, [resolve(frontendRoot, 'node_modules/vue-tsc/bin/vue-tsc.js'), '--noEmit'], { cwd: utilityOnlyRoot, stdio: 'inherit' })
  console.log('profile static assembly checks passed; staged packages are not release-ready and ci/test/build are separate checks')
} finally {
  rmSync(tempRoot, { recursive: true, force: true })
}
