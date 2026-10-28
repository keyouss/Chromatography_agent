import { apiClient } from '@/core/api/client'
import type { AccessContextView } from '@/core/types'

const ACCESS_CONTEXT_TIMEOUT_MS = 10000
let accessContextFlight: Promise<unknown> | null = null

export function requestAccessContext<T = AccessContextView>(): Promise<T> {
  if (!accessContextFlight) {
    const controller = new AbortController()
    const timeout = setTimeout(() => controller.abort(), ACCESS_CONTEXT_TIMEOUT_MS)
    accessContextFlight = apiClient.get<T>('/system/access-context', { signal: controller.signal })
      .catch((reason) => {
        if (controller.signal.aborted) throw new Error('访问上下文请求超时，请检查服务端连接')
        throw reason
      })
      .finally(() => { clearTimeout(timeout); accessContextFlight = null })
  }
  return accessContextFlight as Promise<T>
}

export function accessibleProjectIds(context: AccessContextView | null | undefined): string[] {
  return context?.accessible_projects.map((project) => project.id) ?? []
}

export function resolveProjectId(context: AccessContextView | null | undefined, explicitProjectId?: string | null): string | null {
  if (!context || context.project_selection === 'no_accessible_projects' || !context.accessible_projects.length) return null
  const ids = new Set(accessibleProjectIds(context))
  if (explicitProjectId && ids.has(explicitProjectId)) return explicitProjectId
  if (context.project_selection === 'needs_selection') return null
  if (context.default_project_id && ids.has(context.default_project_id)) return context.default_project_id
  return null
}

export function requiresProjectSelection(context: AccessContextView | null | undefined): boolean {
  return context?.project_selection === 'needs_selection'
}

export type ProjectSelectionState = 'blocked' | 'needs_selection' | 'resolved'

export function projectSelectionState(context: AccessContextView | null | undefined, explicitProjectId?: string | null): ProjectSelectionState {
  if (!context || context.project_selection === 'no_accessible_projects' || !context.accessible_projects.length) return 'blocked'
  return resolveProjectId(context, explicitProjectId) ? 'resolved' : 'needs_selection'
}
