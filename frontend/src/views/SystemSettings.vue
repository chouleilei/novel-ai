<script setup lang="ts">
import { computed, ref, watch, onMounted } from 'vue'
import { useSystemStore } from '@/stores/system'
import type { ModelConfig, ModelConnectivityResult, SystemProjectDefaults, SystemRuntimeSettings, ProviderChannel, ProviderChannelModel } from '@/types'
import ChannelManager from '@/components/ChannelManager.vue'
import ChannelForm from '@/components/ChannelForm.vue'
import {
  buildModelPayload,
  buildPromptBuilderFromWriter,
  createDefaultModels,
  DEFAULT_API_KEY_ENV,
  getApiKeySourceLabel,
  normalizeModelExtraConfig,
  normalizeModels,
  ROLE_DESCRIPTIONS,
  ROLE_LABELS,
} from '@/utils/modelConfig'

interface ModelTestState {
  loading: boolean
  result: ModelConnectivityResult | null
}

const systemStore = useSystemStore()
const loadingPage = ref(false)
const setupError = ref<string | null>(null)
const setupNotice = ref<string | null>(null)
const hydratingModels = ref(false)
const promptBuilderEnabled = ref(false)

const projectDefaults = ref<SystemProjectDefaults>({
  default_total_chapters: 60,
  default_auto_mode: true,
  default_max_retries: 5,
})
const runtimeSettings = ref<SystemRuntimeSettings>({
  review_policy: {
    overall_score_threshold: 8,
    outline_score_threshold: 8,
    instruction_score_threshold: 8,
  },
  memory_policy: {
    auto_apply_confidence_threshold: 0.75,
  },
  context_budget: {
    writer_target_input_tokens: 64000,
    writer_hard_limit_tokens: 96000,
    critic_target_input_tokens: 32000,
    critic_hard_limit_tokens: 48000,
  },
  execution: {
    llm_stage_timeout_seconds: 180,
  },
})
const modelsForm = ref<ModelConfig[]>(createDefaultModels())
const modelTestStates = ref<Record<ModelConfig['role'], ModelTestState>>({
  writer: { loading: false, result: null },
  critic: { loading: false, result: null },
  memory: { loading: false, result: null },
  prompt_builder: { loading: false, result: null },
})

const isBusy = computed(() => loadingPage.value || systemStore.loading)
const visibleModels = computed(() => modelsForm.value.filter(model => promptBuilderEnabled.value || model.role !== 'prompt_builder'))
const runtimeBudgetInvalid = computed(() => {
  return (
    runtimeSettings.value.context_budget.writer_hard_limit_tokens < runtimeSettings.value.context_budget.writer_target_input_tokens ||
    runtimeSettings.value.context_budget.critic_hard_limit_tokens < runtimeSettings.value.context_budget.critic_target_input_tokens
  )
})

// Tab state
const activeTab = ref<'project' | 'runtime' | 'models' | 'channels'>('project')

// Channels state
const channels = ref<ProviderChannel[]>([])
const loadingChannels = ref(false)
const showChannelForm = ref(false)
const channelFormMode = ref<'create' | 'edit'>('create')
const editingChannel = ref<ProviderChannel | undefined>(undefined)

// Channel models cache: channel_id -> models list
const channelModelsCache = ref<Record<string, ProviderChannelModel[]>>({})
const loadingChannelModels = ref<Record<string, boolean>>({})

const loadChannels = async () => {
  loadingChannels.value = true
  setupError.value = null
  try {
    channels.value = await systemStore.fetchChannels()
  } catch (e: any) {
    setupError.value = e?.response?.data?.detail || e?.message || '加载渠道列表失败。'
  } finally {
    loadingChannels.value = false
  }
}

const handleCreateChannel = () => {
  channelFormMode.value = 'create'
  editingChannel.value = undefined
  showChannelForm.value = true
}

const handleEditChannel = (channel: ProviderChannel) => {
  channelFormMode.value = 'edit'
  editingChannel.value = channel
  showChannelForm.value = true
}

const handleDeleteChannel = async (id: string) => {
  setupError.value = null
  setupNotice.value = null
  try {
    await systemStore.deleteChannel(id)
    channelModelsCache.value = {}
    setupNotice.value = '渠道删除成功。'
    await loadChannels()
  } catch (e: any) {
    setupError.value = e?.response?.data?.detail || e?.message || '删除渠道失败。'
  }
}

