<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { ApiClientError, createIdempotencyKey } from '@/core/api/client'
import { activateLlmConfiguration, createLlmConfiguration, getLlmSettings, getLlmTest, getPublicSettings, submitLlmConfigurationTest } from '@/modules/settings/llm-api'
import type { CandidatePublicSettings, LlmConfigurationCreateBody, LlmConfigurationView, LlmSettingsView, LlmTestResult } from '@/modules/settings/llm-api'
import { useAppStore } from '@/stores/app'

type SecretEntryMode = 'new' | 'reuse'
type OperationState = 'idle' | 'loading'

const app = useAppStore()
const publicSettings = ref<CandidatePublicSettings | null>(null)
const publicSettingsLoading = ref(true)
const publicSettingsError = ref('')
const settings = ref<LlmSettingsView | null>(null)
const settingsEtag = ref<string | null>(null)
const settingsLoading = ref(true)
const settingsFresh = ref(false)
const settingsError = ref('')
const createdCandidate = ref<LlmConfigurationView | null>(null)
const selectedCandidateId = ref('')
const createState = ref<OperationState>('idle')
const createError = ref('')
const createNotice = ref('')
const testState = ref<'idle' | 'submitting' | 'polling' | 'paused' | 'ready' | 'failed' | 'error'>('idle')
const testResult = ref<LlmTestResult | null>(null)
const testError = ref('')
const testCandidate = ref<{ id: string; revision: string } | null>(null)
const activeTestId = ref('')
const activationState = ref<OperationState>('idle')
const activationError = ref('')
const activationNotice = ref('')
const secretEntryMode = ref<SecretEntryMode>('new')
const form = reactive({
  name: '',
  base_url: '',
  model_id: '',
  api_key: '',
  reuse_secret_from_configuration_id: '',
  timeout_seconds: 90,
  max_retries: 2,
  max_output_tokens: 4096,
  external_send_policy: 'deny_external' as 'deny_external' | 'approved_endpoint_only',
})

let settingsRequest = 0
let pollGeneration = 0
let pollStartedAt = 0
const maxContinuousPollMs = 5 * 60 * 1000
let pollTimer: ReturnType<typeof setTimeout> | null = null
let pollController: AbortController | null = null

const configurationItems = computed(() => {
  const items = new Map<string, LlmConfigurationView>()
  for (const item of settings.value?.candidate_configurations ?? []) items.set(item.id, item)
  if (createdCandidate.value && !items.has(createdCandidate.value.id)) items.set(createdCandidate.value.id, createdCandidate.value)
  const active = settings.value?.active_configuration
  if (active) items.set(active.id, active)
  return [...items.values()]
})
const activeConfiguration = computed(() => settings.value?.active_configuration ?? null)
const candidateConfigurations = computed(() => configurationItems.value.filter((item) => item.status === 'CANDIDATE' || item.status === 'TESTED'))
const retiredConfigurations = computed(() => configurationItems.value.filter((item) => item.status === 'RETIRED'))
const availableSecrets = computed(() => configurationItems.value.filter((item) => item.has_api_key))
const selectedCandidate = computed(() => candidateConfigurations.value.find((item) => item.id === selectedCandidateId.value) ?? null)
const canActivateSelected = computed(() => Boolean(
  selectedCandidate.value
  && settings.value
  && settingsFresh.value
  && settingsEtag.value
  && testState.value === 'ready'
  && testResult.value?.status === 'SUCCEEDED'
  && testResult.value.configuration_id === selectedCandidate.value.id
  && testResult.value.configuration_revision === selectedCandidate.value.revision
  && testCandidate.value?.id === selectedCandidate.value.id
  && testCandidate.value.revision === selectedCandidate.value.revision
  && testResult.value.checks.connectivity === 'PASSED'
  && testResult.value.checks.streaming === 'PASSED'
  && testResult.value.checks.tool_calling === 'PASSED'
))

function safeCode(value: unknown): string | null {
  return typeof value === 'string' && /^[A-Z][A-Z0-9_]{0,63}$/.test(value) ? value : null
}

function safeRequestError(reason: unknown): string {
  if (reason instanceof ApiClientError) {
    const code = safeCode(reason.code) ?? `HTTP_${reason.status}`
    if (reason.status === 409 || reason.status === 412) return `请求发生版本冲突（${code}）；正在重新读取服务端状态。`
    return `请求未完成（${code}）；服务端详细信息已隐藏。`
  }
  return '请求未完成；服务端详细信息已隐藏。'
}

