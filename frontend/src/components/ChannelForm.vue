<script setup lang="ts">
import { ref, watch } from 'vue'
import type { ProviderChannel, ChannelFormData, ProviderChannelModel } from '@/types'
import { useSystemStore } from '@/stores/system'

const props = defineProps<{
  channel?: ProviderChannel | null
  loading?: boolean
}>()

const emit = defineEmits<{
  (e: 'submit', data: ChannelFormData): void
  (e: 'cancel'): void
}>()

const systemStore = useSystemStore()

const connectionStatus = ref<'idle' | 'testing' | 'success' | 'error'>('idle')
const fetchMessage = ref('')
const fetchedModels = ref<ProviderChannelModel[]>([])
const manualModelInput = ref(false)

const formData = ref<ChannelFormData & { discovered_models?: ProviderChannelModel[] }>({
  name: '',
  provider: 'openai_compatible',
  base_url: '',
  default_model_name: '',
  api_key: '',
  api_key_env_var: '',
  clear_api_key: false,
  is_enabled: true,
})

const errors = ref<Partial<Record<keyof ChannelFormData, string>>>({})

const validate = (): boolean => {
  errors.value = {}
  let isValid = true

  if (!formData.value.name?.trim()) {
    errors.value.name = '名称不能为空'
    isValid = false
  }
  
  if (!formData.value.provider) {
    errors.value.provider = '必须选择提供方'
    isValid = false
  }

  if (formData.value.provider === 'openai_compatible' && !formData.value.base_url?.trim()) {
    errors.value.base_url = '基础地址不能为空'
    isValid = false
  }

  if (!formData.value.default_model_name?.trim()) {
    errors.value.default_model_name = '默认模型名不能为空'
    isValid = false
  }

  return isValid
}

const handleFetchModels = async () => {
  if (!formData.value.base_url || !formData.value.provider) return

  connectionStatus.value = 'testing'
  fetchMessage.value = '连接测试中...'
  
  try {
    let result
    const hasConnectionOverrides = Boolean(
      formData.value.api_key?.trim() ||
      (props.channel && (
        formData.value.provider !== props.channel.provider ||
        formData.value.base_url !== props.channel.base_url
      ))
    )
    
    // Existing channel without unsaved connection changes can refresh with saved API key
    if (props.channel?.id && !hasConnectionOverrides) {
      result = await systemStore.refreshChannelModels(props.channel.id, {
        provider: formData.value.provider,
        base_url: formData.value.base_url,
      })
    } else {
      // New channels or edited-but-unsaved connection changes use in-form values for discovery
      result = await systemStore.discoverChannelModels({
        provider: formData.value.provider,
        base_url: formData.value.base_url,
        api_key: formData.value.api_key || undefined
      })
    }
    
    if (result?.success) {
      const models = result.models ?? []
      const modelsCount = result.models_count ?? models.length

      fetchedModels.value = models
      connectionStatus.value = 'success'
      fetchMessage.value = `✓ 连接成功，发现 ${modelsCount} 个模型`
      
      // Auto-select a model if we don't have one and there are choices
      if (!formData.value.default_model_name && models.length > 0) {
        formData.value.default_model_name = result.default_model_name || models[0].model_name
      } else if (models.length > 0) {
        // Ensure manual input is false if we found models, so the dropdown shows
        manualModelInput.value = false
      }
    } else {
      connectionStatus.value = 'error'
      fetchMessage.value = `✗ 连接失败: ${result?.message || '未知错误'}`
    }
  } catch (err: any) {
    connectionStatus.value = 'error'
    fetchMessage.value = `✗ 连接失败: ${err.message || '网络错误'}`
  }
}

const handleSubmit = () => {
  if (validate()) {
    const payload: ChannelFormData & { discovered_models?: ProviderChannelModel[] } = { ...formData.value }
    if (fetchedModels.value.length > 0) {
      payload.discovered_models = fetchedModels.value
    }
    emit('submit', payload)
  }
}

const handleCancel = () => {
  emit('cancel')
}