const handleChannelFormSave = async (data: any) => {
  setupError.value = null
  setupNotice.value = null
  const payload = {
    ...data,
    api_key: typeof data?.api_key === 'string' ? data.api_key.trim() : data?.api_key,
    api_key_env_var: typeof data?.api_key_env_var === 'string' ? data.api_key_env_var.trim() : data?.api_key_env_var,
  }

  if (!payload.api_key) {
    delete payload.api_key
  }
  if (!payload.api_key_env_var) {
    payload.api_key_env_var = null
  }

  try {
    if (channelFormMode.value === 'create') {
      await systemStore.createChannel(payload)
      setupNotice.value = '渠道创建成功。'
    } else if (editingChannel.value) {
      await systemStore.updateChannel(editingChannel.value.id, payload)
      setupNotice.value = '渠道更新成功。'
    }
    channelModelsCache.value = {}
    showChannelForm.value = false
    await loadChannels()
    await preloadSelectedChannelModels()
  } catch (e: any) {
    setupError.value = e?.response?.data?.detail || e?.message || '保存渠道失败。'
  }
}

const handleChannelFormCancel = () => {
  showChannelForm.value = false
}

const getModelByRole = (role: ModelConfig['role']) => modelsForm.value.find(model => model.role === role)

const setModelByRole = (nextModel: ModelConfig) => {
  const index = modelsForm.value.findIndex(model => model.role === nextModel.role)
  if (index >= 0) {
    modelsForm.value[index] = nextModel
    return
  }
  modelsForm.value.push(nextModel)
}

const syncPromptBuilderWithWriter = () => {
  if (promptBuilderEnabled.value) {
    return
  }
  const writerModel = getModelByRole('writer')
  if (!writerModel) {
    return
  }
  setModelByRole(buildPromptBuilderFromWriter(writerModel))
}

const resetModelTestStates = () => {
  modelTestStates.value = {
    writer: { loading: false, result: null },
    critic: { loading: false, result: null },
    memory: { loading: false, result: null },
    prompt_builder: { loading: false, result: null },
  }
}

const getModelTestState = (role: ModelConfig['role']) => modelTestStates.value[role]

const usesDedicatedApiKeyEnv = (model: ModelConfig) => model.provider === 'openai_compatible'

const markApiKeyForClear = (model: ModelConfig) => {
  const nextExtraConfig = normalizeModelExtraConfig(model.extra_config)
  nextExtraConfig.api_key = ''
  nextExtraConfig.clear_api_key = true
  nextExtraConfig.has_api_key = false
  model.extra_config = nextExtraConfig
}

const handleApiKeyInput = (model: ModelConfig) => {
  const nextExtraConfig = normalizeModelExtraConfig(model.extra_config)
  if (nextExtraConfig.api_key.trim()) {
    nextExtraConfig.clear_api_key = false
  }
  model.extra_config = nextExtraConfig
}

const handleChannelChange = async (model: ModelConfig, resetModelSelection = true) => {
  if (!model.channel_id) return
  
  const channel = channels.value.find(c => c.id === model.channel_id)
  if (!channel) return
  
  model.provider = channel.provider
  model.base_url = channel.base_url
  
  // Clear previous model selection when channel changes
  if (resetModelSelection) {
    model.model_name = ''
  }
  
  const nextExtraConfig = normalizeModelExtraConfig(model.extra_config)
  if (channel.api_key_env_var) {
    nextExtraConfig.api_key_env_var = channel.api_key_env_var
  } else {
    nextExtraConfig.api_key_env_var = undefined
  }
  model.extra_config = nextExtraConfig
  
  // Fetch models for this channel if not already cached
  if (!channelModelsCache.value[model.channel_id]) {
    loadingChannelModels.value[model.channel_id] = true
    try {
      const models = await systemStore.fetchChannelModels(model.channel_id)
      channelModelsCache.value[model.channel_id] = models
      
      // Auto-select default model if available
      const defaultModel = models.find(m => m.is_default) || models[0]
      if (defaultModel && !model.model_name) {
        model.model_name = defaultModel.model_name
      }
    } catch (e) {
      console.error('Failed to fetch channel models:', e)
    } finally {
      loadingChannelModels.value[model.channel_id] = false
    }
  } else {
    // Use cached models to auto-select
    const models = channelModelsCache.value[model.channel_id]
    const defaultModel = models.find(m => m.is_default) || models[0]
    if (defaultModel && !model.model_name) {
      model.model_name = defaultModel.model_name
    }
  }
}