function safeTestFailure(error: unknown): string {
  if (error && typeof error === 'object' && !Array.isArray(error)) {
    const code = safeCode((error as Record<string, unknown>).code)
    if (code) return `连接测试未通过（${code}）；服务端详细信息已隐藏。`
  }
  return '连接测试未通过；服务端详细信息已隐藏。'
}

async function loadPublicSettings() {
  publicSettingsLoading.value = true
  publicSettingsError.value = ''
  try {
    const result = await getPublicSettings()
    publicSettings.value = result.data
  } catch (reason) {
    publicSettingsError.value = safeRequestError(reason)
  } finally {
    publicSettingsLoading.value = false
  }
}

function formatLimits(limits: Record<string, number>) {
  return Object.entries(limits).map(([name, value]) => `${name}: ${value}`).join(' · ') || '服务端未声明'
}

function stopPolling() {
  pollGeneration += 1
  if (pollTimer) clearTimeout(pollTimer)
  pollTimer = null
  pollController?.abort()
  pollController = null
}

async function loadSettings() {
  const request = ++settingsRequest
  if (selectedCandidateId.value) resetTestState()
  settingsLoading.value = true
  settingsFresh.value = false
  settingsEtag.value = null
  settingsError.value = ''
  try {
    const result = await getLlmSettings()
    if (request !== settingsRequest) return
    settings.value = result.data
    settingsEtag.value = result.etag
    settingsFresh.value = true
    if (createdCandidate.value && result.data.candidate_configurations.some((item) => item.id === createdCandidate.value?.id)) {
      createdCandidate.value = null
    }
    if (selectedCandidateId.value) {
      resetTestState()
      const candidate = candidateConfigurations.value.find((item) => item.id === selectedCandidateId.value)
      if (candidate) restoreCandidateTest(candidate)
      else {
        selectedCandidateId.value = ''
        resetTestState()
      }
    }
  } catch (reason) {
    if (request === settingsRequest) {
      settingsError.value = safeRequestError(reason)
      if (selectedCandidate.value?.last_test_id) {
        testState.value = 'error'
        testError.value = '配置刷新失败，无法确认此候选的测试状态。'
      }
    }
  } finally {
    if (request === settingsRequest) settingsLoading.value = false
  }
}

function resetTestState() {
  stopPolling()
  testState.value = 'idle'
  testResult.value = null
  testError.value = ''
  testCandidate.value = null
  activeTestId.value = ''
}

function restoreCandidateTest(candidate: LlmConfigurationView) {
  const testId = candidate.last_test_id
  if (!testId) return
  testCandidate.value = { id: candidate.id, revision: candidate.revision }
  activeTestId.value = testId
  testError.value = ''
  pollStartedAt = Date.now()
  testState.value = 'polling'
  void pollTest(testId, pollGeneration)
}

function selectCandidate(candidate: LlmConfigurationView) {
  if (selectedCandidateId.value === candidate.id) return
  resetTestState()
  selectedCandidateId.value = candidate.id
  activationError.value = ''
  activationNotice.value = ''
  restoreCandidateTest(candidate)
}

function setSecretEntryMode(mode: SecretEntryMode) {
  secretEntryMode.value = mode
  if (mode === 'new') form.reuse_secret_from_configuration_id = ''
  else form.api_key = ''
}

function resetForm() {
  Object.assign(form, {
    name: '', base_url: '', model_id: '', api_key: '', reuse_secret_from_configuration_id: '',
    timeout_seconds: 90, max_retries: 2, max_output_tokens: 4096, external_send_policy: 'deny_external',
  })
  secretEntryMode.value = 'new'
}

function cancelDraft() {
  resetForm()
  createError.value = ''
  createNotice.value = '草稿已清除。'
}

async function createCandidate() {
  if (createState.value === 'loading') return
  createError.value = ''
  createNotice.value = ''
  const credential = secretEntryMode.value === 'new'
    ? (form.api_key ? { api_key: form.api_key } : {})
    : { reuse_secret_from_configuration_id: form.reuse_secret_from_configuration_id }
  const body: LlmConfigurationCreateBody = {
    name: form.name.trim(),
    api_format: 'openai_chat',
    base_url: form.base_url.trim(),
    model_id: form.model_id.trim(),
    timeout_seconds: Number(form.timeout_seconds),
    max_retries: Number(form.max_retries),
    max_output_tokens: Number(form.max_output_tokens),
    external_send_policy: form.external_send_policy,
    ...credential,
  }
  createState.value = 'loading'
  try {
    const result = await createLlmConfiguration(body, createIdempotencyKey())
    createdCandidate.value = result.data
    resetTestState()
    selectedCandidateId.value = result.data.id
    createNotice.value = '候选配置已由服务端创建。'
    await loadSettings()
  } catch (reason) {
    createError.value = safeRequestError(reason)
  } finally {
    form.api_key = ''
    createState.value = 'idle'
  }
}

