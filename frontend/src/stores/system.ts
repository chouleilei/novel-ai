import { defineStore } from 'pinia'
import { ref } from 'vue'
import api from '@/api'
import type {
  ChannelDiscoverRequest,
  ChannelDiscoverResponse,
  ChannelFormData,
  ModelConfig,
  ModelConnectivityResult,
  ProviderChannel,
  ProviderChannelModel,
  SystemProjectDefaults,
  SystemRuntimeSettings,
  SystemSettingsResponse,
} from '@/types'

export const useSystemStore = defineStore('system', () => {
  const loading = ref(false)
  const error = ref<string | null>(null)

  const getErrorMessage = (err: any, fallback: string) => {
    return err?.response?.data?.detail || err?.message || fallback
  }

  const fetchSettings = async () => {
    loading.value = true
    error.value = null
    try {
      const response = await api.get('/system/settings')
      return response as unknown as SystemSettingsResponse
    } catch (err: any) {
      error.value = getErrorMessage(err, 'Failed to fetch system settings')
      throw err
    } finally {
      loading.value = false
    }
  }

  const updateProjectDefaults = async (payload: SystemProjectDefaults) => {
    loading.value = true
    error.value = null
    try {
      const response = await api.put('/system/project-defaults', payload)
      return response as unknown as SystemProjectDefaults
    } catch (err: any) {
      error.value = getErrorMessage(err, 'Failed to update system project defaults')
      throw err
    } finally {
      loading.value = false
    }
  }

  const updateModelConfigs = async (models: ModelConfig[]) => {
    loading.value = true
    error.value = null
    try {
      await api.put('/system/model-configs', models)
    } catch (err: any) {
      error.value = getErrorMessage(err, 'Failed to update system model configs')
      throw err
    } finally {
      loading.value = false
    }
  }

  const updateRuntimeSettings = async (payload: SystemRuntimeSettings) => {
    loading.value = true
    error.value = null
    try {
      const response = await api.put('/system/runtime-settings', payload)
      return response as unknown as SystemRuntimeSettings
    } catch (err: any) {
      error.value = getErrorMessage(err, 'Failed to update system runtime settings')
      throw err
    } finally {
      loading.value = false
    }
  }

  const testModelConnectivity = async (model: ModelConfig) => {
    error.value = null
    try {
      const response = await api.post('/system/model-configs/test', model)
      return response as unknown as ModelConnectivityResult
    } catch (err: any) {
      const detail = err?.response?.data?.detail
      if (detail && typeof detail === 'object') {
        return detail as ModelConnectivityResult
      }
      error.value = getErrorMessage(err, 'Failed to test system model connectivity')
      throw err
    }
  }

  const channels = ref<ProviderChannel[]>([])
  const channelLoading = ref(false)

  const fetchChannels = async () => {
    channelLoading.value = true
    error.value = null
    try {
      const response = await api.get('/system/channels')
      channels.value = response as unknown as ProviderChannel[]
      return channels.value
    } catch (err: any) {
      error.value = getErrorMessage(err, 'Failed to fetch channels')
      throw err
    } finally {
      channelLoading.value = false
    }
  }

  const createChannel = async (data: ChannelFormData) => {
    channelLoading.value = true
    error.value = null
    try {
      const response = await api.post('/system/channels', data)
      return response as unknown as ProviderChannel
    } catch (err: any) {
      error.value = getErrorMessage(err, 'Failed to create channel')
      throw err
    } finally {
      channelLoading.value = false
    }
  }

  const updateChannel = async (id: string, data: ChannelFormData) => {
    channelLoading.value = true
    error.value = null
    try {
      const response = await api.put(`/system/channels/${id}`, data)
      return response as unknown as ProviderChannel
    } catch (err: any) {
      error.value = getErrorMessage(err, 'Failed to update channel')
      throw err
    } finally {
      channelLoading.value = false
    }
  }

  const deleteChannel = async (id: string) => {
    channelLoading.value = true
    error.value = null
    try {
      await api.delete(`/system/channels/${id}`)
    } catch (err: any) {
      error.value = getErrorMessage(err, 'Failed to delete channel')
      throw err
    } finally {
      channelLoading.value = false
    }
  }

  const discoveringModels = ref(false)
  const refreshingModels = ref(false)

  const discoverChannelModels = async (payload: ChannelDiscoverRequest) => {
    discoveringModels.value = true
    error.value = null
    try {
      const response = await api.post('/system/channels/discover-models', payload)
      return response as unknown as ChannelDiscoverResponse
    } catch (err: any) {
      error.value = getErrorMessage(err, 'Failed to discover models')
      throw err
    } finally {
      discoveringModels.value = false
    }
  }

  const refreshChannelModels = async (channelId: string, payload?: ChannelDiscoverRequest) => {
    refreshingModels.value = true
    error.value = null
    try {
      const response = await api.post(`/system/channels/${channelId}/refresh-models`, payload)
      return response as unknown as ChannelDiscoverResponse
    } catch (err: any) {
      error.value = getErrorMessage(err, 'Failed to refresh models')
      throw err
    } finally {
      refreshingModels.value = false
    }
  }

  const fetchChannelModels = async (channelId: string) => {
    loading.value = true
    error.value = null
    try {
      const response = await api.get(`/system/channels/${channelId}/models`)
      return response as unknown as ProviderChannelModel[]
    } catch (err: any) {
      error.value = getErrorMessage(err, 'Failed to fetch channel models')
      throw err
    } finally {
      loading.value = false
    }
  }

  return {
    loading,
    error,
    channels,
    channelLoading,
    discoveringModels,
    refreshingModels,
    fetchSettings,
    updateProjectDefaults,
    updateModelConfigs,
    updateRuntimeSettings,
    testModelConnectivity,
    fetchChannels,
    createChannel,
    updateChannel,
    deleteChannel,
    discoverChannelModels,
    refreshChannelModels,
    fetchChannelModels,
  }
})
