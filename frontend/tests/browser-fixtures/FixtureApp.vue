<script setup lang="ts">
import { ref } from 'vue'
import PredictionView from '@/modules/prediction/PredictionView.vue'
import AssistantText from '@/modules/assistant/AssistantText.vue'
import ImeDiagnostics from './ImeDiagnostics.vue'
import { navigationDiagnostics, observerDiagnostics } from './diagnostics-bootstrap'
import { readLocalProbeCount } from './synthetic-fetch'

type FixtureMode = 'many-ready' | 'no-ready' | 'scope-mismatch'
const props = defineProps<{ mode: FixtureMode; switchMode: (mode: FixtureMode) => void; releaseSlowModelA: () => void }>()
const probeResult = ref('未读取本地 probe 计数')
const diagnosticOutput = ref('诊断记录待读取')
function readDiagnostics() {
  navigationDiagnostics.mark('read-output')
  diagnosticOutput.value = JSON.stringify({ navigation: navigationDiagnostics.read(), observer: observerDiagnostics.read() }, null, 2)
}
function armNavigation() {
  window.setTimeout(() => {
    navigationDiagnostics.clear()
    window.scrollTo(0, 40)
    window.requestAnimationFrame(() => navigationDiagnostics.mark('armed-scroll-40'))
  }, 400)
}
const markdown = `# SYNTHETIC Markdown 安全夹具

可见文本 sentinel：VISIBLE_TEXT_SENTINEL

<script>alert('SYNTHETIC_SCRIPT_SENTINEL')</scr${'ipt'}>

SENTINEL_AFTER_SCRIPT

[危险 scheme sentinel](javascript:alert('SYNTHETIC_JS_SENTINEL'))

SENTINEL_AFTER_JAVASCRIPT_LINK

[data scheme sentinel](data:text/html,SYNTHETIC_DATA_LINK_SENTINEL)

SENTINEL_AFTER_DATA_LINK

<img src="/__fixture/remote-probe?sentinel=LOCAL_PROBE_SENTINEL" alt="LOCAL_PROBE_SENTINEL">

SENTINEL_AFTER_LOCAL_IMAGE

<img src="https://outside.invalid/SYNTHETIC_REMOTE_SENTINEL.png" alt="SYNTHETIC_REMOTE_SENTINEL">

SENTINEL_AFTER_REMOTE_IMAGE

![SYNTHETIC_MARKDOWN_IMAGE](/__fixture/remote-probe?sentinel=MARKDOWN_IMAGE_SENTINEL)

SENTINEL_AFTER_MARKDOWN_IMAGE

<iframe src="https://outside.invalid/SYNTHETIC_IFRAME_SENTINEL"></iframe>

SENTINEL_AFTER_IFRAME

<svg onload="alert('SYNTHETIC_SVG_SENTINEL')"><text>SYNTHETIC_SVG_SENTINEL</text></svg>

SENTINEL_AFTER_SVG

<div style="background-image:url(https://outside.invalid/SYNTHETIC_STYLE_SENTINEL)" onclick="alert(1)">SYNTHETIC_STYLE_SENTINEL</div>

SENTINEL_AFTER_STYLE_ATTRIBUTE

<style>body { background-image: url(https://outside.invalid/SYNTHETIC_STYLE_TAG_SENTINEL) }</style>

SENTINEL_AFTER_STYLE_TAG

| Fixture | Value |
| --- | --- |
| SFC | Synthetic only |`

async function inspectProbe() {
  try {
    const result = await readLocalProbeCount()
    const root = document.querySelector<HTMLElement>('[data-testid="markdown-preview"] .assistant-rendered')
    const forbidden = root?.querySelectorAll('script, style, img, iframe, svg, video, audio, object, embed, source, track, math, [style], [onclick], [onerror], [onload]').length ?? -1
    const unsafeLinks = [...(root?.querySelectorAll<HTMLAnchorElement>('a') ?? [])]
      .filter((anchor) => /^(?:javascript|data|vbscript):/i.test(anchor.getAttribute('href') ?? '')).length
    const text = root?.textContent ?? ''
    const sentinelVisible = text.includes('VISIBLE_TEXT_SENTINEL')
    const payloadSentinels = [
      'SENTINEL_AFTER_SCRIPT', 'SENTINEL_AFTER_JAVASCRIPT_LINK', 'SENTINEL_AFTER_DATA_LINK',
      'SENTINEL_AFTER_LOCAL_IMAGE', 'SENTINEL_AFTER_REMOTE_IMAGE', 'SENTINEL_AFTER_MARKDOWN_IMAGE',
      'SENTINEL_AFTER_IFRAME', 'SENTINEL_AFTER_SVG', 'SENTINEL_AFTER_STYLE_ATTRIBUTE', 'SENTINEL_AFTER_STYLE_TAG',
    ]
    const parsedSentinels = payloadSentinels.filter((sentinel) => text.includes(sentinel)).length
    probeResult.value = `DOM: sentinel=${sentinelVisible ? 'visible' : 'missing'}, payload sentinels=${parsedSentinels}/${payloadSentinels.length}, forbidden nodes/attrs=${forbidden}, unsafe links=${unsafeLinks}; loopback image GETs=${result.localImageProbeRequests}`
  } catch (reason) { probeResult.value = reason instanceof Error ? reason.message : 'probe 读取失败' }
}
</script>

