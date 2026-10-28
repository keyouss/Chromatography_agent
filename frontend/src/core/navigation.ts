import type { ModuleId } from '@/app/profile'
import { navigationRegistry } from '@/app/generated/registry'
import type { NavigationEntry } from '@/app/module'

export type NavigationItem = NavigationEntry
export { navigationRegistry }

export function navigationForModule(module: ModuleId) {
  return navigationRegistry.filter((item) => item.module === module || item.childModules?.includes(module))
}
