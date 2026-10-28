import type { Component } from 'vue'
import type { RouteRecordRaw } from 'vue-router'
import type { ModuleId } from '@/app/profile'

export interface NavigationEntry {
  id: string
  label: string
  module: ModuleId
  path: string
  component: Component
  icon: string
  order: number
  childModules?: readonly ModuleId[]
}

export interface FrontendModule {
  id: ModuleId
  navigation: readonly NavigationEntry[]
  routes: readonly RouteRecordRaw[]
}

export interface FrontendModuleExtension {
  id: string
  parent: ModuleId
  navigation: readonly NavigationEntry[]
  routes: readonly RouteRecordRaw[]
}
