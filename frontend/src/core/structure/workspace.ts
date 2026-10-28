import type { InjectionKey, Ref } from 'vue'
import { ref, shallowReactive } from 'vue'
import type { Structure } from '@/core/types'

export interface StructureTargetController {
  id: string
  label: string
  projectId: string
  disabled: Ref<boolean>
  smiles: Ref<string>
  getSmiles(): string
  setSmiles(value: string): void
  invalidate(): void
  normalize(): Promise<Structure | null>
}

export interface StructureWorkspaceContext {
  targets: Map<string, StructureTargetController>
  activeTargetId: Ref<string | null>
  registerTarget(target: StructureTargetController): () => void
}

export const structureWorkspaceKey: InjectionKey<StructureWorkspaceContext> = Symbol('structure-workspace')

export function createStructureWorkspaceContext(): StructureWorkspaceContext {
  const targets = shallowReactive(new Map<string, StructureTargetController>())
  const activeTargetId = ref<string | null>(null)
  return {
    targets,
    activeTargetId,
    registerTarget(target) {
      targets.set(target.id, target)
      if (!activeTargetId.value) activeTargetId.value = target.id
      return () => {
        if (targets.get(target.id) !== target) return
        targets.delete(target.id)
        if (activeTargetId.value === target.id) activeTargetId.value = null
      }
    },
  }
}
