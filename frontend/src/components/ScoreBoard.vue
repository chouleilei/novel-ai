<script setup lang="ts">
import type { ChapterReview } from '@/types'

defineProps<{
  review: ChapterReview | null
}>()

const getScoreColor = (score?: number) => {
  if (score == null) return 'text-gray-400'
  if (score >= 8) return 'text-green-600'
  if (score >= 6) return 'text-yellow-600'
  return 'text-red-600'
}
</script>

<template>
  <div v-if="review" class="space-y-4">
    <div class="flex flex-col gap-3 border-b pb-2 sm:flex-row sm:items-center sm:justify-between">
      <span class="font-bold text-lg">综合评分：<span :class="getScoreColor(review.overall_score)">{{ review.overall_score }}/10</span></span>
      <span :class="review.passed ? 'bg-green-100 text-green-800' : 'bg-red-100 text-red-800'" class="px-3 py-1 rounded-full text-sm font-bold">
        {{ review.passed ? '通过' : '未通过' }}
      </span>
    </div>

    <div class="grid grid-cols-1 gap-4 text-sm sm:grid-cols-2">
      <div class="bg-gray-50 p-3 rounded">
        <div class="text-gray-500 mb-1">大纲遵循</div>
        <div class="font-bold text-lg" :class="getScoreColor(review.outline_score)">{{ review.outline_score }}/10</div>
      </div>
      <div class="bg-gray-50 p-3 rounded">
        <div class="text-gray-500 mb-1">提示词遵循</div>
        <div class="font-bold text-lg" :class="getScoreColor(review.instruction_score)">{{ review.instruction_score }}/10</div>
      </div>
      <div class="bg-gray-50 p-3 rounded">
        <div class="text-gray-500 mb-1">连续性</div>
        <div class="font-bold text-lg" :class="getScoreColor(review.continuity_score)">{{ review.continuity_score }}/10</div>
      </div>
      <div class="bg-gray-50 p-3 rounded">
        <div class="text-gray-500 mb-1">人物一致性</div>
        <div class="font-bold text-lg" :class="getScoreColor(review.character_score)">{{ review.character_score }}/10</div>
      </div>
      <div class="rounded bg-gray-50 p-3 sm:col-span-2">
        <div class="text-gray-500 mb-1">文笔质量</div>
        <div class="font-bold text-lg" :class="getScoreColor(review.writing_score)">{{ review.writing_score }}/10</div>
      </div>
    </div>

    <div v-if="review.blocking_issues?.length" class="mt-4 bg-red-50 p-3 rounded border border-red-100">
      <h4 class="font-medium text-red-800 text-sm mb-2 flex items-center">
        <svg class="w-4 h-4 mr-1" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"></path></svg>
        阻断问题
      </h4>
      <ul class="list-disc pl-5 text-sm text-red-700 space-y-1">
        <li v-for="(issue, i) in review.blocking_issues" :key="i">{{ issue }}</li>
      </ul>
    </div>

    <div v-if="review.violated_instructions?.length" class="mt-4 bg-orange-50 p-3 rounded border border-orange-100">
      <h4 class="font-medium text-orange-800 text-sm mb-2 flex items-center">
        <svg class="w-4 h-4 mr-1" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"></path></svg>
        违反要求
      </h4>
      <ul class="list-disc pl-5 text-sm text-orange-700 space-y-1">
        <li v-for="(issue, i) in review.violated_instructions" :key="i">{{ issue }}</li>
      </ul>
    </div>

    <div v-if="review.improvement_suggestions?.length" class="mt-4 bg-yellow-50 p-3 rounded border border-yellow-100">
      <h4 class="font-medium text-yellow-800 text-sm mb-2 flex items-center">
        <svg class="w-4 h-4 mr-1" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"></path></svg>
        改进建议
      </h4>
      <ul class="list-disc pl-5 text-sm text-yellow-700 space-y-1">
        <li v-for="(sug, i) in review.improvement_suggestions" :key="i">{{ sug }}</li>
      </ul>
    </div>
  </div>
  <div v-else class="text-center py-8 text-gray-500">
    <svg class="mx-auto h-12 w-12 text-gray-300 mb-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"></path></svg>
    <p>暂时还没有评审结果。</p>
    <p class="text-xs mt-1">章节写作完成后，监督模型会自动进行评估。</p>
  </div>
</template>
