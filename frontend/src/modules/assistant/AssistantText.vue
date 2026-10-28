<script setup lang="ts">
import DOMPurify from 'dompurify'
import { marked, Renderer } from 'marked'
import { computed, nextTick, onMounted, onUpdated, ref } from 'vue'

const props = defineProps<{ text: string }>()
const root = ref<HTMLElement | null>(null)
const showSource = ref(false)
const copied = ref(false)

// Reused from PurifyX: chiral SMILES branches can otherwise become Markdown links.
const renderer = new Renderer()
const renderLink = renderer.link
renderer.link = function(token) {
  if (/^\[[^\]]*@/.test(token.raw) && !/^(?:https?:|mailto:|tel:|\/|#)/i.test(token.href)) {
    return token.raw.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
  }
  return renderLink.call(this, token)
}
const html = computed(() => {
  const parsed = marked.parse(props.text, { async: false, renderer })
  return DOMPurify.sanitize(parsed, {
    ALLOWED_TAGS: ['a', 'blockquote', 'br', 'code', 'del', 'em', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'hr', 'li', 'ol', 'p', 'pre', 'strong', 'table', 'tbody', 'td', 'th', 'thead', 'tr', 'ul'],
    ALLOWED_ATTR: ['href', 'title', 'class'],
    ALLOWED_URI_REGEXP: /^(?:(?:https?:|mailto:|tel:)|(?:\/|#|\.\.?\/|[^:/?#]+(?:[?#]|$)))/i,
    ALLOW_DATA_ATTR: false,
    ALLOW_ARIA_ATTR: false,
    FORBID_TAGS: ['audio', 'embed', 'form', 'iframe', 'img', 'math', 'object', 'source', 'svg', 'track', 'video'],
    FORBID_ATTR: ['style', 'onerror', 'onload'],
  })
})

function addCodeCopyButtons() {
  for (const pre of root.value?.querySelectorAll<HTMLElement>('.assistant-rendered pre') ?? []) {
    if (pre.querySelector(':scope > .assistant-copy-code')) continue
    const button = document.createElement('button')
    button.type = 'button'
    button.className = 'assistant-copy-code'
    button.textContent = '复制代码'
    button.addEventListener('click', async () => {
      const code = pre.querySelector('code')?.textContent ?? pre.textContent ?? ''
      try { await navigator.clipboard.writeText(code); button.textContent = '已复制' }
      catch { button.textContent = '复制失败' }
      window.setTimeout(() => { button.textContent = '复制代码' }, 1200)
    })
    pre.prepend(button)
  }
}

async function copySource() {
  try { await navigator.clipboard.writeText(props.text); copied.value = true }
  catch { copied.value = false }
  window.setTimeout(() => { copied.value = false }, 1200)
}

onMounted(() => void nextTick(addCodeCopyButtons))
onUpdated(() => void nextTick(addCodeCopyButtons))
</script>

<template>
  <div ref="root" class="assistant-text" :data-source-text="text">
    <div v-if="!showSource" class="assistant-rendered" v-html="html"></div>
    <pre v-else class="assistant-source">{{ text }}</pre>
    <div class="assistant-text-actions">
      <button class="assistant-source-toggle" type="button" :aria-expanded="showSource" @click="showSource = !showSource">{{ showSource ? '返回格式化显示' : '查看原文' }}</button>
      <button class="assistant-source-toggle" type="button" @click="copySource">{{ copied ? '已复制 Markdown' : '复制 Markdown' }}</button>
    </div>
  </div>
</template>

<style scoped>
.assistant-text { min-width: 0; overflow-wrap: anywhere; white-space: normal; }
.assistant-source { max-width: 100%; max-height: 240px; margin: 6px 0 0; padding: 8px; overflow: auto; border-radius: 7px; background: #f7fafc; color: #5f7587; white-space: pre-wrap; overflow-wrap: anywhere; }
.assistant-text-actions { display: flex; gap: 12px; margin-top: 7px; }
.assistant-source-toggle, .assistant-copy-code { padding: 0; border: 0; color: #8193a1; background: transparent; font-size: 11px; cursor: pointer; }
:deep(.assistant-rendered) { min-width: 0; overflow-wrap: anywhere; }
:deep(.assistant-rendered > :first-child) { margin-top: 0; }
:deep(.assistant-rendered > :last-child) { margin-bottom: 0; }
:deep(.assistant-rendered p), :deep(.assistant-rendered ul), :deep(.assistant-rendered ol), :deep(.assistant-rendered blockquote), :deep(.assistant-rendered table) { margin: 0 0 10px; }
:deep(.assistant-rendered h1), :deep(.assistant-rendered h2), :deep(.assistant-rendered h3), :deep(.assistant-rendered h4), :deep(.assistant-rendered h5), :deep(.assistant-rendered h6) { margin: 12px 0 7px; color: #294c66; font-size: 1em; line-height: 1.45; }
:deep(.assistant-rendered ul), :deep(.assistant-rendered ol) { padding-left: 22px; }
:deep(.assistant-rendered blockquote) { padding: 2px 0 2px 12px; border-left: 3px solid #cbdde9; color: #60798c; }
:deep(.assistant-rendered a) { color: #1475bd; overflow-wrap: anywhere; }
:deep(.assistant-rendered table) { display: block; max-width: 100%; overflow: auto; border-collapse: collapse; }
:deep(.assistant-rendered th), :deep(.assistant-rendered td) { padding: 6px 9px; border: 1px solid #dce7ee; text-align: left; }
:deep(.assistant-rendered pre) { position: relative; max-width: 100%; margin: 8px 0 10px; padding: 32px 12px 10px; overflow: auto; border-radius: 8px; background: #f2f6f9; color: #294c66; white-space: pre; }
:deep(.assistant-rendered pre code) { font-family: Consolas, 'Courier New', monospace; }
:deep(.assistant-copy-code) { position: absolute; top: 8px; right: 10px; }
</style>