const preloadSelectedChannelModels = async () => {
  await Promise.all(
    modelsForm.value
      .filter(model => model.channel_id)
      .map(model => handleChannelChange(model, false))
  )
}

const loadSettings = async () => {
  loadingPage.value = true
  setupError.value = null
  setupNotice.value = null
  hydratingModels.value = true
  resetModelTestStates()
  try {
    const payload = await systemStore.fetchSettings()
    projectDefaults.value = payload.project_defaults
    runtimeSettings.value = payload.runtime_settings
    modelsForm.value = normalizeModels(payload.model_configs)
    promptBuilderEnabled.value = payload.model_configs.some(model => model.role === 'prompt_builder')
    syncPromptBuilderWithWriter()
    await preloadSelectedChannelModels()
  } catch (e: any) {
    setupError.value = e?.response?.data?.detail || e?.message || '加载系统设置失败。'
  } finally {
    hydratingModels.value = false
    loadingPage.value = false
  }
}

watch(
  () => promptBuilderEnabled.value,
  (enabled, previous) => {
    if (hydratingModels.value) {
      return
    }
    if (enabled && !previous) {
      setModelByRole(buildPromptBuilderFromWriter(getModelByRole('writer')))
      return
    }
    if (!enabled) {
      syncPromptBuilderWithWriter()
    }
  },
)
watch(
  () => JSON.stringify(getModelByRole('writer') || null),
  () => {
    if (hydratingModels.value) {
      return
    }
    syncPromptBuilderWithWriter()
  },
)

onMounted(async () => {
  await loadChannels()
  await loadSettings()
})

const saveProjectDefaults = async () => {
  setupError.value = null
  setupNotice.value = null
  try {
    await systemStore.updateProjectDefaults({
      default_total_chapters: Number(projectDefaults.value.default_total_chapters || 1),
      default_auto_mode: projectDefaults.value.default_auto_mode !== false,
      default_max_retries: Number(projectDefaults.value.default_max_retries || 1),
    })
    setupNotice.value = '默认项目参数已保存，仅影响后续新建项目。'
  } catch (e: any) {
    setupError.value = e?.response?.data?.detail || e?.message || '保存默认项目参数失败。'
  }
}

const saveRuntimeSettings = async () => {
  setupError.value = null
  setupNotice.value = null
  if (runtimeBudgetInvalid.value) {
    setupError.value = '上下文预算的 hard limit 必须大于等于 target。'
    return
  }
  try {
    runtimeSettings.value = await systemStore.updateRuntimeSettings({
      review_policy: {
        overall_score_threshold: Number(runtimeSettings.value.review_policy.overall_score_threshold),
        outline_score_threshold: Number(runtimeSettings.value.review_policy.outline_score_threshold),
        instruction_score_threshold: Number(runtimeSettings.value.review_policy.instruction_score_threshold),
      },
      memory_policy: {
        auto_apply_confidence_threshold: Number(runtimeSettings.value.memory_policy.auto_apply_confidence_threshold),
      },
      context_budget: {
        writer_target_input_tokens: Number(runtimeSettings.value.context_budget.writer_target_input_tokens),
        writer_hard_limit_tokens: Number(runtimeSettings.value.context_budget.writer_hard_limit_tokens),
        critic_target_input_tokens: Number(runtimeSettings.value.context_budget.critic_target_input_tokens),
        critic_hard_limit_tokens: Number(runtimeSettings.value.context_budget.critic_hard_limit_tokens),
      },
      execution: {
        llm_stage_timeout_seconds: Number(runtimeSettings.value.execution.llm_stage_timeout_seconds),
      },
    })
    setupNotice.value = '运行策略已保存，将对后续章节运行生效；正在执行中的 stage 不会被热中断。'
  } catch (e: any) {
    setupError.value = e?.response?.data?.detail || e?.message || '保存运行策略失败。'
  }
}

