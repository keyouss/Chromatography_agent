<script setup lang="ts">
import { computed, watch } from 'vue'
import { useConfirmationStore } from '@/stores/confirmations'
import type { ResourceRef } from '@/core/types'
const props = defineProps<{ resourceRef: ResourceRef; action: string }>(); const store = useConfirmationStore(); const busy = computed(() => store.state === 'loading')
const previewMatches = computed(() => store.previewIsFor(props.resourceRef, props.action))
const matches = computed(() => store.isFor(props.resourceRef, props.action))
watch(() => [props.resourceRef.id, props.resourceRef.type, props.action], () => store.reset(), { immediate: true })
async function preview() { await store.loadPreview(props.resourceRef, props.action) }; async function confirm() { if (previewMatches.value) await store.create() }
</script>
<template>
  <section class="panel confirmation-card"><div class="panel-heading"><div><span class="eyebrow">HUMAN CONFIRMATION</span><h2>提交前确认</h2></div><span class="badge" :class="store.state">{{ matches && store.confirmation?.status || (store.state === 'error' ? '不可用' : '未预览') }}</span></div>
    <p class="muted">确认摘要必须来自服务端预览，并绑定资源 revision、配置哈希和 ETag。</p>
    <button v-if="!previewMatches" class="secondary" :disabled="busy" @click="preview">{{ busy ? '读取预览中…' : '读取服务端预览' }}</button>
    <template v-else-if="store.preview"><ul class="summary-list"><li v-for="item in store.preview.summary" :key="item.label"><span>{{ item.label }}</span><strong>{{ item.value }}<small v-if="item.unit"> {{ item.unit }}</small></strong></li></ul><p v-if="store.preview.blockers.length" class="error-text">{{ store.preview.blockers.map((item) => item.message).join('；') }}</p><button class="primary" :disabled="busy || !store.preview.can_confirm" @click="confirm">{{ busy ? '确认中…' : '确认此版本' }}</button></template>
    <p v-if="store.error" class="error-text">{{ store.error }}</p>
  </section>
</template>
