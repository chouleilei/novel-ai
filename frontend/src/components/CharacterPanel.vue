<script setup lang="ts">
import { ref, onMounted } from 'vue'
import api from '@/api'

const props = defineProps<{
  projectId: string
}>()

const characters = ref<any[]>([])
const loading = ref(false)

const fetchCharacters = async () => {
  loading.value = true
  try {
    const res = await api.get(`/projects/${props.projectId}/characters`)
    characters.value = res as unknown as any[]
  } catch (e) {
    console.error('Failed to fetch characters', e)
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  fetchCharacters()
})
</script>

<template>
  <div class="flex flex-col h-full">
    <div class="bg-gray-50 px-4 py-3 border-b border-gray-200 flex justify-between items-center">
      <h3 class="text-sm font-medium text-gray-900">角色</h3>
      <button @click="fetchCharacters" class="text-gray-400 hover:text-gray-600">
        <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"></path></svg>
      </button>
    </div>
    <div class="flex-1 overflow-y-auto p-4">
      <div v-if="loading" class="text-center py-4">
        <div class="inline-block animate-spin rounded-full h-4 w-4 border-b-2 border-primary-600"></div>
      </div>
      <div v-else-if="characters.length === 0" class="text-center py-4 text-sm text-gray-500">
        暂无角色信息。
      </div>
      <div v-else class="space-y-4">
        <div v-for="char in characters" :key="char.id" class="bg-white border border-gray-200 rounded-md p-3">
          <div class="flex justify-between items-start mb-2">
            <h4 class="font-bold text-gray-900">{{ char.name }}</h4>
            <span v-if="char.role" class="text-xs bg-gray-100 text-gray-600 px-2 py-0.5 rounded">{{ char.role }}</span>
          </div>
          <div class="text-sm text-gray-600 space-y-1">
            <p v-if="char.profile_json?.appearance"><span class="font-medium">外貌：</span> {{ char.profile_json.appearance }}</p>
            <p v-if="char.profile_json?.personality"><span class="font-medium">性格：</span> {{ char.profile_json.personality }}</p>
            <p v-if="char.profile_json?.background"><span class="font-medium">背景：</span> {{ char.profile_json.background }}</p>
            <p v-if="char.profile_json?.current_status"><span class="font-medium text-primary-600">状态：</span> {{ char.profile_json.current_status }}</p>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
