<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import api from '@/api'
import { useProjectStore } from '@/stores/project'
import type { Project, ModelConfig, ChapterOutline, ModelConnectivityResult, ProviderChannel, ProviderChannelModel } from '@/types'
import { buildModelPayload, buildPromptBuilderFromWriter, createDefaultModels, DEFAULT_API_KEY_ENV, getApiKeySourceLabel, normalizeModelExtraConfig, normalizeModels, ROLE_DESCRIPTIONS, ROLE_LABELS } from '@/utils/modelConfig'
import { useSystemStore } from '@/stores/system'

const route = useRoute()
const router = useRouter()
const projectStore = useProjectStore()
const systemStore = useSystemStore()

const isNew = computed(() => route.name === 'project-new')
const projectId = computed(() => route.params.id as string)

const activeTab = ref('basic')
const loadingProjectResources = ref(false)
const setupError = ref<string | null>(null)
const setupNotice = ref<string | null>(null)
const promptBuilderEnabled = ref(false)
const generatingGlobalPrompt = ref(false)
const generatingOutlineDraft = ref(false)
const hydratingModels = ref(false)
const channels = ref<ProviderChannel[]>([])

// Channel models cache: channel_id -> models list
const channelModelsCache = ref<Record<string, ProviderChannelModel[]>>({})
const loadingChannelModels = ref<Record<string, boolean>>({})
const advancedParamsOpen = ref<Record<ModelConfig['role'], boolean>>({
  writer: false,
  critic: false,
  memory: false,
  prompt_builder: false,
})

interface ProjectAutofillResponse {
  title?: string
  genre?: string
  style?: string
  global_prompt: string
}

interface OutlineDraftResponse {
  outlines: ChapterOutline[]
}

interface ModelTestState {
  loading: boolean
  result: ModelConnectivityResult | null
}

const createEmptyProjectForm = (): Partial<Project> => ({
  title: '',
  genre: '',
  style: '',
  global_prompt: '',
  total_chapters: 10,
  auto_mode: true,
  auto_accept_critic_failed: false,
  auto_accept_on_max_retries: false,
  hard_review_gates_enabled: true,
  max_retries: 5,
  writer_streaming_enabled: false,
  generation_mode: 'standard',
  rush_previous_chapter_count: 10,
})

const createEmptyModelTestStates = (): Record<ModelConfig['role'], ModelTestState> => ({
  writer: { loading: false, result: null },
  critic: { loading: false, result: null },
  memory: { loading: false, result: null },
  prompt_builder: { loading: false, result: null },
})

const projectForm = ref<Partial<Project>>(createEmptyProjectForm())
const modelsForm = ref<ModelConfig[]>(createDefaultModels())
const modelTestStates = ref<Record<ModelConfig['role'], ModelTestState>>(createEmptyModelTestStates())

const outlinesText = ref('')
const outlineParseWarning = ref<string | null>(null)

const isBusy = computed(() => projectStore.loading || loadingProjectResources.value)
const visibleModels = computed(() => {
  return modelsForm.value.filter(model => promptBuilderEnabled.value || model.role !== 'prompt_builder')
})

const getModelByRole = (role: ModelConfig['role']) => {
  return modelsForm.value.find(model => model.role === role)
}

const hasLoadedChannelModels = (channelId: string) => {
  return Object.prototype.hasOwnProperty.call(channelModelsCache.value, channelId)
}

const setModelByRole = (nextModel: ModelConfig) => {
  const index = modelsForm.value.findIndex(model => model.role === nextModel.role)
  if (index >= 0) {
    modelsForm.value[index] = nextModel
    return
  }
  modelsForm.value.push(nextModel)
}

const getCustomParamEntries = (model: ModelConfig): [string, any][] => {
  const params = model.extra_config?.custom_params
  if (!params || typeof params !== 'object') return []
  return Object.entries(params)
}

const parseParamValue = (raw: string): any => {
  const trimmed = raw.trim()
  if (trimmed === '') return ''
  if (trimmed === 'true') return true
  if (trimmed === 'false') return false
  const num = Number(trimmed)
  if (!isNaN(num) && trimmed !== '') return num
  if ((trimmed.startsWith('{') && trimmed.endsWith('}')) || (trimmed.startsWith('[') && trimmed.endsWith(']'))) {
    try {
      return JSON.parse(trimmed)
    } catch {
      return trimmed
    }
  }
  return trimmed
}

const addCustomParam = (model: ModelConfig) => {
  if (!model.extra_config) return
  if (!model.extra_config.custom_params || typeof model.extra_config.custom_params !== 'object') {
    model.extra_config.custom_params = {}
  }
  let newKey = 'new_param'
  let i = 1
  while (newKey in model.extra_config.custom_params) {
    newKey = `new_param_${i++}`
  }
  model.extra_config.custom_params[newKey] = ''
}

const removeCustomParam = (model: ModelConfig, key: string) => {
  if (!model.extra_config?.custom_params) return
  delete model.extra_config.custom_params[key]
  if (Object.keys(model.extra_config.custom_params).length === 0) {
    model.extra_config.custom_params = null
  }
}

const renameCustomParam = (model: ModelConfig, oldKey: string, newKey: string) => {
  if (!model.extra_config?.custom_params) return
  const trimmed = newKey.trim()
  if (!trimmed || trimmed === oldKey) return
  if (trimmed in model.extra_config.custom_params) return
  model.extra_config.custom_params[trimmed] = model.extra_config.custom_params[oldKey]
  delete model.extra_config.custom_params[oldKey]
}

