<script setup lang="ts">
import type { Chapter } from '@/types'

defineProps<{
  chapters: Chapter[]
  currentChapterId?: string | number
}>()

defineEmits<{
  (e: 'select', chapterNumber: number): void
}>()

const getStatusColor = (status: string) => {
  switch (status) {
    case 'passed': return 'bg-green-500'
    case 'failed': return 'bg-red-500'
    case 'writing': return 'bg-blue-500 animate-pulse'
    case 'reviewing': return 'bg-purple-500 animate-pulse'
    case 'queued': return 'bg-yellow-500'
    case 'paused': return 'bg-orange-500'
    default: return 'bg-gray-300'
  }
}
</script>

<template>
  <div class="flex flex-wrap gap-2">
    <div 
      v-for="chapter in chapters" 
      :key="chapter.id || chapter.chapter_number"
      class="relative group cursor-pointer"
      @click="$emit('select', chapter.chapter_number)"
    >
      <div 
        class="w-8 h-8 rounded-full flex items-center justify-center text-xs font-bold text-white transition-all"
        :class="[
          getStatusColor(chapter.status),
          currentChapterId === chapter.id || currentChapterId === chapter.chapter_number ? 'ring-4 ring-primary-200 scale-110' : 'hover:scale-105'
        ]"
      >
        {{ chapter.chapter_number }}
      </div>
      
      <!-- Tooltip -->
      <div class="absolute bottom-full left-1/2 transform -translate-x-1/2 mb-2 hidden group-hover:block z-10">
        <div class="bg-gray-900 text-white text-xs rounded py-1 px-2 whitespace-nowrap">
          第 {{ chapter.chapter_number }} 章<br>
          状态：{{ chapter.status }}<br>
          重试：{{ chapter.retry_count }} 次
        </div>
        <div class="w-2 h-2 bg-gray-900 transform rotate-45 absolute -bottom-1 left-1/2 -translate-x-1/2"></div>
      </div>
    </div>
  </div>
</template>