watch(
  () => props.channel,
  async (newChannel) => {
    if (newChannel) {
      formData.value = {
        id: newChannel.id,
        name: newChannel.name,
        provider: newChannel.provider,
        base_url: newChannel.base_url,
        default_model_name: newChannel.default_model_name,
        api_key: '',
        api_key_env_var: newChannel.api_key_env_var || '',
        clear_api_key: false,
        is_enabled: newChannel.is_enabled,
      }
      
      // Fetch models if it's an existing channel
      if (newChannel.id) {
        try {
          const models = await systemStore.fetchChannelModels(newChannel.id)
          if (models && models.length > 0) {
            fetchedModels.value = models
            // If the current default model is not in the list, enable manual input
            if (newChannel.default_model_name && !models.some(m => m.model_name === newChannel.default_model_name)) {
              manualModelInput.value = true
            }
          }
        } catch (e) {
          console.error("Failed to fetch channel models on mount", e)
        }
      }
    } else {
      formData.value = {
        name: '',
        provider: 'openai_compatible',
        base_url: '',
        default_model_name: '',
        api_key: '',
        api_key_env_var: '',
        clear_api_key: false,
        is_enabled: true,
      }
      fetchedModels.value = []
      connectionStatus.value = 'idle'
      fetchMessage.value = ''
      manualModelInput.value = false
    }
    errors.value = {}
  },
  { immediate: true, deep: true }
)
</script>

