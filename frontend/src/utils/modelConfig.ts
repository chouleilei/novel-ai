import type { ModelConfig, ModelConnectivityResult, ModelExtraConfig } from '@/types'

export const MODEL_ROLE_ORDER: ModelConfig['role'][] = ['writer', 'critic', 'memory', 'prompt_builder']

export const DEFAULT_API_KEY_ENV: Record<ModelConfig['role'], string> = {
  writer: 'WRITER_API_KEY',
  critic: 'CRITIC_API_KEY',
  memory: 'MEMORY_API_KEY',
  prompt_builder: 'WRITER_API_KEY',
}

export const ROLE_LABELS: Record<ModelConfig['role'], string> = {
  writer: '创作模型',
  critic: '监督模型',
  memory: '上下文与记忆模型',
  prompt_builder: '提示词构建模型',
}

export const ROLE_DESCRIPTIONS: Record<ModelConfig['role'], string> = {
  writer: '负责根据章节大纲、上下文和提示词生成正文。',
  critic: '负责章节评审，判断是否通过并给出分数与建议。',
  memory: '负责摘要、角色状态、世界观更新与远期记忆压缩。',
  prompt_builder: '负责根据当前大纲生成更稳定的系统提示词。',
}

export const normalizeModelExtraConfig = (extraConfig?: ModelExtraConfig) => {
  return {
    api_key: extraConfig?.api_key || '',
    api_key_env_var: extraConfig?.api_key_env_var || undefined,
    has_api_key: extraConfig?.has_api_key === true,
    can_save_api_key: extraConfig?.can_save_api_key,
    clear_api_key: extraConfig?.clear_api_key === true,
    supports_json_schema_output: extraConfig?.supports_json_schema_output === true,
    custom_params: extraConfig?.custom_params ?? null,
  }
}

export const buildDefaultModel = (role: ModelConfig['role']): ModelConfig => {
  const defaults: Record<'writer' | 'critic' | 'memory', Omit<ModelConfig, 'role'>> = {
    writer: {
      provider: 'openai_compatible',
      base_url: '',
      model_name: '',
      temperature: null,
      max_tokens: null,
      extra_config: normalizeModelExtraConfig({ api_key_env_var: DEFAULT_API_KEY_ENV.writer }),
    },
    critic: {
      provider: 'openai_compatible',
      base_url: '',
      model_name: '',
      temperature: null,
      max_tokens: null,
      extra_config: normalizeModelExtraConfig({
        api_key_env_var: DEFAULT_API_KEY_ENV.critic,
        supports_json_schema_output: true,
      }),
    },
    memory: {
      provider: 'openai_compatible',
      base_url: '',
      model_name: '',
      temperature: null,
      max_tokens: null,
      extra_config: normalizeModelExtraConfig({ api_key_env_var: DEFAULT_API_KEY_ENV.memory }),
    },
  }

  if (role === 'prompt_builder') {
    const writerDefaults = defaults.writer
    return {
      role,
      ...writerDefaults,
      extra_config: normalizeModelExtraConfig(writerDefaults.extra_config),
    }
  }

  return {
    role,
    ...defaults[role],
  }
}

export const createDefaultModels = (): ModelConfig[] => MODEL_ROLE_ORDER.map(role => buildDefaultModel(role))

export const buildPromptBuilderFromWriter = (writerModel?: ModelConfig): ModelConfig => {
  const source = writerModel || buildDefaultModel('writer')
  return {
    ...source,
    role: 'prompt_builder',
    id: undefined,
    project_id: undefined,
    extra_config: normalizeModelExtraConfig(source.extra_config),
  }
}

export const normalizeModels = (models: ModelConfig[]) => {
  if (models.length === 0) {
    return createDefaultModels()
  }

  const roleMap = new Map<ModelConfig['role'], ModelConfig>()
  for (const model of models) {
    const fallback = buildDefaultModel(model.role)
    roleMap.set(model.role, {
      ...fallback,
      ...model,
      temperature: typeof model.temperature === 'number' ? Number(model.temperature) : fallback.temperature ?? null,
      max_tokens: typeof model.max_tokens === 'number' ? Number(model.max_tokens) : fallback.max_tokens ?? null,
      extra_config: normalizeModelExtraConfig({
        ...fallback.extra_config,
        ...(model.extra_config || {}),
      }),
    })
  }

  return MODEL_ROLE_ORDER.map(role => {
    if (role === 'prompt_builder') {
      return roleMap.get(role) || buildPromptBuilderFromWriter(roleMap.get('writer') || buildDefaultModel('writer'))
    }
    return roleMap.get(role) || buildDefaultModel(role)
  })
}

export const buildModelPayload = (model: ModelConfig): ModelConfig => {
  const extraConfig = model.extra_config || {}
  const apiKey = extraConfig.api_key?.trim() || undefined
  const shouldClearApiKey = extraConfig.clear_api_key === true && !apiKey

  const customParams = extraConfig.custom_params && typeof extraConfig.custom_params === 'object' && Object.keys(extraConfig.custom_params).length > 0
    ? extraConfig.custom_params
    : null

  const payload: ModelConfig = {
    ...model,
    extra_config: {
      api_key: apiKey,
      api_key_env_var: extraConfig.api_key_env_var?.trim() || undefined,
      clear_api_key: shouldClearApiKey,
      supports_json_schema_output: extraConfig.supports_json_schema_output === true,
      custom_params: customParams,
    },
  }

  return payload
}

export const getApiKeySourceLabel = (source?: ModelConnectivityResult['api_key_source']) => {
  switch (source) {
    case 'inline':
      return '本次输入的 API Key'
    case 'saved':
      return '已保存的 API Key'
    case 'env_var':
      return '环境变量 API Key'
    case 'not_required':
      return '无需 API Key'
    default:
      return '未知'
  }
}