const saveModels = async () => {
  setupError.value = null
  setupNotice.value = null
  try {
    const normalizedModels = normalizeModels(modelsForm.value)
      .filter(model => promptBuilderEnabled.value || model.role !== 'prompt_builder')

    const blockedModel = normalizedModels.find(model => {
      if (model.provider !== 'openai_compatible') {
        return false
      }
      const extraConfig = model.extra_config || {}
      return Boolean(extraConfig.api_key?.trim()) && extraConfig.can_save_api_key !== true
    })

    if (blockedModel) {
      setupError.value = `${ROLE_LABELS[blockedModel.role]} 当前服务端未启用密钥加密，不能直接保存系统级 API Key。请改用下方的“API Key 环境变量名”，或由部署方配置 NOVEL_AI_MODEL_SECRET_ENCRYPTION_KEY。`
      return
    }

    await systemStore.updateModelConfigs(normalizedModels.map(buildModelPayload))
    await loadSettings()
    setupNotice.value = '系统默认模型配置已保存，仅影响后续新建项目。'
  } catch (e: any) {
    setupError.value = e?.response?.data?.detail || e?.message || '保存系统默认模型失败。'
  }
}

const testModelConnectivity = async (model: ModelConfig) => {
  const state = getModelTestState(model.role)
  state.loading = true
  setupError.value = null
  setupNotice.value = null

  try {
    state.result = await systemStore.testModelConnectivity(buildModelPayload(model))
  } catch (e: any) {
    setupError.value = e?.response?.data?.detail || e?.message || '模型连通性测试失败。'
  } finally {
    state.loading = false
  }
}
</script>

