<script setup lang="ts">
import { computed, onMounted, onUnmounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useProjectStore } from '@/stores/project'
import ScoreBoard from '@/components/ScoreBoard.vue'
import LiveEventTimeline from '@/components/LiveEventTimeline.vue'
import ProjectResourcesPanel from '@/components/ProjectResourcesPanel.vue'
import { useProjectEvents } from '@/composables/useProjectEvents'
import { useLiveWritingState } from '@/composables/useLiveWritingState'

const route = useRoute()
const router = useRouter()
const projectStore = useProjectStore()
const projectId = computed(() => route.params.id as string)

const {
  events,
  connectionStatus,
  fetchHistoricalEvents,
  connect,
  disconnect,
  reset: resetEvents,
} = useProjectEvents(projectId)
const {
  chapters,
  currentChapter,
  currentReview,
  liveContent,
  retryingChapterNumber,
  rewritingChapterNumber,
  continuingChapterNumber,
  manualApprovingChapterNumber,
  resourceRefreshToken,
  generationActionLabel,
  generationActionClass,
  isPausePending,
  generationDisabled,
  isRetryableChapter,
  canContinueCurrentChapter,
  canManualApproveCurrentDraft,
  canRewriteFromCurrent,
  currentChapterNotice,
  currentChapterNoticeClass,
  refreshProjectState,
  handleProjectEvent,
  toggleGeneration,
  continueCurrentChapter,
  retryCurrentChapter,
  rewriteFromCurrentChapter,
  manualApproveCurrentChapter,
  selectChapter,
  reset: resetLiveWritingState,
} = useLiveWritingState({
  projectId,
  projectStore,
  refreshEvents: fetchHistoricalEvents,
})

const getStatusBadgeClass = (status?: string) => {
  switch (status) {
    case 'ready': return 'badge-info'
    case 'running': return 'badge-primary'
    case 'paused': return 'badge-warning'
    case 'completed': return 'badge-success'
    case 'failed': return 'badge-danger'
    default: return 'badge-secondary'
  }
}

const getProjectStatusLabel = (status?: string) => {
  switch (status) {
    case 'draft': return '草稿'
    case 'ready': return '就绪'
    case 'running': return '生成中'
    case 'paused': return '已暂停'
    case 'completed': return '已完成'
    case 'failed': return '失败'
    default: return status || '未知'
  }
}

const getChapterStatusLabel = (status?: string) => {
  switch (status) {
    case 'pending': return '待处理'
    case 'queued': return '排队中'
    case 'writing': return '写作中'
    case 'reviewing': return '评审中'
    case 'passed': return '通过'
    case 'failed': return '失败'
    case 'paused': return '暂停'
    default: return status || '未知'
  }
}


let initialized = false

const initProject = async () => {
  disconnect()
  resetEvents()
  resetLiveWritingState()
  projectStore.resetProjectScope()
  await refreshProjectState()
  await fetchHistoricalEvents()
  connect(handleProjectEvent)
}

watch(projectId, (newId, oldId) => {
  if (newId && newId !== oldId && initialized) {
    void initProject()
  }
})

onMounted(async () => {
  initialized = true
  await initProject()
})

onUnmounted(() => {
  disconnect()
  projectStore.resetProjectScope()
})

const editPrompt = () => {
  if (currentChapter.value) {
    router.push(`/projects/${projectId.value}/prompt/${currentChapter.value.chapter_number}`)
  }
}

const openProjectSetup = async () => {
  await router.push(`/projects/${projectId.value}`)
}

const openReader = async () => {
  await router.push(`/projects/${projectId.value}/read`)
}
</script>

