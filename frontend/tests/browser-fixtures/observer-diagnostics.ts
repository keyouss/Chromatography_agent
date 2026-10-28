type DiagnosticRecord = {
  at: number
  kind: string
  [key: string]: unknown
}

const records: DiagnosticRecord[] = []
const maxRecords = 200
let installed = false
let originalObserve: MutationObserver['observe'] | null = null
let errorListener: ((event: ErrorEvent) => void) | null = null
let rejectionListener: ((event: PromiseRejectionEvent) => void) | null = null

function record(kind: string, details: Record<string, unknown> = {}) {
  records.push({ at: performance.now(), kind, ...details })
  if (records.length > maxRecords) records.splice(0, records.length - maxRecords)
}

function targetInfo(target: unknown) {
  const isObject = target !== null && typeof target === 'object'
  const node = isObject ? target as Partial<Node> : null
  return {
    targetType: target === null ? 'null' : typeof target,
    isNodeInstance: typeof Node !== 'undefined' && target instanceof Node,
    nodeType: typeof node?.nodeType === 'number' ? node.nodeType : null,
    nodeName: typeof node?.nodeName === 'string' ? node.nodeName : null,
  }
}

function errorInfo(reason: unknown) {
  const error = reason instanceof Error ? reason : null
  return {
    errorName: error?.name ?? typeof reason,
    errorMessage: error?.message ?? String(reason),
    errorStack: error?.stack ?? null,
  }
}

export function installObserverDiagnostics() {
  if (installed) return observerDiagnostics
  installed = true
  originalObserve = MutationObserver.prototype.observe
  MutationObserver.prototype.observe = function observeWithDiagnostics(target: Node, options: MutationObserverInit) {
    const callStack = new Error('MutationObserver.observe call site').stack ?? null
    record('mutation-observer.observe', { ...targetInfo(target), options, callStack })
    try {
      return Reflect.apply(originalObserve!, this, [target, options])
    } catch (reason) {
      record('mutation-observer.observe-threw', { ...targetInfo(target), ...errorInfo(reason), callStack })
      throw reason
    }
  }
  errorListener = (event) => record('window.error', {
    message: event.message,
    filename: event.filename,
    line: event.lineno,
    column: event.colno,
    ...errorInfo(event.error),
  })
  rejectionListener = (event) => record('window.unhandledrejection', errorInfo(event.reason))
  window.addEventListener('error', errorListener)
  window.addEventListener('unhandledrejection', rejectionListener)
  return observerDiagnostics
}

export const observerDiagnostics = {
  read() { return records.map((entry) => ({ ...entry })) },
  snapshot() { return records.map((entry) => ({ ...entry })) },
  clear() { records.length = 0 },
  stop() {
    if (!installed) return
    MutationObserver.prototype.observe = originalObserve!
    if (errorListener) window.removeEventListener('error', errorListener)
    if (rejectionListener) window.removeEventListener('unhandledrejection', rejectionListener)
    installed = false
    originalObserve = null
    errorListener = null
    rejectionListener = null
  },
}