<template>
  <div class="card">
    <div class="card-header">
      <h2 class="text-lg font-semibold text-gray-900">
        {{ channel ? '编辑渠道' : '新增渠道' }}
      </h2>
    </div>
    <div class="card-body space-y-6">
      <div class="grid gap-6 md:grid-cols-2">
        <!-- Name -->
        <div class="md:col-span-2">
          <label class="block text-sm font-medium text-gray-700">渠道名称 <span class="text-red-500">*</span></label>
          <input
            v-model="formData.name"
            type="text"
            class="input-field mt-1"
            :class="{ 'border-red-300 ring-red-500': errors.name }"
            placeholder="例如：OpenAI 官方、DeepSeek 等"
          />
          <p v-if="errors.name" class="mt-1 text-xs text-red-600">{{ errors.name }}</p>
        </div>

        <!-- Provider -->
        <div>
          <label class="block text-sm font-medium text-gray-700">提供方 <span class="text-red-500">*</span></label>
          <select
            v-model="formData.provider"
            class="input-field mt-1"
            :class="{ 'border-red-300 ring-red-500': errors.provider }"
          >
            <option value="openai_compatible">OpenAI 兼容接口</option>
            <option value="mock">模拟模型 (Mock)</option>
          </select>
          <p v-if="errors.provider" class="mt-1 text-xs text-red-600">{{ errors.provider }}</p>
        </div>

        <!-- Base URL -->
        <div class="md:col-span-2">
          <label class="block text-sm font-medium text-gray-700">
            基础地址 (Base URL)
            <span v-if="formData.provider === 'openai_compatible'" class="text-red-500">*</span>
          </label>
          <input
            v-model="formData.base_url"
            type="text"
            class="input-field mt-1"
            :class="{ 'border-red-300 ring-red-500': errors.base_url }"
            placeholder="https://api.openai.com/v1"
          />
          <p v-if="errors.base_url" class="mt-1 text-xs text-red-600">{{ errors.base_url }}</p>
        </div>

        <!-- Default Model Name -->
        <div>
          <label class="block text-sm font-medium text-gray-700">默认模型名称 <span class="text-red-500">*</span></label>
          <div v-if="fetchedModels.length > 0 && !manualModelInput">
            <div class="flex gap-2 items-start mt-1">
              <select
                v-model="formData.default_model_name"
                class="input-field flex-1"
                :class="{ 'border-red-300 ring-red-500': errors.default_model_name }"
                @change="(e) => { if ((e.target as HTMLSelectElement).value === '__manual__') { manualModelInput = true; formData.default_model_name = ''; } }"
              >
                <option disabled value="">请选择默认模型</option>
                <option v-for="model in fetchedModels" :key="model.model_name" :value="model.model_name">
                  {{ model.display_name || model.model_name }}
                  {{ model.owned_by ? `(${model.owned_by})` : '' }}
                </option>
                <option value="__manual__">-- 手动输入 --</option>
              </select>
            </div>
          </div>
          <div v-else>
            <div class="flex gap-2 items-start mt-1">
              <input
                v-model="formData.default_model_name"
                type="text"
                class="input-field flex-1"
                :class="{ 'border-red-300 ring-red-500': errors.default_model_name }"
                placeholder="例如：gpt-4o"
              />
              <button 
                v-if="fetchedModels.length > 0"
                type="button"
                class="btn btn-secondary whitespace-nowrap"
                @click="manualModelInput = false"
              >
                返回选择
              </button>
            </div>
          </div>
          <p v-if="errors.default_model_name" class="mt-1 text-xs text-red-600">{{ errors.default_model_name }}</p>
        </div>

        <!-- API Key -->
        <div class="md:col-span-2">
          <label class="block text-sm font-medium text-gray-700">
            API Key
            <span v-if="channel?.has_api_key" class="text-xs text-gray-500 font-normal ml-2">(已保存，留空则保留原值)</span>
          </label>
          <div class="flex gap-2">
            <input
              v-model="formData.api_key"
              type="password"
              class="input-field mt-1 flex-1"
              :placeholder="channel?.has_api_key ? '已保存 API Key，如需更改请输入新值' : 'sk-xxxxxxxxxxxxxxxxxxxxxxxx'"
            />
            <button
              type="button"
              class="btn btn-secondary mt-1 whitespace-nowrap"
              :disabled="!formData.base_url || formData.provider !== 'openai_compatible' || systemStore.discoveringModels || systemStore.refreshingModels"
              @click="handleFetchModels"
            >
              <span v-if="systemStore.discoveringModels || systemStore.refreshingModels">
                <svg class="animate-spin -ml-1 mr-2 h-4 w-4 text-gray-700 inline" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                  <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
                  <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                </svg>
                测试中...
              </span>
              <span v-else>{{ channel ? '重新拉取模型' : '测试连接并拉取模型' }}</span>
            </button>
          </div>
          
          <!-- Connection Status Message -->
          <div v-if="connectionStatus !== 'idle'" class="mt-2 text-sm" :class="{
            'text-gray-500': connectionStatus === 'testing',
            'text-green-600': connectionStatus === 'success',
            'text-red-600': connectionStatus === 'error'
          }">
            {{ fetchMessage }}
          </div>

          <div class="mt-2 space-y-1 text-xs">
            <p class="text-gray-500">{{ channel?.has_api_key ? '当前已设置 API Key。留空则保留现有值，输入新值则更新。' : '留空表示当前不设置 API Key。' }}</p>
            <label v-if="channel?.has_api_key" class="inline-flex items-center gap-2 cursor-pointer text-amber-700">
              <input
                v-model="formData.clear_api_key"
                type="checkbox"
                class="h-4 w-4 rounded border-gray-300 text-primary-600 focus:ring-primary-600"
              />
              <span>保存时清除当前 API Key</span>
            </label>
          </div>
        </div>

        <!-- Is Enabled -->
        <div class="flex items-center md:col-span-2">
          <label class="inline-flex items-center gap-2 cursor-pointer">
            <input
              v-model="formData.is_enabled"
              type="checkbox"
              class="h-4 w-4 rounded border-gray-300 text-primary-600 focus:ring-primary-600"
            />
            <span class="text-sm font-medium text-gray-900">启用该渠道</span>
          </label>
        </div>
      </div>
    </div>
    <div class="card-footer flex justify-end gap-3">
      <button
        type="button"
        class="btn btn-secondary"
        :disabled="loading"
        @click="handleCancel"
      >
        取消
      </button>
      <button
        type="button"
        class="btn btn-primary"
        :disabled="loading"
        @click="handleSubmit"
      >
        {{ loading ? '保存中...' : (channel ? '更新渠道' : '保存渠道') }}
      </button>
    </div>
  </div>
</template>
