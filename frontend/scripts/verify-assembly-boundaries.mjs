import { execFileSync } from 'node:child_process'
import { existsSync, mkdirSync, rmSync, symlinkSync, unlinkSync, writeFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const frontendRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const assembler = resolve(frontendRoot, 'scripts/assemble-profile.mjs')
const boundaryRoot = resolve(frontendRoot, '.assembly-boundary-check')
const rootSentinel = resolve(frontendRoot, 'src', `.assembly-boundary-${process.pid}.sentinel`)
const srcSentinel = resolve(frontendRoot, 'src', `.assembly-boundary-src-${process.pid}.sentinel`)
const publicSentinel = resolve(frontendRoot, 'public', `.assembly-boundary-public-${process.pid}.sentinel`)
const releaseOutput = resolve(frontendRoot, '.profile-check', `release-attempt-${process.pid}`)
const releaseSentinel = resolve(releaseOutput, 'sentinel.txt')
const run = (outputArg) => {
  try {
    execFileSync(process.execPath, [assembler, '--profile', 'minimal-structure-v1', '--output', outputArg], { cwd: frontendRoot, stdio: 'pipe' })
    throw new Error(`assembler unexpectedly accepted unsafe output: ${outputArg}`)
  } catch (error) {
    if (error instanceof Error && error.message.startsWith('assembler unexpectedly')) throw error
  }
}

let junctionPath = null
try {
  writeFileSync(srcSentinel, 'must-survive', 'utf8')
  run('src')
  if (!existsSync(srcSentinel)) throw new Error('unsafe output check deleted src sentinel')

  writeFileSync(publicSentinel, 'must-survive', 'utf8')
  run('public')
  if (!existsSync(publicSentinel)) throw new Error('unsafe output check deleted public sentinel')

  run('src/public/逃逸目标')
  if (!existsSync(srcSentinel)) throw new Error('unsafe output check deleted src sentinel')

  mkdirSync(releaseOutput, { recursive: true })
  writeFileSync(releaseSentinel, 'must-survive', 'utf8')
  try {
    execFileSync(process.execPath, [assembler, '--profile', 'sfc-v1', '--release-ready', '--output', `.profile-check/release-attempt-${process.pid}`], { cwd: frontendRoot, stdio: 'pipe' })
    throw new Error('assembler unexpectedly accepted incomplete release-ready profile')
  } catch (error) {
    if (error instanceof Error && error.message.startsWith('assembler unexpectedly')) throw error
  }
  if (!existsSync(releaseSentinel)) throw new Error('release-ready rejection deleted its output sentinel')

  writeFileSync(rootSentinel, 'must-survive', 'utf8')
  run('.')
  if (!existsSync(rootSentinel)) throw new Error('unsafe output check deleted frontend root sentinel')

  mkdirSync(boundaryRoot, { recursive: true })
  const outside = resolve(boundaryRoot, 'junction-target')
  mkdirSync(outside, { recursive: true })
  const outsideSentinel = resolve(outside, 'sentinel.txt')
  writeFileSync(outsideSentinel, 'must-survive', 'utf8')
  const junctionName = `junction-escape-${process.pid}`
  junctionPath = resolve(frontendRoot, '.profile-check', junctionName)
  mkdirSync(resolve(frontendRoot, '.profile-check'), { recursive: true })
  try {
    symlinkSync(outside, junctionPath, 'junction')
    run(`.profile-check/${junctionName}/profile`)
    if (!existsSync(outsideSentinel)) throw new Error('symlink/junction output check deleted target sentinel')
  } catch (error) {
    if (error?.code === 'EPERM' || error?.code === 'EACCES') {
      console.log('assembly boundary checks passed; junction test skipped by filesystem permissions')
    } else {
      throw error
    }
  }
  console.log('assembly boundary checks passed; unsafe output sentinels survived')
} finally {
  if (junctionPath && existsSync(junctionPath)) unlinkSync(junctionPath)
  if (existsSync(boundaryRoot)) rmSync(boundaryRoot, { recursive: true, force: true })
  if (existsSync(releaseOutput)) rmSync(releaseOutput, { recursive: true, force: true })
  if (existsSync(srcSentinel)) rmSync(srcSentinel, { force: true })
  if (existsSync(publicSentinel)) rmSync(publicSentinel, { force: true })
  if (existsSync(rootSentinel)) rmSync(rootSentinel, { force: true })
}
