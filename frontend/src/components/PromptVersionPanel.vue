<script setup lang="ts">
import type { ChapterPrompt } from '@/types'
import { formatDateTime } from '@/utils/datetime'

defineProps<{
  prompts: ChapterPrompt[]
  currentPromptId?: string | number
}>()

defineEmits<{
  (e: 'select', prompt: ChapterPrompt): void
}>()
</script>

<template>
  <div class="flex flex-col h-full">
    <div class="bg-gray-50 px-4 py-3 border-b border-gray-200">
      <h3 class="text-sm font-medium text-gray-900">提示词版本</h3>
    </div>
    <div class="flex-1 overflow-y-auto p-2">
      <ul class="space-y-2">
        <li 
          v-for="prompt in prompts" 
          :key="prompt.id || prompt.version_no"
          class="p-3 rounded-md border cursor-pointer transition-colors"
          :class="currentPromptId === prompt.id || currentPromptId === prompt.version_no ? 'bg-primary-50 border-primary-300' : 'bg-white border-gray-200 hover:border-primary-300'"
          @click="$emit('select', prompt)"
        >
          <div class="flex justify-between items-center mb-1">
            <span class="font-medium text-sm">版本 {{ prompt.version_no }}</span>
            <span class="badge text-[10px]" :class="prompt.status === 'approved' ? 'badge-success' : 'badge-secondary'">
              {{ prompt.status }}
            </span>
          </div>
          <div class="text-xs text-gray-500">
            {{ formatDateTime(prompt.created_at, '暂无时间') }}
          </div>
        </li>
      </ul>
      <div v-if="prompts.length === 0" class="text-center py-4 text-sm text-gray-500">
        暂未生成提示词。
      </div>
    </div>
  </div>
</template>
