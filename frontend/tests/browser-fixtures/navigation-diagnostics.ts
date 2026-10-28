type NavigationRecord = {
  at: number
  kind: string
  [key: string]: unknown
}

const records: NavigationRecord[] = []
const maxRecords = 300
const restorers: Array<() => void> = []
let installed = false
let layoutObserver: PerformanceObserver | null = null

function elementLabel(element: Element | null) {
  if (!element) return null
  const candidate = element as HTMLElement
  return {
    tag: element.tagName.toLowerCase(),
    id: element.id || null,
    testId: element.getAttribute('data-testid'),
    ariaLabel: element.getAttribute('aria-label'),
    text: (element.textContent ?? '').trim().slice(0, 100),
  }
}

function measure(selector: string) {
  const element = document.querySelector<HTMLElement>(selector)
  if (!element) return null
  const rect = element.getBoundingClientRect()
  return {
    selector,
    label: elementLabel(element),
    top: rect.top,
    bottom: rect.bottom,
    documentTop: rect.top + window.scrollY,
    height: rect.height,
  }
}

function record(kind: string, details: Record<string, unknown> = {}) {
  const scrollingElement = document.scrollingElement
  records.push({
    at: performance.now(),
    kind,
    href: location.href,
    scrollX: window.scrollX,
    scrollY: window.scrollY,
    scrollTop: scrollingElement?.scrollTop ?? null,
    documentHeight: Math.max(document.documentElement.scrollHeight, document.body?.scrollHeight ?? 0),
    activeElement: elementLabel(document.activeElement),
    selectedNav: elementLabel(document.querySelector('[aria-current="page"], [aria-selected="true"]')),
    anchors: [
      measure('[data-testid="chromatography-tabs"]'),
      measure('.chromatography-tabs'),
      measure('#main-content'),
      measure('main'),
    ].filter(Boolean),
    ...details,
  })
  if (records.length > maxRecords) records.splice(0, records.length - maxRecords)
}

function listen(target: EventTarget, type: string, handler: EventListener, options?: AddEventListenerOptions) {
  target.addEventListener(type, handler, options)
  restorers.push(() => target.removeEventListener(type, handler, options))
}

function wrapMethod(target: object, key: string, describe: (self: unknown, args: unknown[]) => Record<string, unknown>) {
  const ownDescriptor = Object.getOwnPropertyDescriptor(target, key)
  let owner: object | null = target
  let descriptor = ownDescriptor
  while (!descriptor && (owner = Object.getPrototypeOf(owner))) descriptor = Object.getOwnPropertyDescriptor(owner, key)
  const original = descriptor?.value
  if (typeof original !== 'function' || !descriptor?.configurable) return
  Object.defineProperty(target, key, {
    ...descriptor,
    configurable: true,
    value: function diagnosticWrapper(this: unknown, ...args: unknown[]) {
      record(`call.${key}`, { ...describe(this, args), callStack: new Error(`${key} call site`).stack ?? null })
      return Reflect.apply(original, this, args)
    },
  })
  restorers.push(() => {
    if (ownDescriptor) Object.defineProperty(target, key, ownDescriptor)
    else Reflect.deleteProperty(target, key)
  })
}

function scrollArguments(_self: unknown, args: unknown[]) {
  return { args: args.map((value) => value && typeof value === 'object' ? { ...value as object } : value) }
}

export const navigationDiagnostics = {
  mark(label: string) { record('mark', { label }) },
  read() { return records.map((entry) => ({ ...entry })) },
  snapshot() { return records.map((entry) => ({ ...entry })) },
  clear() { records.length = 0 },
  stop() {
    if (!installed) return
    layoutObserver?.disconnect()
    layoutObserver = null
    restorers.splice(0).reverse().forEach((restore) => restore())
    installed = false
  },
}

export function installNavigationDiagnostics() {
  if (installed) return navigationDiagnostics
  installed = true
  for (const type of ['scroll', 'focusin', 'focusout', 'keydown', 'popstate', 'hashchange', 'resize']) {
    listen(window, type, ((event: Event) => record(`event.${type}`, {
      trusted: event.isTrusted,
      eventTarget: elementLabel(event.target instanceof Element ? event.target : null),
      key: event instanceof KeyboardEvent ? event.key : null,
      shiftKey: event instanceof KeyboardEvent ? event.shiftKey : null,
    })) as EventListener, { capture: true, passive: true })
  }
  wrapMethod(Window.prototype, 'scrollTo', scrollArguments)
  wrapMethod(Window.prototype, 'scrollBy', scrollArguments)
  wrapMethod(Element.prototype, 'scrollIntoView', (_self, args) => scrollArguments(null, args))
  wrapMethod(HTMLElement.prototype, 'focus', (_self, args) => scrollArguments(null, args))
  try {
    layoutObserver = new PerformanceObserver((list) => {
      for (const entry of list.getEntries() as PerformanceEntry[]) {
        const shift = entry as PerformanceEntry & { value?: number; hadRecentInput?: boolean; sources?: Array<{ node?: Node; previousRect?: DOMRectReadOnly; currentRect?: DOMRectReadOnly }> }
        record('layout-shift', {
          value: shift.value ?? null,
          hadRecentInput: shift.hadRecentInput ?? null,
          sources: shift.sources?.map((source) => ({
            node: source.node instanceof Element ? elementLabel(source.node) : null,
            previousRect: source.previousRect ? { top: source.previousRect.top, height: source.previousRect.height } : null,
            currentRect: source.currentRect ? { top: source.currentRect.top, height: source.currentRect.height } : null,
          })) ?? [],
        })
      }
    })
    layoutObserver.observe({ type: 'layout-shift', buffered: true })
  } catch { layoutObserver = null }
  record('installed')
  return navigationDiagnostics
}
