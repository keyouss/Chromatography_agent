<script setup lang="ts">
import { provide } from 'vue'
import { createStructureWorkspaceContext, structureWorkspaceKey, type StructureWorkspaceContext } from '@/core/structure/workspace'

const props = defineProps<{ context?: StructureWorkspaceContext }>()
provide(structureWorkspaceKey, props.context ?? createStructureWorkspaceContext())
</script>

<template><slot /></template>
