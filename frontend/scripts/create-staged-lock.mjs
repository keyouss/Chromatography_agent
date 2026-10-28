import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const packageName = (name) => name.startsWith('@') ? name.split('/').slice(0, 2).join('/') : name

const parentPackagePath = (packagePath) => {
  if (!packagePath) return ''
  const marker = '/node_modules/'
  const index = packagePath.lastIndexOf(marker)
  return index < 0 ? '' : packagePath.slice(0, index)
}

const packagePathFor = (parentPath, name) => parentPath ? `${parentPath}/node_modules/${name}` : `node_modules/${name}`

const findDependency = (packages, parentPath, name) => {
  let current = parentPath
  while (true) {
    const candidate = packagePathFor(current, name)
    if (packages[candidate]) return candidate
    if (!current) return null
    current = parentPackagePath(current)
  }
}

const sortedObject = (value = {}) => Object.fromEntries(Object.entries(value).sort(([left], [right]) => left.localeCompare(right)))

export function createStagedLock(frontendRoot, stagedPackage) {
  const sourceLock = JSON.parse(readFileSync(resolve(frontendRoot, 'package-lock.json'), 'utf8'))
  const sourcePackages = sourceLock.packages ?? {}
  if (!sourcePackages['']) throw new Error('Frontend package-lock.json has no root package entry')

  const selected = new Set([''])
  const queue = ['']
  const visitDependency = (parentPath, name, optional) => {
    const packagePath = findDependency(sourcePackages, parentPath, name)
    if (!packagePath) {
      if (optional) return
      throw new Error(`Frontend package-lock.json is missing dependency closure entry: ${name} from ${parentPath || '<root>'}`)
    }
    if (!selected.has(packagePath)) {
      selected.add(packagePath)
      queue.push(packagePath)
    }
  }

  while (queue.length) {
    const packagePath = queue.shift()
    const entry = packagePath === ''
      ? { ...sourcePackages[''], dependencies: stagedPackage.dependencies, devDependencies: stagedPackage.devDependencies, optionalDependencies: stagedPackage.optionalDependencies }
      : sourcePackages[packagePath]
    const optionalNames = new Set(Object.keys(entry.optionalDependencies ?? {}))
    const dependencyNames = new Set([
      ...Object.keys(entry.dependencies ?? {}),
      ...Object.keys(entry.optionalDependencies ?? {}),
      ...Object.keys(entry.peerDependencies ?? {}),
      ...(packagePath === '' ? Object.keys(entry.devDependencies ?? {}) : []),
    ])
    for (const name of dependencyNames) {
      const optionalPeer = entry.peerDependenciesMeta?.[name]?.optional === true
      visitDependency(packagePath, packageName(name), optionalNames.has(name) || optionalPeer)
    }
  }

  const packages = {}
  for (const packagePath of [...selected].sort((left, right) => left.localeCompare(right))) {
    packages[packagePath] = sourcePackages[packagePath]
  }
  packages[''] = {
    ...packages[''],
    name: stagedPackage.name,
    version: stagedPackage.version,
    dependencies: sortedObject(stagedPackage.dependencies),
    devDependencies: sortedObject(stagedPackage.devDependencies),
  }

  const lock = { ...sourceLock, packages }
  if (lock.dependencies) delete lock.dependencies
  return lock
}