<template>
  <main class="fixture-page">
    <header class="fixture-banner">
      <div><strong>SYNTHETIC ONLY · 无 LLM · 无真实 API 写入</strong><p>独立 127.0.0.1:5185；SFC 交互复用实际 PredictionView 和 SfcSearchForm。</p></div>
      <div class="mode-buttons" aria-label="合成能力场景">
        <button type="button" :aria-pressed="props.mode === 'many-ready'" @click="props.switchMode('many-ready')">多 READY：手动选择与迟到响应</button>
        <button type="button" :aria-pressed="props.mode === 'no-ready'" @click="props.switchMode('no-ready')">无 READY 模型</button>
        <button type="button" :aria-pressed="props.mode === 'scope-mismatch'" @click="props.switchMode('scope-mismatch')">工具范围无匹配</button>
      </div>
    </header>

    <section class="fixture-instructions" aria-label="验收步骤">
      <strong>浏览器操作步骤</strong>
      <ol>
        <li>多 READY 场景初始模型应为空；选择 SYNTHETIC_MODEL_A，等待“正在读取”后选 SYNTHETIC_MODEL_B；B 的条件应先出现。</li>
        <li>点击“释放 A 慢响应”；页面仍应保持 B 条件（SYNTHETIC_CURRENT_B_COLUMN）。</li>
        <li>选择“无 READY 模型”确认模型不可选；“工具范围无匹配”应明确显示无可匹配模型。</li>
        <li>检查 VISIBLE_TEXT_SENTINEL 与 payload sentinels=10/10；危险节点/属性不存在；probe 计数要求为 0。Markdown image 和 raw img 分别检查。</li>
      </ol>
      <div class="fixture-actions">
        <button type="button" data-testid="release-model-a" @click="props.releaseSlowModelA()">释放 A 慢响应</button>
        <button type="button" data-testid="inspect-probe" @click="inspectProbe">读取本地图片 probe 计数</button>
        <output data-testid="probe-count">{{ probeResult }}</output>
      </div>
    </section>

    <aside class="diagnostic-controls" aria-label="隔离诊断控制">
      <button type="button" @click="armNavigation">准备导航滚动 40</button>
      <button type="button" @click="readDiagnostics">读取诊断记录</button>
    </aside>
    <PredictionView />
    <ImeDiagnostics />
    <details><summary>诊断 JSON（只读）</summary><pre data-testid="diagnostics-output">{{ diagnosticOutput }}</pre></details>

    <section class="markdown-fixture" aria-labelledby="markdown-fixture-title">
      <h2 id="markdown-fixture-title">实际 AssistantText.vue · Markdown 安全检查</h2>
      <p>以下输入含合成 raw HTML、javascript/data 链接、raw img、Markdown image、iframe、SVG 和 style sentinel；CSP 阻止外联，loopback probe 用于发现被错误保留的自动加载图片。</p>
      <div data-testid="markdown-preview"><AssistantText :text="markdown" /></div>
      <p class="fixture-note">DOMPurify 检查应基于实际渲染 DOM；仅看到 CSP 拦截不算通过。</p>
    </section>
  </main>
</template>

<style scoped>
.fixture-page { display: grid; gap: 16px; max-width: 1440px; margin: 0 auto; padding: 16px; color: #213d53; font: 14px/1.5 system-ui, sans-serif; }
.fixture-banner,.fixture-instructions,.markdown-fixture { border: 1px solid #d3e0ea; border-radius: 12px; padding: 14px; background: #fff; }
.fixture-banner { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 12px; background: #f2f8fc; }
.fixture-banner p,.markdown-fixture p { margin: 4px 0 0; color: #587187; }
.mode-buttons,.fixture-actions { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
button { border: 1px solid #b6cddd; border-radius: 7px; padding: 7px 10px; background: #fff; color: #264e6c; cursor: pointer; }
button[aria-pressed="true"] { border-color: #1475bd; background: #e7f4ff; }
.fixture-instructions ol { margin: 8px 0; padding-left: 24px; }
.fixture-note { font-size: 12px; }
.diagnostic-controls { position: fixed; right: 12px; bottom: 12px; z-index: 100; display: flex; gap: 8px; padding: 8px; background: #f2f8fc; border: 1px solid #b6cddd; border-radius: 8px; }
pre { max-height: 360px; overflow: auto; white-space: pre-wrap; overflow-wrap: anywhere; }
</style>