function scheduleTestPoll(testId: string, generation: number) {
  pollTimer = setTimeout(() => {
    pollTimer = null
    void pollTest(testId, generation)
  }, 1000)
}

async function pollTest(testId: string, generation: number) {
  if (generation !== pollGeneration) return
  const controller = new AbortController()
  pollController = controller
  try {
    const result = await getLlmTest(testId, controller.signal)
    if (generation !== pollGeneration) return
    if (result.data.id !== testId || result.data.configuration_id !== testCandidate.value?.id || result.data.configuration_revision !== testCandidate.value.revision) {
      throw new Error('测试结果与当前候选配置不匹配')
    }
    testResult.value = result.data
    if (result.data.status === 'QUEUED' || result.data.status === 'RUNNING') {
      if (Date.now() - pollStartedAt >= maxContinuousPollMs) {
        stopPolling()
        testState.value = 'paused'
        testError.value = '自动查询已暂停（连续查询达到 5 分钟）；服务端任务状态未判定为失败，可继续查询。'
        return
      }
      testState.value = 'polling'
      scheduleTestPoll(testId, generation)
    } else {
      testState.value = result.data.status === 'SUCCEEDED' ? 'ready' : 'failed'
    }
  } catch (reason) {
    if (controller.signal.aborted || generation !== pollGeneration) return
    testState.value = 'error'
    testError.value = safeRequestError(reason)
  } finally {
    if (pollController === controller) pollController = null
  }
}

function pauseTestPolling() {
  if (testState.value !== 'polling') return
  stopPolling()
  testState.value = 'paused'
  testError.value = '已停止自动查询；服务端测试任务可能仍在运行。继续查询可读取其当前状态。'
}

function resumeTestPolling() {
  if (testState.value !== 'paused' || !activeTestId.value || !testCandidate.value || testCandidate.value.id !== selectedCandidate.value?.id) return
  testError.value = ''
  stopPolling()
  pollStartedAt = Date.now()
  testState.value = 'polling'
  void pollTest(activeTestId.value, pollGeneration)
}

async function testCandidateConfiguration(candidate: LlmConfigurationView | null = selectedCandidate.value) {
  if (!candidate || createState.value === 'loading' || testState.value === 'submitting') return
  resetTestState()
  selectedCandidateId.value = candidate.id
  testCandidate.value = { id: candidate.id, revision: candidate.revision }
  pollStartedAt = Date.now()
  activationError.value = ''
  activationNotice.value = ''
  testState.value = 'submitting'
  const generation = pollGeneration
  try {
    const accepted = await submitLlmConfigurationTest(candidate.id, createIdempotencyKey())
    if (generation !== pollGeneration) return
    if (accepted.data.status !== 'QUEUED' || !accepted.data.id) throw new Error('测试任务接收状态无效')
    activeTestId.value = accepted.data.id
    testState.value = 'polling'
    await pollTest(accepted.data.id, generation)
  } catch (reason) {
    if (generation !== pollGeneration) return
    testState.value = 'error'
    testError.value = safeRequestError(reason)
  }
}

async function activateSelectedCandidate() {
  const candidate = selectedCandidate.value
  const current = settings.value
  const etag = settingsEtag.value
  const result = testResult.value
  if (!candidate || !current || !etag || !result || !canActivateSelected.value || activationState.value === 'loading') return
  activationError.value = ''
  activationNotice.value = ''
  activationState.value = 'loading'
  try {
    const activated = await activateLlmConfiguration(candidate.id, {
      test_id: result.id,
      expected_active_revision: current.active_revision,
    }, etag, createIdempotencyKey())
    settings.value = activated.data
    settingsEtag.value = activated.etag
    settingsFresh.value = true
    createdCandidate.value = null
    resetTestState()
    selectedCandidateId.value = ''
    activationNotice.value = '候选配置已激活。'
  } catch (reason) {
    activationError.value = safeRequestError(reason)
    await loadSettings()
    if (reason instanceof ApiClientError && (reason.status === 409 || reason.status === 412)) activationNotice.value = settingsError.value ? '无法读取最新活动版本；页面保留的上次结果已标记为过期，激活已禁用。' : '已重新读取服务端当前配置。'
  } finally {
    activationState.value = 'idle'
  }
}