<template>
  <div class="grid gap-6 xl:h-[calc(100vh-8rem)] xl:grid-cols-[minmax(280px,0.95fr)_minmax(0,1.8fr)_minmax(300px,1.05fr)]">
    <!-- 左侧：章节与状态 -->
    <div class="flex flex-col gap-4 xl:min-h-0">
      <div class="card flex-shrink-0">
        <div class="card-body">
          <h2 class="text-lg font-bold mb-2">{{ projectStore.currentProject?.title }}</h2>
          <div class="mb-3 flex flex-wrap gap-2">
            <button @click="openProjectSetup" class="btn btn-secondary btn-sm">
              项目配置
            </button>
            <button @click="openReader" class="btn btn-primary btn-sm">
              阅读 / 导出
            </button>
          </div>
          <div class="mb-4 flex flex-wrap items-center justify-between gap-3">
            <span :class="['badge', getStatusBadgeClass(projectStore.currentProject?.status)]" role="status">
              {{ isPausePending ? '停止中' : getProjectStatusLabel(projectStore.currentProject?.status) }}
            </span>
            <button
              @click="toggleGeneration"
              class="btn btn-sm"
              :class="generationActionClass"
              :disabled="generationDisabled"
              :aria-label="generationActionLabel"
            >
              {{ generationActionLabel }}
            </button>
          </div>
          <div class="text-sm text-gray-600" aria-live="polite">
            进度：{{ projectStore.currentProject?.current_chapter }} / {{ projectStore.currentProject?.total_chapters }}
          </div>
          <div v-if="projectStore.currentProject?.generation_mode === 'rush'" class="mt-3 rounded-md bg-blue-50 px-3 py-2 text-xs text-blue-700" role="note">
            当前项目已开启 Rush 极速创作模式。系统只调用 Writer，生成完成后直接通过。
          </div>
          <div v-if="projectStore.currentProject?.generation_mode !== 'rush' && projectStore.currentProject?.hard_review_gates_enabled === false" class="mt-3 rounded-md bg-amber-50 px-3 py-2 text-xs text-amber-800" role="alert">
            当前项目已关闭"严格评审硬门槛"。critic 仍会输出评分和问题列表，但会按整体质量综合判断是否通过。
          </div>
          <div v-if="projectStore.currentProject?.generation_mode !== 'rush' && projectStore.currentProject?.auto_accept_critic_failed" class="mt-3 rounded-md bg-blue-50 px-3 py-2 text-xs text-blue-700" role="alert">
            当前项目已开启"评审未通过时自动放行"。critic 评分仍会展示，但失败章节不会自动进入重写。
          </div>
          <div v-if="projectStore.currentProject?.last_error" class="mt-3 rounded-md bg-amber-50 px-3 py-2 text-xs text-amber-800" role="alert">
            {{ projectStore.currentProject.last_error }}
          </div>
          <div v-if="projectStore.currentProject?.status === 'paused'" class="mt-3 rounded-md bg-blue-50 px-3 py-2 text-xs text-blue-700" role="note">
            顶部"继续"用于恢复项目流程；如果你只想补写当前章节，请使用正文区域里的"断点续写"。
          </div>
          <div v-if="connectionStatus === 'reconnecting'" class="mt-3 rounded-md bg-yellow-50 px-3 py-2 text-xs text-yellow-700" role="alert">
            实时连接中断，正在自动重连…
          </div>
          <div v-if="projectStore.error" class="mt-3 rounded-md bg-red-50 px-3 py-2 text-xs text-red-700" role="alert">
            {{ projectStore.error }}
          </div>
        </div>
      </div>

      <div class="card flex flex-col overflow-hidden xl:min-h-0 xl:flex-1">
        <div class="card-header bg-gray-50 py-3">
          <h3 class="font-medium">章节列表</h3>
        </div>
        <div class="overflow-y-auto flex-1 p-2" role="listbox" aria-label="章节列表">
          <div
            v-for="chapter in chapters"
            :key="chapter.chapter_number"
            class="p-3 mb-2 rounded border cursor-pointer hover:bg-gray-50 transition-colors"
            :class="currentChapter?.chapter_number === chapter.chapter_number ? 'border-primary-500 bg-primary-50' : 'border-gray-200'"
            role="option"
            :aria-selected="currentChapter?.chapter_number === chapter.chapter_number"
            tabindex="0"
            @click="selectChapter(chapter)"
            @keydown.enter="selectChapter(chapter)"
          >
            <div class="flex justify-between items-center">
              <span class="font-medium">第 {{ chapter.chapter_number }} 章</span>
              <span
                class="text-xs px-2 py-1 rounded-full"
                :class="chapter.status === 'failed' ? 'bg-red-100 text-red-700' : chapter.status === 'passed' ? 'bg-green-100 text-green-700' : 'bg-gray-100 text-gray-600'"
              >
                {{ getChapterStatusLabel(chapter.status) }}
              </span>
            </div>
            <div v-if="chapter.retry_count > 0" class="text-xs text-orange-500 mt-1">
              重试次数：{{ chapter.retry_count }}
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- 中间：实时写作 -->
    <div class="card flex min-h-[420px] flex-col xl:min-h-0">
      <div class="card-header flex flex-col gap-3 bg-gray-50 sm:flex-row sm:items-center sm:justify-between">
        <h3 class="font-medium">
          {{ currentChapter ? `第 ${currentChapter.chapter_number} 章正文` : '请选择章节' }}
        </h3>
        <div v-if="currentChapter" class="flex flex-wrap gap-2">
          <button
            v-if="canContinueCurrentChapter"
            @click="continueCurrentChapter"
            class="btn btn-primary text-xs py-1 px-2"
            :disabled="continuingChapterNumber === currentChapter.chapter_number || projectStore.loading"
          >
            {{ continuingChapterNumber === currentChapter.chapter_number ? '续写中...' : '断点续写' }}
          </button>
          <button
            v-if="isRetryableChapter"
            @click="retryCurrentChapter"
            class="btn btn-warning text-xs py-1 px-2"
            :disabled="retryingChapterNumber === currentChapter.chapter_number || projectStore.loading"
          >
            {{ retryingChapterNumber === currentChapter.chapter_number ? '重试中...' : '重试本章节' }}
          </button>
          <button
            v-if="canRewriteFromCurrent"
            @click="rewriteFromCurrentChapter"
            class="btn btn-danger text-xs py-1 px-2"
            :disabled="rewritingChapterNumber === currentChapter.chapter_number || projectStore.loading"
          >
            {{ rewritingChapterNumber === currentChapter.chapter_number ? '重写中...' : '从本章重写' }}
          </button>
          <button
            v-if="canManualApproveCurrentDraft"
            @click="manualApproveCurrentChapter"
            class="btn btn-success text-xs py-1 px-2"
            :disabled="manualApprovingChapterNumber === currentChapter.chapter_number || projectStore.loading"
          >
            {{ manualApprovingChapterNumber === currentChapter.chapter_number ? '正在人工通过...' : '人工通过当前草稿' }}
          </button>
          <button @click="editPrompt" class="btn btn-secondary text-xs py-1 px-2">
            编辑提示词
          </button>
        </div>
      </div>
      <div v-if="currentChapterNotice" class="border-b px-6 py-3 text-sm" :class="currentChapterNoticeClass">
        {{ currentChapterNotice }}
      </div>
      <div class="card-body flex-1 overflow-y-auto bg-white p-6 font-serif whitespace-pre-wrap leading-relaxed text-gray-800 xl:min-h-0" aria-live="polite">
        {{ liveContent || '等待开始生成...' }}
      </div>
    </div>

    <!-- 右侧：评审、资源与事件 -->
    <div class="flex flex-col gap-4 xl:min-h-0">
      <div class="card flex flex-col overflow-hidden xl:min-h-0 xl:flex-1">
        <div class="card-header bg-gray-50 py-3">
          <h3 class="font-medium">监督评审</h3>
        </div>
        <div class="overflow-y-auto flex-1 p-4">
          <ScoreBoard :review="currentReview" />
        </div>
      </div>

      <ProjectResourcesPanel
        :project-id="projectId"
        :current-chapter-number="currentChapter?.chapter_number"
        :refresh-token="resourceRefreshToken"
        @resources-updated="refreshProjectState(currentChapter?.chapter_number)"
      />

      <div class="card flex h-80 flex-col overflow-hidden xl:h-auto xl:min-h-0 xl:flex-1">
        <div class="card-header bg-gray-50 py-2">
          <h3 class="font-medium text-sm">实时事件</h3>
        </div>
        <LiveEventTimeline :events="events" />
      </div>
    </div>
  </div>
</template>
