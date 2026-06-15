<script setup lang="ts">
import { ref, onMounted, computed, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import api from '@/api'
import type { ChapterPrompt } from '@/types'
import { formatDateTime } from '@/utils/datetime'

const route = useRoute()
const router = useRouter()
const projectId = computed(() => route.params.id as string)
const chapterNumber = computed(() => parseInt(route.params.chapterNumber as string))

const prompts = ref<ChapterPrompt[]>([])
const currentPrompt = ref<ChapterPrompt | null>(null)
const editedPromptText = ref('')
const loading = ref(false)
const errorMessage = ref<string | null>(null)

const getErrorMessage = (error: any, fallback: string) => {
  return error?.response?.data?.detail || error?.message || fallback
}

const fetchPrompts = async (preferredVersionNo?: number) => {
  if (!Number.isFinite(chapterNumber.value) || chapterNumber.value < 1) {
    errorMessage.value = '章节序号不合法'
    return
  }
  loading.value = true
  errorMessage.value = null
  try {
    const res = await api.get(`/projects/${projectId.value}/chapters/${chapterNumber.value}/prompts`)
    prompts.value = (res as unknown as ChapterPrompt[]).sort((a, b) => b.version_no - a.version_no)
    if (prompts.value.length > 0) {
      const nextPrompt = prompts.value.find(prompt => prompt.version_no === preferredVersionNo) || prompts.value[0]
      selectPrompt(nextPrompt)
    }
  } catch (e) {
    errorMessage.value = getErrorMessage(e, '加载提示词失败。')
    console.error('Failed to fetch prompts', e)
  } finally {
    loading.value = false
  }
}

const selectPrompt = (prompt: ChapterPrompt) => {
  currentPrompt.value = prompt
  editedPromptText.value = prompt.user_edited_prompt || prompt.generated_system_prompt
}

const savePrompt = async () => {
  if (!currentPrompt.value) return
  
  loading.value = true
  errorMessage.value = null
  try {
    await api.put(`/projects/${projectId.value}/chapters/${chapterNumber.value}/prompts/${currentPrompt.value.version_no}`, {
      edited_prompt: editedPromptText.value
    })
    await fetchPrompts(currentPrompt.value.version_no)
  } catch (e) {
    errorMessage.value = getErrorMessage(e, '保存提示词失败。')
    console.error('Failed to save prompt', e)
  } finally {
    loading.value = false
  }
}

const persistCurrentPromptIfNeeded = async () => {
  if (!currentPrompt.value) return currentPrompt.value
  const baseline = currentPrompt.value.user_edited_prompt || currentPrompt.value.generated_system_prompt
  if (editedPromptText.value === baseline) {
    return currentPrompt.value
  }

  await api.put(`/projects/${projectId.value}/chapters/${chapterNumber.value}/prompts/${currentPrompt.value.version_no}`, {
    edited_prompt: editedPromptText.value,
  })
  await fetchPrompts(currentPrompt.value.version_no)
  return currentPrompt.value
}

const approvePrompt = async () => {
  if (!currentPrompt.value) return
  
  loading.value = true
  errorMessage.value = null
  try {
    await persistCurrentPromptIfNeeded()
    await api.post(`/projects/${projectId.value}/chapters/${chapterNumber.value}/prompt/approve`, null, {
      params: {
        version_no: currentPrompt.value.version_no,
      },
    })
    router.push(`/projects/${projectId.value}/live`)
  } catch (e) {
    errorMessage.value = getErrorMessage(e, '批准提示词失败。')
    console.error('Failed to approve prompt', e)
  } finally {
    loading.value = false
  }
}

const generateNewVersion = async () => {
  loading.value = true
  errorMessage.value = null
  try {
    await api.post(`/projects/${projectId.value}/chapters/${chapterNumber.value}/prompt/generate`)
    await fetchPrompts()
  } catch (e) {
    errorMessage.value = getErrorMessage(e, '生成新提示词版本失败。')
    console.error('Failed to generate new prompt', e)
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  if (!Number.isFinite(chapterNumber.value) || chapterNumber.value < 1) {
    errorMessage.value = '章节序号不合法'
    return
  }
  fetchPrompts()
})

watch([projectId, chapterNumber], (newVals, oldVals) => {
  const [newPid, newCn] = newVals
  const [oldPid, oldCn] = oldVals
  if (newPid !== oldPid || newCn !== oldCn) {
    if (!Number.isFinite(newCn) || newCn < 1) {
      errorMessage.value = '章节序号不合法'
      return
    }
    errorMessage.value = null
    fetchPrompts()
  }
})
</script>

<template>
  <div class="mx-auto max-w-screen-xl">
    <div v-if="errorMessage" class="mb-6 rounded-md bg-red-50 p-4 text-sm text-red-700">
      {{ errorMessage }}
    </div>

    <div class="mb-6 flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
      <div>
        <h1 class="text-2xl font-bold text-gray-900">提示词编辑器</h1>
        <p class="mt-1 text-sm text-gray-500">第 {{ chapterNumber }} 章</p>
      </div>
      <div class="flex flex-wrap gap-3">
        <button @click="router.push(`/projects/${projectId}/live`)" class="btn btn-secondary">
          返回实时写作
        </button>
        <button @click="generateNewVersion" class="btn btn-secondary" :disabled="loading">
          生成新版本
        </button>
      </div>
    </div>

    <div class="grid gap-6 xl:h-[calc(100vh-12rem)] xl:grid-cols-[minmax(280px,0.95fr)_minmax(0,2.35fr)]">
      <!-- Left: Version History -->
      <div class="card flex flex-col xl:min-h-0">
        <div class="card-header bg-gray-50">
          <h3 class="font-medium">版本列表</h3>
        </div>
        <div class="card-body flex-1 overflow-y-auto p-0 xl:min-h-0">
          <ul class="divide-y divide-gray-200">
            <li 
              v-for="prompt in prompts" 
              :key="prompt.id || prompt.version_no"
              class="p-4 hover:bg-gray-50 cursor-pointer transition-colors"
              :class="currentPrompt?.version_no === prompt.version_no ? 'bg-primary-50 border-l-4 border-primary-500' : ''"
              @click="selectPrompt(prompt)"
            >
              <div class="flex justify-between items-center">
                <span class="font-medium text-sm">版本 {{ prompt.version_no }}</span>
                <span class="badge" :class="prompt.status === 'approved' ? 'badge-success' : 'badge-secondary'">
                  {{ prompt.status === 'approved' ? '已批准' : prompt.status === 'edited' ? '已编辑' : prompt.status === 'superseded' ? '已替换' : '已生成' }}
                </span>
              </div>
              <div class="text-xs text-gray-500 mt-1">
                {{ formatDateTime(prompt.created_at, '暂无时间') }}
              </div>
            </li>
          </ul>
          <div v-if="prompts.length === 0 && !loading" class="p-4 text-center text-gray-500 text-sm">
            暂未生成提示词。
          </div>
        </div>
      </div>

      <!-- Right: Editor -->
      <div class="card flex min-h-[420px] flex-col xl:min-h-0">
        <div class="card-header flex flex-col gap-3 bg-gray-50 sm:flex-row sm:items-center sm:justify-between">
          <h3 class="font-medium">
            {{ currentPrompt ? `正在编辑版本 ${currentPrompt.version_no}` : '请选择一个版本' }}
          </h3>
          <div v-if="currentPrompt" class="flex flex-wrap gap-2">
            <button @click="savePrompt" class="btn btn-sm btn-secondary" :disabled="loading">
              保存草稿
            </button>
            <button @click="approvePrompt" class="btn btn-sm btn-primary" :disabled="loading">
              批准并使用
            </button>
          </div>
        </div>
        <div class="card-body flex flex-1 flex-col p-0 xl:min-h-0">
          <textarea 
            v-if="currentPrompt"
            v-model="editedPromptText"
            class="min-h-[360px] flex-1 w-full resize-none border-0 p-4 font-mono text-sm focus:outline-none focus:ring-0 xl:min-h-0"
            placeholder="编辑本章的系统提示词..."
          ></textarea>
          <div v-else class="flex-1 flex items-center justify-center text-gray-500">
            请先从左侧选择一个提示词版本进行编辑。
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