function configurationStatusLabel(status: LlmConfigurationView['status']) {
  return ({ CANDIDATE: '候选', TESTED: '已测试', ACTIVE: '活动', RETIRED: '已退役' })[status]
}

function testStatusLabel(status: LlmTestResult['status']) {
  return ({ QUEUED: '排队中', RUNNING: '测试中', SUCCEEDED: '测试完成', FAILED: '测试未通过' })[status]
}

function checkLabel(check: string) {
  return ({ connectivity: '连通性', streaming: '流式响应', tool_calling: '工具调用', structured_output: '结构化输出' } as Record<string, string>)[check] ?? '测试项'
}

function checkStatusLabel(status: string) {
  return ({ PASSED: '通过', FAILED: '失败', NOT_SUPPORTED: '不支持', SKIPPED: '未执行（认证失败）' } as Record<string, string>)[status] ?? '未完成'
}

onMounted(() => {
  void loadPublicSettings()
  void loadSettings()
})
onBeforeUnmount(() => {
  settingsRequest += 1
  stopPolling()
  resetForm()
})
</script>

<template>
  <div class="view settings-view">
    <header class="page-header">
      <span class="eyebrow">SETTINGS</span>
      <h1>系统设置</h1>
      <p>查看运行信息并管理 LLM 候选配置。密钥只在创建表单的组件内存中短暂保留，不会回显或持久化。</p>
    </header>

    <section class="panel settings-runtime" aria-labelledby="settings-runtime-title">
      <div class="settings-section-heading">
        <div>
          <span class="eyebrow">RUNTIME</span>
          <h2 id="settings-runtime-title">运行信息</h2>
        </div>
      </div>
      <dl class="facts">
        <div><dt>应用版本</dt><dd>{{ app.version?.application_version || '未连接' }}</dd></div>
        <div><dt>API Schema</dt><dd>{{ app.version?.api_schema_version || '未连接' }}</dd></div>
        <div><dt>认证模式</dt><dd>{{ app.accessContext?.auth_mode || '未连接' }}</dd></div>
        <template v-if="publicSettings">
          <div><dt>服务名称</dt><dd>{{ publicSettings.product_name }}</dd></div>
          <div><dt>构建标识</dt><dd>{{ publicSettings.build_id }}</dd></div>
          <div><dt>构建模块</dt><dd>{{ publicSettings.built_modules.join('、') || '无' }}</dd></div>
          <div><dt>可用模式</dt><dd>{{ publicSettings.allowed_modes.join('、') || '无' }}</dd></div>
          <div><dt>输入限制</dt><dd>{{ formatLimits(publicSettings.input_limits) }}</dd></div>
          <div><dt>导出限制</dt><dd>{{ formatLimits(publicSettings.export_limits) }}</dd></div>
        </template>
      </dl>
      <p v-if="publicSettingsLoading" class="settings-empty" role="status">正在读取公开运行设置…</p>
      <p v-else-if="publicSettingsError" class="settings-error" role="alert">公开运行设置读取失败：{{ publicSettingsError }} <button class="text-button" type="button" @click="loadPublicSettings">重试</button></p>
      <p v-else-if="!publicSettings" class="settings-empty">服务端未返回公开运行设置。</p>
    </section>

    <section class="settings-lifecycle" aria-labelledby="llm-settings-title">
      <div class="settings-section-heading settings-lifecycle-heading">
        <div>
          <span class="eyebrow">LLM CONFIGURATION</span>
          <h2 id="llm-settings-title">LLM 配置</h2>
          <p class="muted">创建不可变候选配置；测试通过后才可激活，已退役 revision 会保留。</p>
        </div>
        <div class="settings-heading-actions">
          <span class="badge loading">候选接口预览</span>
          <button class="secondary" type="button" :disabled="settingsLoading || createState === 'loading' || activationState === 'loading'" @click="loadSettings">{{ settingsLoading ? '刷新中…' : '刷新配置' }}</button>
        </div>
      </div>

        <p v-if="settingsError" class="settings-error" role="alert">{{ settingsError }} <button class="text-button" type="button" @click="loadSettings">重试读取</button></p>
      <p v-if="settingsLoading && !settings" class="settings-loading" role="status">正在读取服务端配置…</p>

      <div class="settings-summary-grid" :aria-busy="settingsLoading">
        <article class="panel settings-summary-card">
          <div class="settings-card-title"><h3>活动配置</h3><span class="badge" :class="activeConfiguration ? 'ready' : ''">{{ activeConfiguration ? 'ACTIVE' : '空' }}</span></div>
          <template v-if="activeConfiguration">
            <dl class="configuration-facts">
              <div><dt>名称</dt><dd>{{ activeConfiguration.name }}</dd></div>
              <div><dt>Model ID</dt><dd>{{ activeConfiguration.model_id }}</dd></div>
              <div><dt>Base URL</dt><dd>{{ activeConfiguration.base_url }}</dd></div>
            <div><dt>revision</dt><dd>{{ activeConfiguration.revision }} · active {{ settings?.active_revision }}</dd></div>
              <div><dt>密钥</dt><dd>{{ activeConfiguration.has_api_key ? '已配置' : '未配置' }}</dd></div>
            </dl>
          </template>
          <p v-else-if="settingsLoading && !settings" class="settings-empty">正在读取活动配置…</p>
          <p v-else class="settings-empty">服务端当前没有活动配置。</p>
        </article>

        <article class="panel settings-summary-card">
          <div class="settings-card-title"><h3>候选配置</h3><span class="badge">{{ candidateConfigurations.length }}</span></div>
          <p v-if="settingsLoading && !settings" class="settings-empty">正在读取候选配置…</p>
          <p v-else-if="candidateConfigurations.length === 0" class="settings-empty">当前没有候选配置。</p>
          <div v-else class="configuration-list">
            <article v-for="candidate in candidateConfigurations" :key="candidate.id" class="configuration-item" :class="{ selected: selectedCandidateId === candidate.id }">
              <div class="settings-card-title"><h4>{{ candidate.name }}</h4><span class="badge" :class="candidate.status === 'TESTED' ? 'ready' : ''">{{ configurationStatusLabel(candidate.status) }}</span></div>
              <p>{{ candidate.model_id }} · revision {{ candidate.revision }}</p>
              <p class="configuration-endpoint">{{ candidate.base_url }}</p>
              <div class="configuration-item-actions">
                <button class="secondary" type="button" :disabled="settingsLoading" :aria-pressed="selectedCandidateId === candidate.id" @click="selectCandidate(candidate)">{{ selectedCandidateId === candidate.id ? '已选择' : '选择' }}</button>
                <button class="secondary" type="button" :disabled="settingsLoading || testState === 'submitting' || activationState === 'loading'" @click="testCandidateConfiguration(candidate)">测试此配置</button>
              </div>
            </article>
          </div>
        </article>

        <article class="panel settings-summary-card">
          <div class="settings-card-title"><h3>历史配置</h3><span class="badge">{{ retiredConfigurations.length }}</span></div>
          <p v-if="settingsLoading && !settings" class="settings-empty">正在读取历史配置…</p>
          <p v-else-if="retiredConfigurations.length === 0" class="settings-empty">当前没有已退役配置。</p>
          <div v-else class="configuration-list">
            <article v-for="configuration in retiredConfigurations" :key="configuration.id" class="configuration-item">
              <div class="settings-card-title"><h4>{{ configuration.name }}</h4><span class="badge">{{ configurationStatusLabel(configuration.status) }}</span></div>
              <p>{{ configuration.model_id }} · revision {{ configuration.revision }}</p>
              <p class="configuration-endpoint">{{ configuration.base_url }}</p>
            </article>
          </div>
        </article>
      </div>
      <p v-if="settings && !settingsFresh" class="settings-error" role="status">配置读取未完成；当前展示的是上次读取结果，激活已禁用。</p>

      <section class="panel settings-editor" aria-labelledby="settings-editor-title">
        <div class="settings-card-title settings-editor-heading">
          <div>
            <h3 id="settings-editor-title">准备候选配置</h3>
            <p class="muted">普通字段与 API Key 仅保存在当前组件内存中；创建请求结束后会清空 API Key。</p>
          </div>
          <span class="badge" :class="createState === 'loading' ? 'loading' : ''">{{ createState === 'loading' ? '提交中' : '未提交' }}</span>
        </div>

        <form class="settings-form" autocomplete="off" @submit.prevent="createCandidate">
          <div class="settings-form-grid">
            <label class="settings-field">
              <span>配置名称</span>
              <input v-model="form.name" name="configuration-name" type="text" maxlength="120" required placeholder="例如：内部助手" autocomplete="off">
            </label>
            <label class="settings-field">
              <span>API 格式</span>
              <select name="api-format" disabled><option value="openai_chat">OpenAI Chat Completions</option></select>
            </label>
            <label class="settings-field settings-field-wide">
            <span>Base URL</span>
              <input v-model="form.base_url" name="base-url" type="url" pattern="https://.+" maxlength="1000" required placeholder="https://provider.example/v1" autocomplete="url">
              <small>请输入部署管理员批准的完整 HTTPS base endpoint（scheme/host/effective port/base path）。这里只按服务端策略校验，不展示管理员配置值。</small>
            </label>
            <label class="settings-field">
              <span>Model ID</span>
              <input v-model="form.model_id" name="model-id" type="text" maxlength="128" required placeholder="模型标识" autocomplete="off">
            </label>
            <fieldset class="settings-field settings-secret-mode settings-field-wide">
              <legend>凭据来源</legend>
              <label class="settings-choice">
                <input type="radio" name="secret-entry-mode" value="new" :checked="secretEntryMode === 'new'" @change="setSecretEntryMode('new')">
                <span>输入新 API Key</span>
              </label>
              <label class="settings-choice">
                <input type="radio" name="secret-entry-mode" value="reuse" :checked="secretEntryMode === 'reuse'" :disabled="availableSecrets.length === 0" @change="setSecretEntryMode('reuse')">
                <span>引用已有密钥</span>
              </label>
            </fieldset>
            <label class="settings-field settings-field-wide">
              <span>API Key <small>可留空；输入后不会写入 Pinia 或浏览器存储</small></span>
              <input v-model="form.api_key" name="api-key-once" type="password" autocomplete="new-password" :disabled="secretEntryMode !== 'new' || createState === 'loading'" placeholder="仅本次输入，不会回显" spellcheck="false">
            </label>
            <label class="settings-field settings-field-wide">
              <span>选择已有密钥</span>
              <select v-model="form.reuse_secret_from_configuration_id" name="reuse-secret" :disabled="secretEntryMode !== 'reuse' || availableSecrets.length === 0 || createState === 'loading'" :required="secretEntryMode === 'reuse'">
                <option value="">{{ availableSecrets.length ? '选择已配置密钥的配置' : '服务端尚无可复用密钥' }}</option>
                <option v-for="configuration in availableSecrets" :key="configuration.id" :value="configuration.id">{{ configuration.name }} · revision {{ configuration.revision }}</option>
              </select>
            </label>
            <label class="settings-field">
              <span>请求超时（秒）</span>
              <input v-model.number="form.timeout_seconds" name="timeout-seconds" type="number" min="1" max="600" required>
            </label>
            <label class="settings-field">
              <span>最大重试次数</span>
              <input v-model.number="form.max_retries" name="max-retries" type="number" min="0" max="5" required>
            </label>
            <label class="settings-field">
              <span>最大输出 Token</span>
              <input v-model.number="form.max_output_tokens" name="max-output-tokens" type="number" min="1" max="32768" required>
            </label>
            <label class="settings-field">
              <span>外部数据发送策略</span>
              <select v-model="form.external_send_policy" name="external-send-policy">
                <option value="deny_external">默认拒绝外部发送</option>
                <option value="approved_endpoint_only">仅发送到已批准端点</option>
              </select>
            </label>
          </div>

          <p v-if="createError" class="settings-error" role="alert">{{ createError }}</p>
          <p v-if="createNotice" class="settings-notice" role="status">{{ createNotice }}</p>
          <div class="settings-actions">
            <button class="secondary" type="button" :disabled="createState === 'loading'" @click="cancelDraft">清除草稿</button>
            <button class="primary" type="submit" :disabled="createState === 'loading'">{{ createState === 'loading' ? '正在创建…' : '创建候选配置' }}</button>
          </div>
        </form>
      </section>

      <section class="panel settings-test-panel" aria-labelledby="settings-test-title" :aria-busy="testState === 'submitting' || testState === 'polling' || activationState === 'loading'">
        <div class="settings-card-title settings-editor-heading">
          <div><h3 id="settings-test-title">连接测试与激活</h3><p class="muted">只有当前候选配置的测试成功，且连通性、工具调用与流式响应均通过，才可激活。</p></div>
          <span class="badge" :class="testState === 'ready' ? 'ready' : testState === 'failed' || testState === 'error' ? 'error' : testState === 'polling' || testState === 'submitting' ? 'loading' : ''">{{ selectedCandidate ? configurationStatusLabel(selectedCandidate.status) : '未选择候选' }}</span>
        </div>
        <p v-if="activationError" class="settings-error" role="alert">{{ activationError }}</p>
        <p v-if="activationNotice" class="settings-notice" role="status">{{ activationNotice }}</p>
        <p v-if="!selectedCandidate" class="settings-empty">先从服务端候选列表选择配置，或创建候选配置。</p>
        <template v-else>
          <p class="settings-selected-candidate">当前候选：{{ selectedCandidate.name }} · revision {{ selectedCandidate.revision }}</p>
          <div class="settings-test-status" role="status" aria-live="polite">
            <strong>测试状态</strong>
            <span>{{ testState === 'submitting' ? '正在提交测试任务…' : testState === 'polling' ? (testResult ? testStatusLabel(testResult.status) : '测试任务已排队') : testState === 'paused' ? '已暂停自动查询' : testState === 'ready' ? '测试完成' : testState === 'failed' ? '测试未通过' : testState === 'error' ? '测试状态读取失败' : '尚未测试' }}</span>
          </div>
          <dl v-if="testResult" class="test-checks">
            <div v-for="(status, check) in testResult.checks" :key="check"><dt>{{ checkLabel(check) }}</dt><dd :class="{ passed: status === 'PASSED', failed: status === 'FAILED' }">{{ checkStatusLabel(status) }}</dd></div>
          </dl>
          <p v-if="testError" class="settings-error" role="alert">{{ testError }}</p>
          <p v-else-if="testState === 'failed'" class="settings-error" role="alert">{{ safeTestFailure(testResult?.error) }}</p>
          <div class="settings-actions">
            <button v-if="testState === 'polling'" class="secondary" type="button" @click="pauseTestPolling">停止查询</button>
            <button v-else-if="testState === 'paused'" class="secondary" type="button" @click="resumeTestPolling">继续查询</button>
            <button v-else class="secondary" type="button" :disabled="testState === 'submitting' || activationState === 'loading'" @click="testCandidateConfiguration()">{{ testState === 'submitting' ? '正在提交测试…' : testState === 'ready' || testState === 'failed' || testState === 'error' ? '重新测试' : '测试此配置' }}</button>
            <button class="primary" type="button" :disabled="!canActivateSelected || activationState === 'loading'" @click="activateSelectedCandidate">{{ activationState === 'loading' ? '正在激活…' : '激活候选配置' }}</button>
          </div>
        </template>
      </section>
    </section>
  </div>