<template>
  <div class="mx-auto max-w-screen-xl space-y-6">
    <div>
      <h1 class="text-2xl font-bold text-gray-900">系统设置</h1>
      <p class="mt-2 text-sm text-gray-700">维护新建项目使用的系统默认参数、系统默认模型配置，以及直接影响后续 worker 执行的全局运行策略。</p>
    </div>

    <div class="border-b border-gray-200">
      <nav class="-mb-px flex space-x-8" aria-label="Tabs">
        <button
          @click="activeTab = 'project'"
          :class="[activeTab === 'project' ? 'border-primary-500 text-primary-600' : 'border-transparent text-gray-500 hover:border-gray-300 hover:text-gray-700', 'whitespace-nowrap border-b-2 py-4 px-1 text-sm font-medium']"
        >
          默认项目参数
        </button>
        <button
          @click="activeTab = 'runtime'"
          :class="[activeTab === 'runtime' ? 'border-primary-500 text-primary-600' : 'border-transparent text-gray-500 hover:border-gray-300 hover:text-gray-700', 'whitespace-nowrap border-b-2 py-4 px-1 text-sm font-medium']"
        >
          运行策略
        </button>
        <button
          @click="activeTab = 'channels'"
          :class="[activeTab === 'channels' ? 'border-primary-500 text-primary-600' : 'border-transparent text-gray-500 hover:border-gray-300 hover:text-gray-700', 'whitespace-nowrap border-b-2 py-4 px-1 text-sm font-medium']"
        >
          渠道管理
        </button>
        <button
          @click="activeTab = 'models'"
          :class="[activeTab === 'models' ? 'border-primary-500 text-primary-600' : 'border-transparent text-gray-500 hover:border-gray-300 hover:text-gray-700', 'whitespace-nowrap border-b-2 py-4 px-1 text-sm font-medium']"
        >
          模型配置
        </button>
      </nav>
    </div>

    <div v-if="setupError" class="rounded-md bg-red-50 p-4 text-sm text-red-700">
      {{ setupError }}
    </div>
    <div v-if="setupNotice" class="rounded-md bg-green-50 p-4 text-sm text-green-700">
      {{ setupNotice }}
    </div>

    <div v-if="activeTab === 'project'" class="card">
      <div class="card-header">
        <h2 class="text-lg font-semibold text-gray-900">默认项目参数</h2>
        <p class="mt-1 text-sm text-gray-500">仅影响后续新建项目的初始表单值。</p>
      </div>
      <div class="card-body space-y-6">
        <div class="grid grid-cols-1 gap-6 md:grid-cols-3">
          <div>
            <label class="block text-sm font-medium text-gray-700">默认总章节数</label>
            <input v-model.number="projectDefaults.default_total_chapters" type="number" min="1" class="input-field mt-1" />
          </div>
          <div>
            <label class="block text-sm font-medium text-gray-700">默认最大重试次数</label>
            <input v-model.number="projectDefaults.default_max_retries" type="number" min="1" max="20" class="input-field mt-1" />
          </div>
          <div class="flex items-end">
            <label class="inline-flex items-center gap-2">
              <input v-model="projectDefaults.default_auto_mode" type="checkbox" class="h-4 w-4 rounded border-gray-300 text-primary-600" />
              <span class="text-sm text-gray-900">默认启用自动模式</span>
            </label>
          </div>
        </div>
      </div>
      <div class="card-footer flex justify-end">
        <button class="btn btn-primary" :disabled="isBusy" @click="saveProjectDefaults">保存默认项目参数</button>
      </div>
    </div>

    <div v-if="activeTab === 'runtime'" class="card">
      <div class="card-header">
        <h2 class="text-lg font-semibold text-gray-900">运行策略</h2>
        <p class="mt-1 text-sm text-gray-500">不回填项目表单，直接影响后续章节运行；保存后对新进入的章节任务生效。</p>
      </div>
      <div class="card-body space-y-6">
        <div class="grid gap-6 xl:grid-cols-2">
          <div class="rounded-2xl border border-[var(--color-border)] bg-white px-4 py-4 space-y-4">
            <div>
              <h3 class="text-base font-semibold text-gray-900">评审通过阈值</h3>
              <p class="mt-1 text-xs text-gray-500">blocking_issues 仍然会直接阻断通过。</p>
            </div>
            <div class="grid gap-4 md:grid-cols-3">
              <div>
                <label class="block text-sm font-medium text-gray-700">overall 最低分</label>
                <input v-model.number="runtimeSettings.review_policy.overall_score_threshold" type="number" min="0" max="10" step="0.1" class="input-field mt-1" />
              </div>
              <div>
                <label class="block text-sm font-medium text-gray-700">大纲一致性最低分</label>
                <input v-model.number="runtimeSettings.review_policy.outline_score_threshold" type="number" min="0" max="10" step="0.1" class="input-field mt-1" />
              </div>
              <div>
                <label class="block text-sm font-medium text-gray-700">指令一致性最低分</label>
                <input v-model.number="runtimeSettings.review_policy.instruction_score_threshold" type="number" min="0" max="10" step="0.1" class="input-field mt-1" />
              </div>
            </div>
          </div>

          <div class="rounded-2xl border border-[var(--color-border)] bg-white px-4 py-4 space-y-4">
            <div>
              <h3 class="text-base font-semibold text-gray-900">Memory 审核阈值</h3>
              <p class="mt-1 text-xs text-gray-500">高风险角色/世界观修订仍然强制人工审核。</p>
            </div>
            <div>
              <label class="block text-sm font-medium text-gray-700">自动应用最低 confidence</label>
              <input v-model.number="runtimeSettings.memory_policy.auto_apply_confidence_threshold" type="number" min="0" max="1" step="0.01" class="input-field mt-1" />
            </div>
          </div>
        </div>

        <div class="rounded-2xl border border-[var(--color-border)] bg-white px-4 py-4 space-y-4">
          <div>
            <h3 class="text-base font-semibold text-gray-900">上下文预算</h3>
            <p class="mt-1 text-xs text-gray-500">仅开放 writer / critic 顶层 target 和 hard limit；逐层裁剪策略仍使用系统内置规则。</p>
          </div>
          <div class="grid gap-4 lg:grid-cols-2">
            <div class="rounded-2xl border border-[var(--color-border)] bg-stone-50 px-4 py-4">
              <h4 class="text-sm font-semibold text-gray-900">Writer</h4>
              <div class="mt-3 grid gap-4 md:grid-cols-2">
                <div>
                  <label class="block text-sm font-medium text-gray-700">target tokens</label>
                  <input v-model.number="runtimeSettings.context_budget.writer_target_input_tokens" type="number" min="1" class="input-field mt-1" />
                </div>
                <div>
                  <label class="block text-sm font-medium text-gray-700">hard limit tokens</label>
                  <input v-model.number="runtimeSettings.context_budget.writer_hard_limit_tokens" type="number" min="1" class="input-field mt-1" />
                </div>
              </div>
            </div>
            <div class="rounded-2xl border border-[var(--color-border)] bg-stone-50 px-4 py-4">
              <h4 class="text-sm font-semibold text-gray-900">Critic</h4>
              <div class="mt-3 grid gap-4 md:grid-cols-2">
                <div>
                  <label class="block text-sm font-medium text-gray-700">target tokens</label>
                  <input v-model.number="runtimeSettings.context_budget.critic_target_input_tokens" type="number" min="1" class="input-field mt-1" />
                </div>
                <div>
                  <label class="block text-sm font-medium text-gray-700">hard limit tokens</label>
                  <input v-model.number="runtimeSettings.context_budget.critic_hard_limit_tokens" type="number" min="1" class="input-field mt-1" />
                </div>
              </div>
            </div>
          </div>
          <div v-if="runtimeBudgetInvalid" class="rounded-md bg-amber-50 px-3 py-2 text-xs text-amber-700">
            writer / critic 的 hard limit 都必须大于等于对应的 target。
          </div>
        </div>

        <div class="rounded-2xl border border-[var(--color-border)] bg-white px-4 py-4 space-y-4">
          <div>
            <h3 class="text-base font-semibold text-gray-900">执行超时</h3>
            <p class="mt-1 text-xs text-gray-500">单阶段超时，影响 prompt、context、writer、critic、memory 等阶段的超时判断。</p>
          </div>
          <div class="max-w-sm">
            <label class="block text-sm font-medium text-gray-700">单阶段超时秒数</label>
            <input v-model.number="runtimeSettings.execution.llm_stage_timeout_seconds" type="number" min="0.1" step="0.1" class="input-field mt-1" />
          </div>
        </div>
      </div>
      <div class="card-footer flex justify-end">
        <button class="btn btn-primary" :disabled="isBusy || runtimeBudgetInvalid" @click="saveRuntimeSettings">保存运行策略</button>
      </div>
    </div>

    <template v-if="activeTab === 'channels'">
      <div class="space-y-6">
        <ChannelManager
          v-if="!showChannelForm"
          :channels="channels"
          :loading="loadingChannels"
          @create="handleCreateChannel"
          @edit="handleEditChannel"
          @delete="handleDeleteChannel"
        />
        <ChannelForm
          v-else
          :channel="editingChannel"
          :loading="isBusy"
          @submit="handleChannelFormSave"
          @cancel="handleChannelFormCancel"
        />
      </div>
    </template>

    <template v-if="activeTab === 'models'">
      <div class="rounded-lg border border-blue-200 bg-blue-50 px-4 py-3 text-sm text-blue-800">
        <div class="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
          <div>
            <p class="font-medium">自定义提示词生成模型</p>
            <p class="mt-1 text-xs text-blue-700">默认情况下，prompt_builder 会跟随 writer。只有当你想为新项目单独指定 prompt_builder 默认配置时，才需要开启这里。</p>
          </div>
          <label class="inline-flex items-center gap-2">
            <input v-model="promptBuilderEnabled" type="checkbox" class="h-4 w-4 rounded border-blue-300 text-primary-600" />
            <span class="text-xs font-medium">自定义</span>
          </label>
        </div>
      </div>

      <div v-for="model in visibleModels" :key="model.role" class="card overflow-hidden">
      <div class="card-header bg-[linear-gradient(135deg,rgba(15,118,110,0.08),rgba(180,83,9,0.08))]">
        <div class="flex flex-col gap-4 xl:flex-row xl:items-start xl:justify-between">
          <div>
            <div class="flex items-center gap-2">
              <h3 class="text-lg font-semibold text-gray-900">{{ ROLE_LABELS[model.role] }}</h3>
              <span class="badge badge-secondary">{{ model.role }}</span>
              <span v-if="model.role === 'prompt_builder'" class="rounded bg-blue-100 px-2 py-0.5 text-xs font-medium text-blue-700">自定义</span>
            </div>
            <p class="mt-2 max-w-2xl text-sm text-gray-600">{{ ROLE_DESCRIPTIONS[model.role] }}</p>
          </div>
          <button type="button" class="btn btn-sm btn-secondary" :disabled="isBusy || getModelTestState(model.role).loading" @click="testModelConnectivity(model)">
            {{ getModelTestState(model.role).loading ? '测试中...' : '测试连通性' }}
          </button>
        </div>
      </div>
      <div class="card-body space-y-6">
        <div class="grid gap-4 md:grid-cols-4">
          <div class="rounded-2xl border border-[var(--color-border)] bg-stone-50 px-4 py-4 md:col-span-4">
            <p class="text-xs uppercase tracking-wide text-gray-400">渠道</p>
            <select v-model="model.channel_id" class="input-field mt-3" @change="handleChannelChange(model)">
              <option value="">无（手动配置）</option>
              <option v-for="channel in channels.filter(c => c.is_enabled)" :key="channel.id" :value="channel.id">
                {{ channel.name }}
              </option>
            </select>
          </div>
          <div class="rounded-2xl border border-[var(--color-border)] bg-stone-50 px-4 py-4 md:col-span-1">
            <p class="text-xs uppercase tracking-wide text-gray-400">提供方</p>
            <select v-model="model.provider" class="input-field mt-3">
              <option value="mock">模拟模型（本地测试）</option>
              <option value="openai_compatible">OpenAI 兼容接口</option>
            </select>
          </div>
          <div class="rounded-2xl border border-[var(--color-border)] bg-stone-50 px-4 py-4 md:col-span-3">
            <p class="text-xs uppercase tracking-wide text-gray-400">模型名称</p>
            <!-- Show dropdown if channel has models, otherwise show text input -->
            <select 
              v-if="model.channel_id && channelModelsCache[model.channel_id]?.length > 0" 
              v-model="model.model_name" 
              class="input-field mt-3"
            >
              <option value="">请选择模型</option>
              <option 
                v-for="m in channelModelsCache[model.channel_id]" 
                :key="m.model_name" 
                :value="m.model_name"
              >
                {{ m.display_name || m.model_name }}
                <span v-if="m.is_default" class="text-xs text-gray-500">(默认)</span>
              </option>
            </select>
              <input 
                v-else 
                v-model="model.model_name" 
                type="text" 
                class="input-field mt-3" 
                :placeholder="model.channel_id && loadingChannelModels[model.channel_id] ? '加载模型列表...' : '请输入模型名称'"
                :disabled="!!(model.channel_id && loadingChannelModels[model.channel_id])"
              />
              <p v-if="model.channel_id && loadingChannelModels[model.channel_id]" class="mt-2 text-xs text-blue-600">
                正在加载模型列表...
              </p>
              <p v-else-if="model.channel_id && !channelModelsCache[model.channel_id]?.length" class="mt-2 text-xs text-amber-600">
                该渠道暂无模型列表，请先到渠道管理中拉取模型
              </p>
          </div>
        </div>

        <div class="rounded-2xl border border-[var(--color-border)] bg-white px-4 py-4">
          <p class="text-xs uppercase tracking-wide text-gray-400">基础地址</p>
          <input v-model="model.base_url" type="text" class="input-field mt-3" />
        </div>

        <div class="grid gap-4 md:grid-cols-[minmax(0,1.2fr)_minmax(0,0.8fr)]">
          <div v-if="usesDedicatedApiKeyEnv(model)" class="space-y-4 rounded-2xl border border-[var(--color-border)] bg-white px-4 py-4">
            <div>
              <p class="text-xs uppercase tracking-wide text-gray-400">系统级默认 API Key</p>
              <input
                v-model="model.extra_config!.api_key"
                type="password"
                autocomplete="new-password"
                class="input-field mt-3"
                :disabled="model.extra_config?.can_save_api_key === false"
                :placeholder="model.extra_config?.can_save_api_key === false ? '当前服务端未启用密钥加密，请改用下方环境变量名' : '留空则保留当前已保存的 Key'"
                @input="handleApiKeyInput(model)"
              />
              <div class="mt-2 flex flex-wrap items-center gap-3 text-xs">
                <span v-if="model.extra_config?.clear_api_key" class="text-amber-700">保存后将清空已存 API Key</span>
                <span v-else-if="model.extra_config?.api_key?.trim()" class="text-blue-700">保存后将更新系统级 API Key</span>
                <span v-else-if="model.extra_config?.has_api_key" class="text-emerald-700">已设置系统级 API Key</span>
                <span v-else-if="model.extra_config?.can_save_api_key === false" class="text-amber-700">当前服务端不允许直接保存系统级 API Key</span>
                <span v-else class="text-gray-500">当前未设置系统级 API Key</span>
                <button
                  v-if="model.extra_config?.has_api_key || model.extra_config?.clear_api_key"
                  type="button"
                  class="text-red-600 hover:text-red-700"
                  @click="markApiKeyForClear(model)"
                >
                  清空已存 API Key
                </button>
              </div>
            </div>

            <div>
              <p class="text-xs uppercase tracking-wide text-gray-400">API Key 环境变量名</p>
              <input v-model="model.extra_config!.api_key_env_var" type="text" class="input-field mt-3" :placeholder="DEFAULT_API_KEY_ENV[model.role]" />
              <p class="mt-2 text-xs text-gray-500">后续新建项目若未覆盖 API Key，将优先使用这里指定的环境变量名。</p>
            </div>

            <label v-if="model.role === 'critic'" class="flex items-start gap-3 rounded-xl border border-emerald-200 bg-emerald-50 px-3 py-3 text-sm text-emerald-900">
              <input v-model="model.extra_config!.supports_json_schema_output" type="checkbox" class="mt-0.5 h-4 w-4 rounded border-emerald-300 text-primary-600" />
              <span>
                <span class="font-medium">启用结构化 JSON 输出</span>
                <span class="mt-1 block text-xs text-emerald-800">推荐开启。后续新建项目的监督模型会优先按 schema 返回结构化结果；若上游不兼容，后端会自动按单次请求降级。</span>
              </span>
            </label>
          </div>
          <div v-else class="rounded-2xl border border-dashed border-[var(--color-border)] bg-stone-50 px-4 py-4 text-xs text-gray-500">
            模拟模型不需要 API Key。若切换到 OpenAI 兼容接口，可直接填写系统级默认 API Key，或配置类似 <code>{{ DEFAULT_API_KEY_ENV[model.role] }}</code> 的环境变量名。
          </div>

          <div class="rounded-2xl border border-[var(--color-border)] bg-stone-50 px-4 py-4 space-y-4">
            <div>
              <p class="text-xs uppercase tracking-wide text-gray-400">温度策略</p>
              <input v-model.number="model.temperature" type="number" step="0.1" class="input-field mt-3" :placeholder="model.role === 'writer' ? '1.2' : '保持模型默认值'" />
            </div>
            <div>
              <p class="text-xs uppercase tracking-wide text-gray-400">最大 Tokens</p>
              <input v-model.number="model.max_tokens" type="number" min="1" class="input-field mt-3" placeholder="留空则保持模型服务默认策略" />
            </div>
          </div>
        </div>

        <div
          v-if="getModelTestState(model.role).result"
          class="rounded-2xl border px-4 py-4"
          :class="getModelTestState(model.role).result?.success ? 'border-emerald-200 bg-emerald-50' : 'border-red-200 bg-red-50'"
        >
          <div class="flex flex-wrap items-center justify-between gap-3">
            <div>
              <p class="text-sm font-semibold" :class="getModelTestState(model.role).result?.success ? 'text-emerald-900' : 'text-red-900'">
                {{ getModelTestState(model.role).result?.success ? '连通性测试通过' : '连通性测试失败' }}
              </p>
              <p class="mt-1 text-sm" :class="getModelTestState(model.role).result?.success ? 'text-emerald-800' : 'text-red-800'">
                {{ getModelTestState(model.role).result?.message }}
              </p>
            </div>
            <div class="text-right text-xs" :class="getModelTestState(model.role).result?.success ? 'text-emerald-700' : 'text-red-700'">
              <p>耗时 {{ getModelTestState(model.role).result?.latency_ms }} ms</p>
              <p class="mt-1">Key 来源：{{ getApiKeySourceLabel(getModelTestState(model.role).result?.api_key_source) }}</p>
            </div>
          </div>
          <div v-if="getModelTestState(model.role).result?.output_preview" class="mt-3 rounded-xl bg-white/80 px-3 py-3 text-xs text-gray-600">
            <p class="font-medium text-gray-700">返回预览</p>
            <p class="mt-2 break-words whitespace-pre-wrap">{{ getModelTestState(model.role).result?.output_preview }}</p>
          </div>
            <div v-if="!getModelTestState(model.role).result?.success && getModelTestState(model.role).result?.raw_preview" class="mt-3 rounded-xl bg-white/80 px-3 py-3 text-xs text-gray-600">
              <p class="font-medium text-gray-700">原始返回片段</p>
              <p class="mt-2 break-words whitespace-pre-wrap">{{ getModelTestState(model.role).result?.raw_preview }}</p>
            </div>
          </div>
        </div>
      </div>

      <div class="flex justify-end">
        <button class="btn btn-primary" :disabled="isBusy" @click="saveModels">保存系统默认模型</button>
      </div>
    </template>
  </div>
</template>