const setCustomParamValue = (model: ModelConfig, key: string, rawValue: string) => {
  if (!model.extra_config?.custom_params) return
  model.extra_config.custom_params[key] = parseParamValue(rawValue)
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

const syncTabFromRoute = () => {
  const routeTab = typeof route.query.tab === 'string' ? route.query.tab : 'basic'
  activeTab.value = isNew.value && routeTab !== 'basic' ? 'basic' : routeTab
}

const setActiveTab = async (tab: string) => {
  activeTab.value = tab
  await router.replace({
    query: {
      ...route.query,
      tab,
    },
  })
}

const buildProjectPayload = () => {
  const generationMode: Project['generation_mode'] = projectForm.value.generation_mode === 'rush' ? 'rush' : 'standard'
  return {
    title: projectForm.value.title?.trim() || '',
    genre: projectForm.value.genre?.trim() || null,
    style: projectForm.value.style?.trim() || null,
    global_prompt: projectForm.value.global_prompt?.trim() || '',
    total_chapters: Number(projectForm.value.total_chapters || 1),
    auto_mode: projectForm.value.auto_mode !== false,
    auto_accept_critic_failed: projectForm.value.auto_accept_critic_failed === true,
    auto_accept_on_max_retries:
      projectForm.value.auto_accept_critic_failed === true
        ? false
        : projectForm.value.auto_accept_on_max_retries === true,
    hard_review_gates_enabled: projectForm.value.hard_review_gates_enabled !== false,
    max_retries: Number(projectForm.value.max_retries || 1),
    writer_streaming_enabled: projectForm.value.writer_streaming_enabled === true,
    generation_mode: generationMode,
    rush_previous_chapter_count: Math.max(0, Math.min(50, Number(projectForm.value.rush_previous_chapter_count ?? 10))),
  }
}

const usesDedicatedApiKeyEnv = (model: ModelConfig) => {
  return model.provider === 'openai_compatible'
}

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

const resetModelTestStates = () => {
  modelTestStates.value = createEmptyModelTestStates()
}

const getModelTestState = (role: ModelConfig['role']) => {
  return modelTestStates.value[role]
}

const testModelConnectivity = async (model: ModelConfig) => {
  if (isNew.value) {
    return
  }

  const state = getModelTestState(model.role)
  state.loading = true
  setupError.value = null
  setupNotice.value = null

  try {
    const result = await projectStore.testModelConnectivity(projectId.value, buildModelPayload(model))
    state.result = result
  } catch (e: any) {
    setupError.value = e?.response?.data?.detail || e?.message || '模型连通性测试失败。'
  } finally {
    state.loading = false
  }
}

const serializeOutlines = (outlines: ChapterOutline[]) => {
  return outlines
    .sort((a, b) => a.chapter_number - b.chapter_number)
    .map(item => item.outline_text)
    .join('\n\n')
}

const CHAPTER_HEADING_PATTERN = /^(?:\*{1,2}\s*)?(?:[#＃]\s*)?(?:[【\[(（]\s*)?第\s*[0-9零一二三四五六七八九十百千两]+\s*(?:章|回)(?:\s*[：:、.．—-]\s*|\s+)?(?:.*?)(?:\s*[】\])）])?(?:\s*\*{1,2})?\s*$/u
const CHAPTER_HEADING_MAX_LENGTH = 80
const PROSE_PUNCTUATION_PATTERN = /[。！？!?；;]/u
const MAX_PARAGRAPH_FALLBACK_BLOCKS = 200

const SEPARATOR_BLOCK_PATTERN = /^(?:[-—_=*#]{3,}|\*{1,2}\s*[-—_=#]{1,}\s*\*{1,2})$/u

const isSeparatorBlock = (block: string) => {
  const normalizedBlock = block.trim()
  if (!normalizedBlock) {
    return true
  }

  return SEPARATOR_BLOCK_PATTERN.test(normalizedBlock)
}

const isLikelyChapterHeading = (rawLine: string) => {
  const line = rawLine.trim()
  if (!line || isSeparatorBlock(line)) {
    return false
  }
  if (line.length > CHAPTER_HEADING_MAX_LENGTH) {
    return false
  }
  if (PROSE_PUNCTUATION_PATTERN.test(line)) {
    return false
  }
  return CHAPTER_HEADING_PATTERN.test(line)
}

const getParagraphFallbackBlocks = (normalizedText: string) => {
  return normalizedText
    .split(/\n\s*\n+/)
    .map(block => block.trim())
    .filter(block => Boolean(block) && !isSeparatorBlock(block))
}

const isSuspiciousOutlineCount = (count: number) => {
  const expected = Number(projectForm.value.total_chapters || 0)
  if (!expected || expected < 20) {
    return false
  }
  return count > Math.max(expected * 2, expected + 50)
}

const buildOutlineParseWarning = (rawText: string, parsedOutlines: ChapterOutline[]) => {
  const normalizedText = rawText.replace(/\r\n/g, '\n').trim()
  if (!normalizedText) {
    return null
  }

  if (parsedOutlines.length === 0) {
    const paragraphBlockCount = getParagraphFallbackBlocks(normalizedText).length
    if (paragraphBlockCount > MAX_PARAGRAPH_FALLBACK_BLOCKS) {
      return `未识别到可靠的章节标题，且文本按空行会拆成 ${paragraphBlockCount} 段。请使用“第1章：标题”格式粘贴章节大纲，避免直接粘贴正文。`
    }
    return '未解析到可导入的章节大纲。'
  }

  if (isSuspiciousOutlineCount(parsedOutlines.length)) {
    return `当前文本被解析为 ${parsedOutlines.length} 章，明显超过当前项目章节数 ${projectForm.value.total_chapters}。请检查是否粘贴了正文或章内小节标题。`
  }

  return null
}

const parseOutlineBlocks = (rawText: string): ChapterOutline[] => {
  const normalizedText = rawText.replace(/\r\n/g, '\n').trim()
  if (!normalizedText) {
    return []
  }

  // 策略 1：先尝试基于标题行进行全局分割（最鲁棒的方式）
  const lines = normalizedText.split('\n')
  const headingIndexes = lines
    .map((line, index) => ({ line: line.trim(), index }))
    .filter(item => isLikelyChapterHeading(item.line))
    .map(item => item.index)

  if (headingIndexes.length > 0) {
    const outlines = headingIndexes.map((startIndex, index) => {
      const endIndex = headingIndexes[index + 1] ?? lines.length
      const block = lines.slice(startIndex, endIndex).join('\n').trim()
      return {
        chapter_number: index + 1,
        outline_text: block,
      }
    }).filter(item => item.outline_text)

    // 如果识别出的章节数较多（比如超过 1 个），通常说明这种解析方式是正确的
    if (outlines.length > 0) {
      return outlines
    }
  }

  // 策略 2：如果没有明显的标题行，尝试按段落分割（保持向后兼容）
  const paragraphBlocks = getParagraphFallbackBlocks(normalizedText)
  if (paragraphBlocks.length > MAX_PARAGRAPH_FALLBACK_BLOCKS) {
    return []
  }

  return paragraphBlocks
    .filter(Boolean)
    .map((outline_text, index) => ({
      chapter_number: index + 1,
      outline_text,
    }))
}

const syncTotalChaptersFromOutlines = () => {
  const parsedOutlines = parseOutlineBlocks(outlinesText.value)
  outlineParseWarning.value = buildOutlineParseWarning(outlinesText.value, parsedOutlines)
  if (outlineParseWarning.value) {
    return parsedOutlines
  }
  if (parsedOutlines.length > 0) {
    projectForm.value.total_chapters = parsedOutlines.length
  }
  return parsedOutlines
}

const outlinePreviewCount = computed(() => parseOutlineBlocks(outlinesText.value).length)

watch(outlinesText, () => {
  if (outlineParseWarning.value) {
    const parsedOutlines = parseOutlineBlocks(outlinesText.value)
    outlineParseWarning.value = buildOutlineParseWarning(outlinesText.value, parsedOutlines)
  }
})

const loadDefaultModels = async () => {
  resetModelTestStates()
  try {
    const defaults = await api.get('/projects/defaults/model-configs') as ModelConfig[]
    if (Array.isArray(defaults) && defaults.length > 0) {
      modelsForm.value = normalizeModels(defaults)
      promptBuilderEnabled.value = defaults.some(model => model.role === 'prompt_builder')
      syncPromptBuilderWithWriter()
      return
    }
  } catch {
    // Fall back to bundled defaults when the backend default-model endpoint is unavailable.
  }

  modelsForm.value = createDefaultModels()
  promptBuilderEnabled.value = false
  syncPromptBuilderWithWriter()
}

const loadProjectResources = async () => {
  syncTabFromRoute()
  setupError.value = null
  setupNotice.value = null
  hydratingModels.value = true
  resetModelTestStates()

  try {
    channels.value = await systemStore.fetchChannels()
  } catch (e) {
    console.error('Failed to load channels', e)
  }

  if (isNew.value) {
    try {
      const defaults = await projectStore.fetchProjectDefaults()
      projectForm.value = {
        ...createEmptyProjectForm(),
        ...defaults,
      }
    } catch {
      projectForm.value = createEmptyProjectForm()
    }
    await loadDefaultModels()
    outlinesText.value = ''
    hydratingModels.value = false
    return
  }

  loadingProjectResources.value = true
  try {
    const [_, models, outlines] = await Promise.all([
      projectStore.fetchProject(projectId.value),
      projectStore.fetchModels(projectId.value),
      projectStore.fetchOutlines(projectId.value),
    ])

    projectForm.value = {
      ...createEmptyProjectForm(),
      ...projectStore.currentProject,
    }
    modelsForm.value = normalizeModels(models)
    promptBuilderEnabled.value = models.some(model => model.role === 'prompt_builder')
    syncPromptBuilderWithWriter()
    await Promise.all(
      modelsForm.value
        .filter(model => model.channel_id)
        .map(model => handleChannelChange(model, false))
    )
    outlinesText.value = serializeOutlines(outlines)
  } catch (e: any) {
    setupError.value = e?.response?.data?.detail || e?.message || 'Failed to load project configuration.'
  } finally {
    loadingProjectResources.value = false
    hydratingModels.value = false
  }
}

const handleChannelChange = async (model: ModelConfig, resetModelSelection = true) => {
  if (!model.channel_id) return
  
  const channel = channels.value.find((c: ProviderChannel) => c.id === model.channel_id)
  if (!channel) return
  
  model.provider = channel.provider
  model.base_url = channel.base_url
  
  // Clear previous model selection only for user-initiated channel changes
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

watch(() => route.query.tab, syncTabFromRoute, { immediate: true })
watch(() => [route.name, route.params.id], () => {
  void loadProjectResources()
}, { immediate: true })
watch(
  () => projectForm.value.auto_accept_critic_failed,
  enabled => {
    if (enabled) {
      projectForm.value.auto_accept_on_max_retries = false
    }
  },
)
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
watch(outlinesText, () => {
  if (hydratingModels.value) {
    return
  }
  syncTotalChaptersFromOutlines()
})

const saveBasicInfo = async () => {
  setupError.value = null
  setupNotice.value = null
  try {
    const payload = buildProjectPayload()
    if (isNew.value) {
      const newProject = await projectStore.createProject(payload)
      await router.push({
        path: `/projects/${newProject.id}`,
        query: { tab: 'models' },
      })
      return
    }

    await projectStore.updateProject(projectId.value, payload)
    await setActiveTab('models')
  } catch (e: any) {
    setupError.value = e?.response?.data?.detail || e?.message || 'Failed to save project.'
  }
}

const saveModels = async () => {
  if (isNew.value) return

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
      setupError.value = `${ROLE_LABELS[blockedModel.role]} 当前服务端未启用项目级密钥加密，不能直接保存项目级 API Key。请改用下方的“API Key 环境变量名”，或由部署方配置 NOVEL_AI_MODEL_SECRET_ENCRYPTION_KEY。`
      return
    }

    const modelsToSave = normalizedModels.map(buildModelPayload)

    await projectStore.updateModels(projectId.value, modelsToSave)
    const latestModels = await projectStore.fetchModels(projectId.value)
    modelsForm.value = normalizeModels(latestModels)
    resetModelTestStates()
    promptBuilderEnabled.value = latestModels.some(model => model.role === 'prompt_builder')
    syncPromptBuilderWithWriter()
    await setActiveTab('outlines')
  } catch (e: any) {
    setupError.value = e?.response?.data?.detail || e?.message || 'Failed to save models.'
  }
}

const saveOutlines = async () => {
  if (isNew.value || !outlinesText.value.trim()) return

  setupError.value = null
  setupNotice.value = null
  const outlines = syncTotalChaptersFromOutlines()
  if (outlineParseWarning.value || outlines.length === 0) {
    setupError.value = outlineParseWarning.value || '未解析到可导入的章节大纲。'
    return
  }

  const confirmed = window.confirm(
    `将导入 ${outlines.length} 章大纲，并清空当前项目已有的章节正文、生成事件、提示词版本、摘要、角色和世界观资源。确定继续吗？`
  )
  if (!confirmed) {
    return
  }

  try {
    await projectStore.importOutlines(projectId.value, outlines)
    await router.push(`/projects/${projectId.value}/live`)
  } catch (e: any) {
    setupError.value = e?.response?.data?.detail || e?.message || 'Failed to import outlines.'
  }
}

const openLiveWriting = async () => {
  if (isNew.value) {
    return
  }
  await router.push(`/projects/${projectId.value}/live`)
}

const openReader = async () => {
  if (isNew.value) {
    return
  }
  await router.push(`/projects/${projectId.value}/read`)
}

const autofillProjectInfoFromOutlines = async (switchToBasic = false) => {
  if (isNew.value) {
    setupError.value = '请先创建项目并保存模型配置，再根据大纲智能填充项目信息。'
    return
  }
  const outlines = syncTotalChaptersFromOutlines()
  if (outlineParseWarning.value || outlines.length === 0) {
    setupError.value = outlineParseWarning.value || '请先填写章节大纲，再根据大纲智能填充项目信息。'
    return
  }

  generatingGlobalPrompt.value = true
  setupError.value = null
  setupNotice.value = null
  try {
    const response = await api.post(`/projects/${projectId.value}/global-prompt/generate`, {
      title: projectForm.value.title?.trim() || '',
      genre: projectForm.value.genre?.trim() || '',
      style: projectForm.value.style?.trim() || '',
      outlines_text: outlines.map(item => item.outline_text).join('\n\n'),
    }) as ProjectAutofillResponse

    if (response.title?.trim()) {
      projectForm.value.title = response.title.trim()
    }
    if (response.genre?.trim()) {
      projectForm.value.genre = response.genre.trim()
    }
    if (response.style?.trim()) {
      projectForm.value.style = response.style.trim()
    }
    projectForm.value.global_prompt = response.global_prompt
    await projectStore.updateProject(projectId.value, buildProjectPayload())
    setupNotice.value = '已根据当前章节大纲智能回填书名、题材、风格和全局系统提示词，你仍可继续微调。'
    if (switchToBasic) {
      await setActiveTab('basic')
    }
  } catch (e: any) {
    setupError.value = e?.response?.data?.detail || e?.message || '根据大纲智能填充项目信息失败。'
  } finally {
    generatingGlobalPrompt.value = false
  }
}

const generateOutlineDraftFromGlobalPrompt = async () => {
  if (isNew.value) {
    setupError.value = '请先创建项目并保存模型配置，再根据全局提示词生成大纲草案。'
    return
  }

  const globalPrompt = projectForm.value.global_prompt?.trim() || ''
  if (!globalPrompt) {
    setupError.value = '请先填写全局系统提示词，再生成大纲草案。'
    return
  }

  if (outlinesText.value.trim()) {
    const confirmed = window.confirm('当前大纲编辑区已有内容，生成草案会覆盖当前文本。确定继续吗？')
    if (!confirmed) {
      return
    }
  }

  generatingOutlineDraft.value = true
  setupError.value = null
  setupNotice.value = null
  try {
    const response = await api.post(`/projects/${projectId.value}/outlines/generate`, {
      title: projectForm.value.title?.trim() || '',
      genre: projectForm.value.genre?.trim() || '',
      style: projectForm.value.style?.trim() || '',
      global_prompt: globalPrompt,
      total_chapters: Number(projectForm.value.total_chapters || 1),
    }) as OutlineDraftResponse

    outlinesText.value = serializeOutlines(response.outlines || [])
    syncTotalChaptersFromOutlines()
    setupNotice.value = '已根据全局提示词生成大纲草案，可继续编辑后再导入。'
  } catch (e: any) {
    setupError.value = e?.response?.data?.detail || e?.message || '根据全局提示词生成大纲草案失败。'
  } finally {
    generatingOutlineDraft.value = false
  }
}
</script>

<template>
  <div class="mx-auto max-w-screen-xl">
    <div class="mb-8 flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
      <div>
        <h1 class="text-2xl font-bold text-gray-900">{{ isNew ? '新建项目' : '项目配置' }}</h1>
        <p class="mt-2 text-sm text-gray-700">配置你的小说生成参数。</p>
      </div>
      <div v-if="!isNew" class="flex flex-wrap gap-2">
        <button @click="openLiveWriting" class="btn btn-secondary">
          实时写作
        </button>
        <button @click="openReader" class="btn btn-primary">
          阅读 / 导出
        </button>
      </div>
    </div>

    <div v-if="setupError" class="mb-6 rounded-md bg-red-50 p-4 text-sm text-red-700">
      {{ setupError }}
    </div>
    <div v-if="setupNotice" class="mb-6 rounded-md bg-green-50 p-4 text-sm text-green-700">
      {{ setupNotice }}
    </div>

    <div class="mb-6 overflow-x-auto border-b border-gray-200">
      <nav class="-mb-px flex min-w-max gap-6 sm:gap-8" aria-label="Tabs">
        <button
          @click="setActiveTab('basic')"
          :class="[activeTab === 'basic' ? 'border-primary-500 text-primary-600' : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300', 'whitespace-nowrap py-4 px-1 border-b-2 font-medium text-sm']"
        >
          基本信息
        </button>
        <button
          @click="setActiveTab('models')"
          :disabled="isNew || loadingProjectResources"
          :class="[activeTab === 'models' ? 'border-primary-500 text-primary-600' : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300', isNew || loadingProjectResources ? 'opacity-50 cursor-not-allowed' : '', 'whitespace-nowrap py-4 px-1 border-b-2 font-medium text-sm']"
        >
          模型配置
        </button>
        <button
          @click="setActiveTab('outlines')"
          :disabled="isNew || loadingProjectResources"
          :class="[activeTab === 'outlines' ? 'border-primary-500 text-primary-600' : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300', isNew || loadingProjectResources ? 'opacity-50 cursor-not-allowed' : '', 'whitespace-nowrap py-4 px-1 border-b-2 font-medium text-sm']"
        >
          章节大纲
        </button>
      </nav>
    </div>

    <div v-if="activeTab === 'basic'" class="card">
      <div class="card-body space-y-6">
        <div>
          <label class="block text-sm font-medium text-gray-700">书名</label>
          <input v-model="projectForm.title" type="text" class="input-field mt-1" placeholder="例如：风雪夜归人" />
        </div>
        <div class="grid grid-cols-1 gap-6 md:grid-cols-2">
          <div>
            <label class="block text-sm font-medium text-gray-700">题材</label>
            <input v-model="projectForm.genre" type="text" class="input-field mt-1" placeholder="例如：科幻、仙侠、悬疑" />
          </div>
          <div>
            <label class="block text-sm font-medium text-gray-700">风格</label>
            <input v-model="projectForm.style" type="text" class="input-field mt-1" placeholder="例如：冷峻、热血、轻松" />
          </div>
        </div>
        <div>
          <div class="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <label class="block text-sm font-medium text-gray-700">全局提示词</label>
            <button
              type="button"
              class="btn btn-sm btn-secondary"
              :disabled="isBusy || generatingGlobalPrompt || isNew || !outlinesText.trim()"
              @click="autofillProjectInfoFromOutlines(false)"
            >
              {{ generatingGlobalPrompt ? '填充中...' : '根据大纲智能填充项目信息' }}
            </button>
          </div>
          <textarea v-model="projectForm.global_prompt" rows="4" class="input-field mt-1" placeholder="填写整本小说的整体要求与约束..."></textarea>
          <p class="mt-2 text-xs text-gray-500">
            支持根据当前输入的章节大纲智能补全书名、题材、风格与整本书全局系统提示词，回填后仍可继续手动微调。
          </p>
          <p class="mt-1 text-xs text-gray-500">
            已填写的书名、题材、风格会优先沿用；如需按大纲重新推断，请先清空对应字段再点按钮。
          </p>
        </div>
        <div class="grid grid-cols-1 gap-6 md:grid-cols-2">
          <div>
            <label class="block text-sm font-medium text-gray-700">总章节数</label>
            <input v-model.number="projectForm.total_chapters" type="number" class="input-field mt-1" />
          </div>
          <div>
            <label class="block text-sm font-medium text-gray-700">单章最大重试次数</label>
            <input v-model.number="projectForm.max_retries" type="number" class="input-field mt-1" />
          </div>
        </div>
        <div class="flex items-center">
          <input v-model="projectForm.auto_mode" type="checkbox" class="h-4 w-4 text-primary-600 focus:ring-primary-500 border-gray-300 rounded" />
          <label class="ml-2 block text-sm text-gray-900">自动模式（持续生成，不在章节间暂停）</label>
        </div>
        <div class="rounded-lg border border-gray-200 bg-gray-50 px-4 py-4">
          <p class="text-sm font-medium text-gray-900">创作模式</p>
          <div class="mt-4 grid gap-3 md:grid-cols-2">
            <label class="flex cursor-pointer items-start gap-3 rounded-xl border border-gray-200 bg-white px-4 py-3">
              <input v-model="projectForm.generation_mode" value="standard" type="radio" class="mt-1 h-4 w-4 border-gray-300 text-primary-600" />
              <div>
                <p class="text-sm font-medium text-gray-900">标准模式</p>
                <p class="mt-1 text-xs text-gray-500">Writer、Critic、Memory 和 Prompt Builder 按完整流程协作。</p>
              </div>
            </label>
            <label class="flex cursor-pointer items-start gap-3 rounded-xl border border-gray-200 bg-white px-4 py-3">
              <input v-model="projectForm.generation_mode" value="rush" type="radio" class="mt-1 h-4 w-4 border-gray-300 text-primary-600" />
              <div>
                <p class="text-sm font-medium text-gray-900">Rush 极速创作</p>
                <p class="mt-1 text-xs text-gray-500">只调用 Writer，生成后直接通过，不执行评审、摘要或设定修订。</p>
              </div>
            </label>
          </div>
          <div v-if="projectForm.generation_mode === 'rush'" class="mt-4 max-w-xs">
            <label class="block text-sm font-medium text-gray-700">读取前几章正文</label>
            <input v-model.number="projectForm.rush_previous_chapter_count" type="number" min="0" max="50" class="input-field mt-1" />
          </div>
        </div>
        <div class="flex items-start">
          <input v-model="projectForm.hard_review_gates_enabled" type="checkbox" class="mt-0.5 h-4 w-4 text-primary-600 focus:ring-primary-500 border-gray-300 rounded" />
          <div class="ml-2">
            <label class="block text-sm text-gray-900">启用严格评审硬门槛</label>
            <p class="mt-1 text-xs text-gray-500">开启后会按大纲分、指令分、blocking_issues 和总分门槛强制判定失败；关闭后 critic 仍会给分和提建议，但改为综合判断是否通过。</p>
          </div>
        </div>
        <div class="flex items-start">
          <input v-model="projectForm.auto_accept_critic_failed" type="checkbox" class="mt-0.5 h-4 w-4 text-primary-600 focus:ring-primary-500 border-gray-300 rounded" />
          <div class="ml-2">
            <label class="block text-sm text-gray-900">评审未通过时自动放行</label>
            <p class="mt-1 text-xs text-gray-500">仍会执行 critic 评审并展示评分，但失败章节不会自动重写或暂停。</p>
          </div>
        </div>
        <div class="flex items-start">
          <input
            v-model="projectForm.auto_accept_on_max_retries"
            :disabled="projectForm.auto_accept_critic_failed"
            type="checkbox"
            class="mt-0.5 h-4 w-4 rounded border-gray-300 text-primary-600 focus:ring-primary-500 disabled:cursor-not-allowed disabled:opacity-50"
          />
          <div class="ml-2">
            <label class="block text-sm text-gray-900">达到最大重试次数时自动放行</label>
            <p class="mt-1 text-xs text-gray-500">若重试至最大次数仍未通过评审，将强制通过该章节，避免项目暂停。</p>
          </div>
        </div>
        <div class="rounded-lg border border-gray-200 bg-gray-50 px-4 py-4">
          <p class="text-sm font-medium text-gray-900">正文写作模式</p>
          <p class="mt-1 text-xs text-gray-500">该开关仅影响当前项目。流式模式会实时推送正文片段；非流式模式会等待整段生成完成后再进入后续流程。</p>
          <div class="mt-4 grid gap-3 md:grid-cols-2">
            <label class="flex cursor-pointer items-start gap-3 rounded-xl border border-gray-200 bg-white px-4 py-3">
              <input v-model="projectForm.writer_streaming_enabled" :value="true" type="radio" class="mt-1 h-4 w-4 border-gray-300 text-primary-600" />
              <div>
                <p class="text-sm font-medium text-gray-900">流式写作</p>
                <p class="mt-1 text-xs text-gray-500">边生成边推送 <code>content_chunk</code>，适合实时写作观察。</p>
              </div>
            </label>
            <label class="flex cursor-pointer items-start gap-3 rounded-xl border border-gray-200 bg-white px-4 py-3">
              <input v-model="projectForm.writer_streaming_enabled" :value="false" type="radio" class="mt-1 h-4 w-4 border-gray-300 text-primary-600" />
              <div>
                <p class="text-sm font-medium text-gray-900">非流式写作</p>
                <p class="mt-1 text-xs text-gray-500">直接走完整生成，产生 <code>writer_non_stream_started</code> / <code>writer_non_stream_succeeded</code> 事件链路。</p>
              </div>
            </label>
          </div>
        </div>
      </div>
      <div class="card-footer flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-end">
        <button @click="saveBasicInfo" class="btn btn-primary" :disabled="isBusy">
          {{ isNew ? '创建并继续' : '保存并继续' }}
        </button>
      </div>
    </div>

    <div v-if="activeTab === 'models'" class="space-y-6">
      <div class="rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-800">
        <p class="font-medium">当前项目已自动预置系统默认模型参数</p>
        <p class="mt-1 text-xs text-emerald-700">
          这里显示的是创建项目时复制到本项目的默认模型设置。后续你在这里的修改仅影响当前项目，不会反向改动系统设置。
        </p>
      </div>

      <div class="rounded-lg border border-blue-200 bg-blue-50 px-4 py-3 text-sm text-blue-800">
        <div class="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
          <div>
            <p class="font-medium">自定义提示词生成模型</p>
            <p class="mt-1 text-xs text-blue-700">
              默认情况下，提示词生成会直接复用创作模型。只有当你希望为提示词生成单独指定模型参数时，才需要开启这里进行自定义。
            </p>
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
                <span v-if="model.role === 'prompt_builder'" class="rounded bg-blue-100 px-2 py-0.5 text-xs font-medium text-blue-700">
                  自定义
                </span>
              </div>
              <p class="mt-2 max-w-2xl text-sm text-gray-600">{{ ROLE_DESCRIPTIONS[model.role] }}</p>
            </div>
            <div class="flex flex-col gap-3 xl:items-end">
              <div class="rounded-2xl bg-white/80 px-4 py-3 text-right text-xs text-gray-500 shadow-sm">
                <p>当前模型</p>
                <p class="mt-1 font-semibold text-gray-800">{{ model.model_name || '未设置' }}</p>
              </div>
              <button
                type="button"
                class="btn btn-sm btn-secondary"
                :disabled="isBusy || getModelTestState(model.role).loading"
                @click="testModelConnectivity(model)"
              >
                {{ getModelTestState(model.role).loading ? '测试中...' : '测试连通性' }}
              </button>
            </div>
          </div>
        </div>
        <div class="card-body space-y-6">
          <div class="grid gap-4 md:grid-cols-4">
            <div class="rounded-2xl border border-[var(--color-border)] bg-stone-50 px-4 py-4 md:col-span-4">
              <p class="text-xs uppercase tracking-wide text-gray-400">渠道</p>
              <select v-model="model.channel_id" class="input-field mt-3" @change="handleChannelChange(model)">
                <option value="">无（手动配置）</option>
                <option v-for="channel in channels.filter((c: ProviderChannel) => c.is_enabled)" :key="channel.id" :value="channel.id">
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
              <p v-else-if="model.channel_id && hasLoadedChannelModels(model.channel_id) && !channelModelsCache[model.channel_id]?.length" class="mt-2 text-xs text-amber-600">
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
                <p class="text-xs uppercase tracking-wide text-gray-400">项目级 API Key</p>
                <input
                  v-model="model.extra_config!.api_key"
                  type="password"
                  autocomplete="new-password"
                  class="input-field mt-3"
                  :disabled="model.extra_config?.can_save_api_key === false"
                  :placeholder="model.extra_config?.can_save_api_key === false ? '当前服务端未启用项目级密钥加密，请改用下方环境变量名' : '留空则保留当前已保存的 Key'"
                  @input="handleApiKeyInput(model)"
                />
                <div class="mt-2 flex flex-wrap items-center gap-3 text-xs">
                  <span v-if="model.extra_config?.clear_api_key" class="text-amber-700">保存后将清空已存 API Key</span>
                  <span v-else-if="model.extra_config?.api_key?.trim()" class="text-blue-700">保存后将更新项目级 API Key</span>
                  <span v-else-if="model.extra_config?.has_api_key" class="text-emerald-700">已设置项目级 API Key</span>
                  <span v-else-if="model.extra_config?.can_save_api_key === false" class="text-amber-700">当前服务端不允许直接保存项目级 API Key</span>
                  <span v-else class="text-gray-500">当前未设置项目级 API Key</span>
                  <button
                    v-if="model.extra_config?.has_api_key || model.extra_config?.clear_api_key"
                    type="button"
                    class="text-red-600 hover:text-red-700"
                    @click="markApiKeyForClear(model)"
                  >
                    清空已存 API Key
                  </button>
                </div>
                <p v-if="model.extra_config?.can_save_api_key === true" class="mt-2 text-xs text-gray-500">
                  出于安全考虑，已保存的 API Key 不会回显；输入新值后会覆盖旧值。
                </p>
                <p v-else class="mt-2 text-xs text-amber-700">
                  当前服务端未启用项目级密钥加密，不能直接保存项目级 API Key。请改用 API Key 环境变量名，或由部署方配置 NOVEL_AI_MODEL_SECRET_ENCRYPTION_KEY。
                </p>
              </div>

              <div>
                <p class="text-xs uppercase tracking-wide text-gray-400">API Key 环境变量名</p>
                <input
                  v-model="model.extra_config!.api_key_env_var"
                  type="text"
                  class="input-field mt-3"
                  :placeholder="DEFAULT_API_KEY_ENV[model.role]"
                />
                <p class="mt-2 text-xs text-gray-500">
                  未设置项目级 API Key 时，会回退到这里指定的环境变量；若这里也留空，则回退到角色默认环境变量名。
                </p>
              </div>

              <label v-if="model.role === 'critic'" class="flex items-start gap-3 rounded-xl border border-emerald-200 bg-emerald-50 px-3 py-3 text-sm text-emerald-900">
                <input v-model="model.extra_config!.supports_json_schema_output" type="checkbox" class="mt-0.5 h-4 w-4 rounded border-emerald-300 text-primary-600" />
                <span>
                  <span class="font-medium">启用结构化 JSON 输出</span>
                  <span class="mt-1 block text-xs text-emerald-800">推荐开启。监督模型会优先按 schema 返回结构化结果；若上游不兼容，后端会自动按单次请求降级。</span>
                </span>
              </label>
            </div>
            <div v-else class="rounded-2xl border border-dashed border-[var(--color-border)] bg-stone-50 px-4 py-4 text-xs text-gray-500">
              模拟模型不需要 API Key。若切换到 OpenAI 兼容接口，可直接填写项目级 API Key，或配置类似 <code>{{ DEFAULT_API_KEY_ENV[model.role] }}</code> 的环境变量名。
            </div>

            <div class="rounded-2xl border border-[var(--color-border)] bg-stone-50 px-4 py-4">
              <p class="text-xs uppercase tracking-wide text-gray-400">温度策略</p>
              <input v-model.number="model.temperature" type="number" step="0.1" class="input-field mt-3" :placeholder="model.role === 'writer' ? '1.2' : '保持模型默认值'" />
              <p class="mt-2 text-xs text-gray-500">{{ model.role === 'writer' ? '创作模型默认温度为 1.2。' : '留空表示不主动设置温度，保持模型默认值。' }}</p>
              <div class="mt-3">
                <p class="text-xs uppercase tracking-wide text-gray-400">最大 Tokens</p>
                <input v-model.number="model.max_tokens" type="number" min="1" class="input-field mt-3" placeholder="留空则保持模型服务默认策略" />
              </div>
            </div>
          </div>

          <div v-if="model.provider === 'openai_compatible'" class="rounded-2xl border border-[var(--color-border)] bg-white">
            <button
              type="button"
              class="flex w-full items-center justify-between px-4 py-3 text-left"
              @click="advancedParamsOpen[model.role] = !advancedParamsOpen[model.role]"
            >
              <div>
                <p class="text-xs uppercase tracking-wide text-gray-400">自定义 LLM 参数</p>
                <p class="mt-1 text-xs text-gray-500">透传任意 OpenAI 兼容参数，如 top_p、reasoning_effort、seed 等</p>
              </div>
              <span class="text-gray-400 transition-transform" :class="{ 'rotate-180': advancedParamsOpen[model.role] }">
                <svg xmlns="http://www.w3.org/2000/svg" class="h-4 w-4" viewBox="0 0 20 20" fill="currentColor">
                  <path fill-rule="evenodd" d="M5.293 7.293a1 1 0 011.414 0L10 10.586l3.293-3.293a1 1 0 111.414 1.414l-4 4a1 1 0 01-1.414 0l-4-4a1 1 0 010-1.414z" clip-rule="evenodd" />
                </svg>
              </span>
            </button>
            <div v-if="advancedParamsOpen[model.role]" class="border-t border-[var(--color-border)] px-4 py-4 space-y-3">
              <div v-for="entry in getCustomParamEntries(model)" :key="entry[0]" class="grid grid-cols-[1fr_2fr_auto] items-center gap-2">
                <input
                  type="text"
                  class="input-field"
                  :value="entry[0]"
                  @change="renameCustomParam(model, entry[0], ($event.target as HTMLInputElement).value)"
                  placeholder="参数名"
                />
                <input
                  type="text"
                  class="input-field"
                  :value="typeof entry[1] === 'object' ? JSON.stringify(entry[1]) : String(entry[1])"
                  @change="setCustomParamValue(model, entry[0], ($event.target as HTMLInputElement).value)"
                  placeholder="值（数字、true/false、JSON 或字符串）"
                />
                <button type="button" class="text-red-500 hover:text-red-700 text-sm whitespace-nowrap" @click="removeCustomParam(model, entry[0])">删除</button>
              </div>
              <button type="button" class="btn btn-sm btn-secondary" @click="addCustomParam(model)">+ 添加参数</button>
            </div>
          </div>

          <div
            v-if="getModelTestState(model.role).result"
            class="rounded-2xl border px-4 py-4"
            :class="getModelTestState(model.role).result?.success ? 'border-emerald-200 bg-emerald-50' : 'border-red-200 bg-red-50'"
          >
            <div class="flex flex-wrap items-center justify-between gap-3">
              <div>
                <p
                  class="text-sm font-semibold"
                  :class="getModelTestState(model.role).result?.success ? 'text-emerald-900' : 'text-red-900'"
                >
                  {{ getModelTestState(model.role).result?.success ? '连通性测试通过' : '连通性测试失败' }}
                </p>
                <p
                  class="mt-1 text-sm"
                  :class="getModelTestState(model.role).result?.success ? 'text-emerald-800' : 'text-red-800'"
                >
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
            <div
              v-if="!getModelTestState(model.role).result?.success && getModelTestState(model.role).result?.raw_preview"
              class="mt-3 rounded-xl bg-white/80 px-3 py-3 text-xs text-gray-600"
            >
              <p class="font-medium text-gray-700">原始返回片段</p>
              <p class="mt-2 break-words whitespace-pre-wrap">{{ getModelTestState(model.role).result?.raw_preview }}</p>
            </div>
            <p class="mt-3 text-xs text-gray-500">
              本次结果基于你当前表单里的配置即时测试，未保存的自定义 URL、API Key 与模型名也会参与测试。
            </p>
          </div>
        </div>
      </div>
      <div class="flex flex-col gap-3 sm:flex-row sm:justify-end">
        <button @click="saveModels" class="btn btn-primary" :disabled="isBusy">
          保存模型并继续
        </button>
      </div>
    </div>

    <div v-if="activeTab === 'outlines'" class="card">
      <div class="card-body">
        <p class="text-sm text-gray-500 mb-4">
          在这里粘贴章节大纲。推荐使用“第1章：标题”格式；每一章之间请用一个空行分隔。
        </p>
        <textarea
          v-model="outlinesText"
          rows="15"
          class="input-field font-mono text-sm"
          placeholder="第1章：故事开端……&#10;&#10;第2章：冲突升级……"
        ></textarea>
        <div v-if="outlinesText.trim()" class="mt-3 space-y-2 text-xs">
          <p class="text-gray-500">当前识别 {{ outlinePreviewCount }} 章，请确认数量无误后再导入。</p>
          <p v-if="outlineParseWarning" class="rounded-md bg-amber-50 px-3 py-2 text-amber-700">
            {{ outlineParseWarning }}
          </p>
        </div>
      </div>
      <div class="card-footer flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div class="flex flex-col gap-3 sm:flex-row sm:items-center">
          <button
            @click="autofillProjectInfoFromOutlines(true)"
            class="btn btn-secondary"
            :disabled="isBusy || generatingGlobalPrompt || !outlinesText.trim()"
          >
            {{ generatingGlobalPrompt ? '正在智能填充项目信息...' : '根据当前大纲智能填充项目信息' }}
          </button>
          <button
            @click="generateOutlineDraftFromGlobalPrompt"
            class="btn btn-secondary"
            :disabled="isBusy || generatingOutlineDraft || isNew || !projectForm.global_prompt?.trim()"
          >
            {{ generatingOutlineDraft ? '正在生成大纲草案...' : '根据全局提示词生成大纲草案' }}
          </button>
        </div>
        <button @click="saveOutlines" class="btn btn-primary" :disabled="isBusy || !outlinesText.trim() || Boolean(outlineParseWarning)">
          导入大纲并开始
        </button>
      </div>
    </div>
  </div>
</template>