</template>

<style scoped>
.settings-view { display: grid; gap: 18px; min-width: 0; padding-bottom: 48px; }
.settings-view > :not(.page-header) { width: 100%; min-width: 0; margin-inline: auto; }
.settings-runtime, .settings-lifecycle, .settings-editor, .settings-test-panel { min-width: 0; }
.settings-runtime .facts { grid-template-columns: repeat(2, minmax(0, 1fr)); column-gap: 24px; }
.settings-runtime .facts > div { min-width: 0; }
.settings-section-heading { display: flex; align-items: flex-start; justify-content: space-between; gap: 14px; margin-bottom: 14px; }
.settings-section-heading h2 { margin: 4px 0 2px; color: #1f3f5b; font-size: 19px; }
.settings-section-heading p { font-size: 13px; }
.settings-lifecycle { display: grid; gap: 14px; }
.settings-lifecycle-heading { align-items: center; margin: 2px 0 0; }
.settings-heading-actions { display: flex; align-items: center; gap: 8px; flex: 0 0 auto; }
.settings-summary-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; }
.settings-summary-card { min-width: 0; padding: 16px; }
.settings-card-title { display: flex; align-items: flex-start; justify-content: space-between; gap: 10px; }
.settings-card-title h3, .settings-card-title h4 { margin: 0; color: #24445d; font-size: 15px; }
.settings-card-title h4 { font-size: 13px; overflow-wrap: anywhere; }
.settings-summary-card .badge, .settings-editor .badge, .settings-test-panel .badge { flex: 0 0 auto; }
.settings-empty, .settings-loading { min-height: 42px; margin: 12px 0 0; color: #708398; font-size: 12px; line-height: 1.6; }
.settings-loading { padding: 12px; border: 1px solid #e2ebf2; border-radius: 10px; background: #f8fbfe; }
.settings-editor, .settings-test-panel { padding: 18px; }
.settings-editor-heading { align-items: center; margin-bottom: 17px; }
.settings-editor-heading p { margin-top: 4px; font-size: 12px; }
.settings-form-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; }
.settings-field { display: grid; align-content: start; gap: 6px; min-width: 0; margin: 0; padding: 0; border: 0; color: #486277; font-size: 12px; font-weight: 700; }
.settings-field-wide { grid-column: 1 / -1; }
.settings-field input, .settings-field select { width: 100%; min-width: 0; min-height: 40px; padding: 8px 10px; border: 1px solid var(--line-strong); border-radius: 10px; background: #fff; color: var(--text); font-size: 13px; }
.settings-field input:focus, .settings-field select:focus { border-color: #7abaff; outline: 0; box-shadow: 0 0 0 3px rgba(24, 144, 255, .12); }
.settings-field input:disabled, .settings-field select:disabled { background: #f3f6f8; color: #8696a5; }
.settings-field small { color: #7b8c9b; font-size: 11px; font-weight: 400; line-height: 1.55; }
.settings-secret-mode { display: flex; flex-wrap: wrap; gap: 8px 18px; padding: 0; }
.settings-secret-mode legend { margin-bottom: 7px; padding: 0; color: #486277; font-size: 12px; font-weight: 700; }
.settings-choice { display: inline-flex; align-items: center; gap: 7px; color: #516a7e; font-size: 12px; font-weight: 500; }
.settings-choice input { accent-color: #1688e8; }
.configuration-facts, .test-checks { display: grid; gap: 0; margin: 12px 0 0; }
.configuration-facts div, .test-checks div { display: flex; justify-content: space-between; gap: 12px; padding: 8px 0; border-bottom: 1px solid #e4edf3; font-size: 12px; }
.configuration-facts div:last-child, .test-checks div:last-child { border-bottom: 0; }
.configuration-facts dt, .test-checks dt { color: #708398; }
.configuration-facts dd, .test-checks dd { max-width: 68%; margin: 0; color: #315d80; text-align: right; overflow-wrap: anywhere; }
.configuration-list { display: grid; gap: 9px; margin-top: 12px; }
.configuration-item { min-width: 0; padding: 12px; border: 1px solid #e0e9f0; border-radius: 11px; background: #fbfdff; }
.configuration-item.selected { border-color: #9bc9eb; background: #f3f9ff; }
.configuration-item p { margin: 7px 0 0; color: #708398; font-size: 11px; overflow-wrap: anywhere; }
.configuration-item .configuration-endpoint { color: #587289; }
.configuration-item-actions { display: flex; flex-wrap: wrap; gap: 7px; margin-top: 10px; }
.settings-selected-candidate { margin: 0; color: #315d80; font-size: 13px; font-weight: 700; overflow-wrap: anywhere; }
.settings-test-status { display: flex; flex-wrap: wrap; gap: 7px 12px; margin-top: 14px; padding: 12px; border: 1px solid #e4eaf0; border-radius: 11px; background: #f8fafc; color: #65798b; font-size: 12px; }
.settings-test-status strong { color: #315d80; }
.test-checks { grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 0 20px; }
.test-checks dd.passed { color: #16784b; }
.test-checks dd.failed { color: #bd4a4a; }
.settings-error { margin: 12px 0 0; color: #ad4242; font-size: 12px; line-height: 1.55; overflow-wrap: anywhere; }
.settings-notice { margin: 12px 0 0; color: #526f86; font-size: 12px; line-height: 1.55; }
.text-button { padding: 0; border: 0; background: transparent; color: #176da9; text-decoration: underline; }
.settings-actions { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 9px; margin-top: 16px; }
@media (max-width: 900px) {
  .settings-runtime .facts { grid-template-columns: minmax(0, 1fr); column-gap: 0; }
  .settings-summary-grid { grid-template-columns: 1fr; }
  .settings-empty { min-height: auto; }
}
@media (max-width: 520px) {
  .settings-view { gap: 14px; }
  .settings-section-heading, .settings-lifecycle-heading { align-items: flex-start; }
  .settings-lifecycle-heading { flex-direction: column; }
  .settings-heading-actions { width: 100%; flex-wrap: wrap; }
  .settings-summary-grid, .settings-form-grid { grid-template-columns: minmax(0, 1fr); }
  .settings-field-wide { grid-column: auto; }
  .settings-editor, .settings-test-panel { padding: 15px; }
  .settings-editor-heading { align-items: flex-start; }
  .settings-actions { display: grid; grid-template-columns: 1fr; }
  .settings-actions button { width: 100%; }
  .test-checks { grid-template-columns: 1fr; }
}
</style>
