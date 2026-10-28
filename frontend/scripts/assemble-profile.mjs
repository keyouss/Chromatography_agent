import { cpSync, existsSync, lstatSync, mkdirSync, readFileSync, realpathSync, rmSync, writeFileSync } from 'node:fs'
import { dirname, isAbsolute, relative, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { execFileSync } from 'node:child_process'
import { createStagedLock } from './create-staged-lock.mjs'

const scriptDir = dirname(fileURLToPath(import.meta.url))
const frontendRoot = resolve(scriptDir, '..')
const buildsRoot = resolve(frontendRoot, '..', 'builds')
const args = process.argv.slice(2)
const argument = (name, fallback) => { const index = args.indexOf(name); return index >= 0 && args[index + 1] ? args[index + 1] : fallback }
const profile = argument('--profile', process.env.VITE_BUILD_PROFILE || 'universal-v1')
const outputArg = argument('--output', `build-output/${profile}`)
const output = resolve(frontendRoot, outputArg)
const relativeOutput = relative(frontendRoot, output)
const generationRoots = ['build-output', '.profile-check'].map((root) => resolve(frontendRoot, root))
const normalized = (value) => resolve(value).toLowerCase()
const isDescendant = (root, target) => {
  const child = relative(root, target)
  return Boolean(child) && !child.startsWith('..') && !isAbsolute(child)
}
const assertNoSymlinkEscape = (target) => {
  const parts = relative(frontendRoot, target).split(/[\\/]/).filter(Boolean)
  let current = frontendRoot
  const rootReal = realpathSync.native(frontendRoot)
  for (const part of parts) {
    current = resolve(current, part)
    if (!existsSync(current)) break
    const stat = lstatSync(current)
    if (stat.isSymbolicLink()) throw new Error(`Assembly output cannot traverse symlink/junction: ${current}`)
    const real = realpathSync.native(current)
    if (normalized(real) !== normalized(current) && normalized(real) !== normalized(rootReal)) {
      throw new Error(`Assembly output cannot traverse symlink/junction: ${current}`)
    }
  }
}
const allowedRoot = generationRoots.find((root) => isDescendant(root, output))
if (!relativeOutput || !allowedRoot) throw new Error('Assembly output must be a new descendant of frontend/build-output or frontend/.profile-check')
assertNoSymlinkEscape(output)
const manifestPath = resolve(argument('--manifest', resolve(buildsRoot, `${profile}.json`)))
const manifest = JSON.parse(readFileSync(manifestPath, 'utf8'))
const contractProfile = argument('--contract-profile', profile)
const hasTlcReverse = manifest.chromatography_types.includes('tlc') && (manifest.prediction_tools?.tlc?.includes('reverse_solvent') || manifest.utilities.includes('tlc_reverse_solvent'))
const batchTypes = ['tlc', 'cc', 'hplc', 'gc', 'sfc'].filter((type) => manifest.modules.includes('prediction') && manifest.chromatography_types.includes(type) && manifest.prediction_tools?.[type]?.includes('batch'))
const hasBatchPrediction = batchTypes.length > 0
const incompleteModuleIds = ['prediction', 'records', 'datasets', 'knowledge', 'training', 'model_center', 'assistant']
const incompleteModules = manifest.modules.filter((moduleId) => incompleteModuleIds.includes(moduleId))
const releaseReadyRequested = args.includes('--release-ready')
if (releaseReadyRequested && incompleteModules.length) {
  throw new Error(`Profile ${profile} contains incomplete modules and cannot be marked release-ready: ${incompleteModules.join(', ')}`)
}

rmSync(output, { recursive: true, force: true })
mkdirSync(output, { recursive: true })
const copy = (source, target = source) => { const sourcePath = resolve(frontendRoot, source); if (!existsSync(sourcePath)) throw new Error(`Missing assembly source: ${source}`); const destination = resolve(output, target); mkdirSync(dirname(destination), { recursive: true }); cpSync(sourcePath, destination, { recursive: true }) }
for (const common of ['index.html', 'package.json', 'tsconfig.json', 'vite.config.ts', 'vitest.config.ts', 'src/App.vue', 'src/main.ts', 'src/styles.css', 'src/env.d.ts', 'src/app', 'src/components', 'src/core', 'src/router', 'src/stores', 'src/views/AppHomeView.vue', 'tests', 'public/ketcher']) copy(common)
copy('src/generated/README.md', 'src/generated/README.md')
// Repository acceptance harnesses reference historical candidates or testing reports;
// they are retained in the repository, not included in independently compiled source packages.
for (const test of ['gc11-page.test.ts', 'qa-signed-cursor-20261019.test.ts', 'tlc-batch-candidate-client.test.ts', 'tlc-batch-candidate-entry.test.ts', 'tlc-batch-candidate-workspace.test.ts']) {
  rmSync(resolve(output, 'tests', test), { force: true })
}
for (const moduleId of manifest.modules) copy(`src/modules/${moduleId}`, `src/modules/${moduleId}`)
if (!(manifest.modules.includes('knowledge') && manifest.modules.includes('training') && manifest.modules.includes('assistant'))) rmSync(resolve(output, 'tests/m3-m4-workflows.test.ts'), { force: true })
if (!manifest.modules.includes('records')) {
  rmSync(resolve(output, 'tests/api-client.test.ts'), { force: true })
  rmSync(resolve(output, 'tests/records-detail.test.ts'), { force: true })
}
if (!manifest.modules.includes('prediction') && !hasTlcReverse) rmSync(resolve(output, 'tests/navigation.test.ts'), { force: true })
if (!hasBatchPrediction) {
  for (const test of ['batch-client.test.ts', 'batch-workspace.test.ts', 'batch-workspace-component.test.ts', 'batch-structure-preparation.test.ts', 'batch-page.test.ts', 'batch-entry.test.ts']) {
    rmSync(resolve(output, 'tests', test), { force: true })
  }
}
if (!manifest.modules.includes('prediction')) {
  rmSync(resolve(output, 'tests/prediction-type-availability.test.ts'), { force: true })
  rmSync(resolve(output, 'tests/hplc-options.test.ts'), { force: true })
}
if (!(manifest.modules.includes('prediction') && manifest.chromatography_types.includes('sfc'))) {
  for (const test of ['sfc-pinned-prediction.test.ts', 'sfc-search-form.test.ts', 'sfc-search-integration.test.ts']) rmSync(resolve(output, 'tests', test), { force: true })
}
if (!(manifest.modules.includes('prediction') && manifest.chromatography_types.includes('tlc'))) rmSync(resolve(output, 'tests/prediction-project-isolation.test.ts'), { force: true })
if (!(manifest.modules.includes('prediction') && manifest.modules.includes('assistant'))) rmSync(resolve(output, 'tests/rev04-contract-migration.test.ts'), { force: true })
if (!(manifest.modules.includes('utilities') && hasTlcReverse)) rmSync(resolve(output, 'tests/access-context-loading.test.ts'), { force: true })
if (!manifest.chromatography_types.includes('tlc')) {
  rmSync(resolve(output, 'tests/tlc-reverse-page.test.ts'), { force: true })
  rmSync(resolve(output, 'tests/tlc-result.test.ts'), { force: true })
  rmSync(resolve(output, 'tests/tlc-interaction.test.ts'), { force: true })
} else {
  if (!manifest.modules.includes('prediction')) rmSync(resolve(output, 'tests/tlc-reverse-page.test.ts'), { force: true })
}
if (!manifest.modules.includes('records')) {
  rmSync(resolve(output, 'tests/records-detail.test.ts'), { force: true })
  rmSync(resolve(output, 'tests/record-export.test.ts'), { force: true })
}
if (!manifest.modules.includes('assistant')) rmSync(resolve(output, 'tests/assistant-view.test.ts'), { force: true })
if (!manifest.modules.includes('settings')) rmSync(resolve(output, 'tests/settings-view.test.ts'), { force: true })
const viewsByModule = { utilities: ['StructureView.vue'], settings: ['SettingsView.vue'] }
for (const moduleId of manifest.modules) for (const view of viewsByModule[moduleId] || []) copy(`src/views/${view}`, `src/views/${view}`)
if (manifest.chromatography_types.includes('tlc') && manifest.modules.includes('prediction')) copy('src/modules/prediction/tlc-entry.ts', 'src/modules/prediction/tlc-entry.ts')
else rmSync(resolve(output, 'src/modules/prediction/tlc-entry.ts'), { force: true })
if (hasBatchPrediction) {
  copy('src/modules/prediction/batch-entry.ts', 'src/modules/prediction/batch-entry.ts')
  copy('src/modules/prediction/BatchPredictionPage.vue', 'src/modules/prediction/BatchPredictionPage.vue')
  copy('src/shared/batch', 'src/shared/batch')
} else {
  rmSync(resolve(output, 'src/modules/prediction/batch-entry.ts'), { force: true })
  rmSync(resolve(output, 'src/modules/prediction/BatchPredictionPage.vue'), { force: true })
  rmSync(resolve(output, 'src/shared/batch'), { recursive: true, force: true })
}
if (hasTlcReverse && manifest.modules.includes('prediction') && manifest.prediction_tools?.tlc?.includes('reverse_solvent')) copy('src/modules/prediction/tlc-reverse-entry.ts', 'src/modules/prediction/tlc-reverse-entry.ts')
else rmSync(resolve(output, 'src/modules/prediction/tlc-reverse-entry.ts'), { force: true })
if (hasTlcReverse && manifest.modules.includes('utilities')) copy('src/modules/utilities/tlc-reverse-entry.ts', 'src/modules/utilities/tlc-reverse-entry.ts')
else rmSync(resolve(output, 'src/modules/utilities/tlc-reverse-entry.ts'), { force: true })
if (manifest.modules.includes('prediction') || hasTlcReverse) copy('src/shared/prediction', 'src/shared/prediction')
else rmSync(resolve(output, 'src/shared/prediction'), { recursive: true, force: true })
if (manifest.modules.includes('prediction') || hasTlcReverse) copy('src/shared/tlc', 'src/shared/tlc')
else rmSync(resolve(output, 'src/shared/tlc'), { recursive: true, force: true })
if (hasTlcReverse) copy('src/shared/tlc-reverse', 'src/shared/tlc-reverse')
else rmSync(resolve(output, 'src/shared/tlc-reverse'), { recursive: true, force: true })
rmSync(resolve(output, 'src/core/build/profiles.ts'), { force: true })

execFileSync(process.execPath, [resolve(frontendRoot, 'scripts/generate-contract-types.mjs'), '--profile', contractProfile, '--output-root', resolve(output, 'src/generated/contracts')], { stdio: 'inherit' })

const generatedRegistry = resolve(output, 'src/app/generated/registry.ts')
const generatedManifest = resolve(output, 'src/app/generated/build-manifest.json')
const generatedProfile = resolve(output, 'src/app/generated/profile.ts')
execFileSync(process.execPath, [resolve(frontendRoot, 'scripts/generate-registry.mjs'), '--profile', profile, '--manifest', manifestPath, '--output', generatedRegistry, '--manifest-output', generatedManifest, '--profile-output', generatedProfile, '--verify'], { stdio: 'inherit' })
const stagedPackage = JSON.parse(readFileSync(resolve(output, 'package.json'), 'utf8'))
stagedPackage.scripts = { ...stagedPackage.scripts, dev: 'vite', build: 'vue-tsc --noEmit && vite build' }
for (const script of ['generate:registry', 'generate:contracts', 'verify:contracts', 'verify:profiles', 'verify:assembly-boundaries', 'assemble:profile']) delete stagedPackage.scripts[script]
delete stagedPackage.devDependencies?.['openapi-typescript']
writeFileSync(resolve(output, 'package.json'), `${JSON.stringify(stagedPackage, null, 2)}\n`, 'utf8')
const stagedLock = createStagedLock(frontendRoot, stagedPackage)
writeFileSync(resolve(output, 'package-lock.json'), `${JSON.stringify(stagedLock, null, 2)}\n`, 'utf8')
writeFileSync(resolve(output, 'ASSEMBLED_PROFILE.json'), `${JSON.stringify({ build_id: manifest.build_id, modules: manifest.modules, chromatography_types: manifest.chromatography_types, package_kind: 'static-assembly', release_ready: releaseReadyRequested && incompleteModules.length === 0, incomplete_modules: incompleteModules }, null, 2)}\n`, 'utf8')
console.log(`assembled ${profile} -> ${output}`)
