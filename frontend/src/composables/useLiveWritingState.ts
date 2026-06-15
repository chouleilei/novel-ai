import { computed, ref, type ComputedRef } from 'vue'
import api from '@/api'
import { useProjectStore } from '@/stores/project'
import type { Chapter, ChapterAttempt, ChapterReview } from '@/types'
import type { ProjectEvent } from '@/types/projectEvents'
import { getContentChunk, shouldRefreshProjectState, shouldRefreshResources } from '@/utils/projectEvents'

interface UseLiveWritingStateOptions {
  projectId: ComputedRef<string>
  projectStore: ReturnType<typeof useProjectStore>
  refreshEvents: () => Promise<void>
}

export const useLiveWritingState = ({
  projectId,
  projectStore,
  refreshEvents,
}: UseLiveWritingStateOptions) => {
  const chapters = ref<Chapter[]>([])
  const currentChapter = ref<Chapter | null>(null)
  const currentAttempt = ref<ChapterAttempt | null>(null)
  const currentReview = ref<ChapterReview | null>(null)
  const liveContent = ref('')
  const retryingChapterNumber = ref<number | null>(null)
  const rewritingChapterNumber = ref<number | null>(null)
  const continuingChapterNumber = ref<number | null>(null)
  const manualApprovingChapterNumber = ref<number | null>(null)
  const resourceRefreshToken = ref(0)
  let fetchAbortController: AbortController | null = null

  const isPausePending = computed(() => {
    if (projectStore.currentProject?.status !== 'paused') {
      return false
    }
    return chapters.value.some(chapter => ['writing', 'reviewing'].includes(chapter.status))
  })

  const generationActionLabel = computed(() => {
    if (isPausePending.value) {
      return '停止中'
    }
    switch (projectStore.currentProject?.status) {
      case 'running':
        return '暂停'
      case 'paused':
        return '继续'
      case 'completed':
        return '已完成'
      case 'draft':
        return '需先配置'
      default:
        return '开始'
    }
  })

  const generationActionClass = computed(() => {
    return projectStore.currentProject?.status === 'running' ? 'btn-warning' : 'btn-success'
  })

  const generationDisabled = computed(() => {
    const status = projectStore.currentProject?.status
    return !status || projectStore.loading || isPausePending.value || ['draft', 'completed'].includes(status)
  })

  const hasCurrentChapterActiveGenerationJob = computed(() => {
    return currentChapter.value?.has_active_generation_job === true
  })

  const hasAnyActiveGenerationJob = computed(() => {
    return chapters.value.some(chapter => chapter.has_active_generation_job === true)
  })

  const isRetryableChapter = computed(() => {
    return currentChapter.value != null
      && ['failed', 'paused', 'passed'].includes(currentChapter.value.status)
      && !hasAnyActiveGenerationJob.value
  })

  const isManualApprovedDraft = computed(() => {
    if (!currentChapter.value || !currentAttempt.value || !currentReview.value) {
      return false
    }
    return (
      currentChapter.value.status === 'passed'
      && currentAttempt.value.id === currentChapter.value.accepted_attempt_id
      && currentReview.value.passed === false
    )
  })

  const isAutoAcceptedDraft = computed(() => {
    if (!currentChapter.value || !currentAttempt.value || !currentReview.value) {
      return false
    }
    return (
      currentChapter.value.status === 'passed'
      && currentChapter.value.auto_accepted === true
      && currentAttempt.value.id === currentChapter.value.accepted_attempt_id
      && currentReview.value.passed === false
    )
  })

  const canManualApproveCurrentDraft = computed(() => {
    return currentChapter.value?.status === 'failed' && Boolean(currentAttempt.value?.content?.trim())
  })

  const canRewriteFromCurrent = computed(() => {
    return currentChapter.value != null
      && canRewriteFromChapter(currentChapter.value)
  })

  const canContinueCurrentChapter = computed(() => {
    return currentChapter.value != null
      && ['paused', 'failed', 'passed'].includes(currentChapter.value.status)
      && !hasAnyActiveGenerationJob.value
      && Boolean(currentAttempt.value?.content?.trim() || currentChapter.value.final_content?.trim())
  })

  const canRewriteFromChapter = (chapter: Chapter | null) => {
    return chapter != null
      && chapter.status !== 'pending'
      && projectStore.currentProject?.status !== 'running'
      && !hasAnyActiveGenerationJob.value
  }

  const currentChapterNotice = computed(() => {
    const chapter = currentChapter.value
    if (!chapter) {
      return ''
    }
    if (isPausePending.value && ['writing', 'reviewing'].includes(chapter.status)) {
      return '正在停止当前章节，已生成的内容会尽量保留。'
    }
    if (hasAnyActiveGenerationJob.value && ['failed', 'paused', 'passed'].includes(chapter.status)) {
      return '本项目已有章节级“断点续写”或“重试本章节”任务在排队或执行中；同一时间只允许一个，请等待当前任务完成后再操作。'
    }
    if (chapter.status === 'paused') {
      return chapter.last_error || '当前章节已停止，已保留已有内容。你可以使用正文区的“断点续写”继续补写当前章节；若要整章重写，请使用“重试本章节”或“从本章重写”。'
    }
    if (chapter.status === 'passed' && canContinueCurrentChapter.value) {
      return '当前章节已完成；你可以使用“断点续写”基于现有正文继续补写，或用“重试本章节”清空本章后重新完整生成。两者都不会自动重置后续章节。'
    }
    if (chapter.status === 'writing') {
      return '系统正在自动生成正文，内容会持续刷新到中间面板。'
    }
    if (chapter.status === 'reviewing') {
      return '正文已生成，监督 AI 正在根据大纲与提示词进行评审。'
    }
    if (chapter.status === 'queued' && currentReview.value?.passed === false) {
      return '上一轮评审未通过，已进入重写队列。右侧保留失败原因，便于你对照修改提示词或决定是否手动重试。'
    }
    if (chapter.status === 'failed') {
      return chapter.last_error || chapter.improvement_notes || '本章未通过评审；如果当前草稿可用，你也可以使用“断点续写”继续补写本章。'
    }
    if (isManualApprovedDraft.value) {
      return '当前终稿来自人工放行，监督评分未通过，仅供参考。'
    }
    if (isAutoAcceptedDraft.value) {
      return '当前终稿因项目已开启自动放行而被接受，监督评分未通过，仅供参考。'
    }
    return ''
  })

  const currentChapterNoticeClass = computed(() => {
    const chapter = currentChapter.value
    if (!chapter) {
      return 'border-gray-200 bg-gray-50 text-gray-600'
    }
    if (isPausePending.value && ['writing', 'reviewing'].includes(chapter.status)) {
      return 'border-orange-200 bg-orange-50 text-orange-700'
    }
    if (hasCurrentChapterActiveGenerationJob.value && ['failed', 'paused'].includes(chapter.status)) {
      return 'border-blue-200 bg-blue-50 text-blue-700'
    }
    if (chapter.status === 'paused') {
      return 'border-yellow-200 bg-yellow-50 text-yellow-800'
    }
    if (chapter.status === 'failed') {
      return 'border-red-200 bg-red-50 text-red-700'
    }
    if (chapter.status === 'queued' && currentReview.value?.passed === false) {
      return 'border-amber-200 bg-amber-50 text-amber-800'
    }
    if (isManualApprovedDraft.value || isAutoAcceptedDraft.value) {
      return 'border-amber-200 bg-amber-50 text-amber-800'
    }
    if (['writing', 'reviewing'].includes(chapter.status)) {
      return 'border-blue-200 bg-blue-50 text-blue-700'
    }
    return 'border-gray-200 bg-gray-50 text-gray-600'
  })

  const mergeChapter = (chapterDetail: Chapter) => {
    const index = chapters.value.findIndex(item => item.chapter_number === chapterDetail.chapter_number)
    if (index >= 0) {
      chapters.value[index] = {
        ...chapters.value[index],
        ...chapterDetail,
      }
      return chapters.value[index]
    }

    chapters.value.push(chapterDetail)
    chapters.value.sort((a, b) => a.chapter_number - b.chapter_number)
    return chapterDetail
  }

  const resolveCurrentAttempt = (chapterDetail: Chapter, attempts: ChapterAttempt[]) => {
    const latestAttempt = attempts[attempts.length - 1] || null
    if (!latestAttempt) {
      return null
    }
    if (chapterDetail.status === 'writing' && latestAttempt.status !== 'running') {
      return null
    }
    return latestAttempt
  }

  const resolveLiveContent = (chapterDetail: Chapter, attempt: ChapterAttempt | null) => {
    if (attempt) {
      return attempt.content || chapterDetail.final_content || ''
    }
    if (chapterDetail.status === 'queued') {
      return chapterDetail.final_content || ''
    }
    if (chapterDetail.status === 'writing') {
      return ''
    }
    return chapterDetail.final_content || ''
  }

  const resolveTargetChapter = (preferredChapterNumber?: number | null) => {
    if (preferredChapterNumber) {
      const preferred = chapters.value.find(chapter => chapter.chapter_number === preferredChapterNumber)
      if (preferred) {
        return preferred
      }
    }

    const current = currentChapter.value
      ? chapters.value.find(chapter => chapter.chapter_number === currentChapter.value?.chapter_number)
      : null
    if (current) {
      return current
    }

    const active = chapters.value.find(chapter => ['writing', 'reviewing', 'failed', 'paused', 'queued'].includes(chapter.status))
    if (active) {
      return active
    }

    if (projectStore.currentProject?.current_chapter) {
      const latest = chapters.value.find(chapter => chapter.chapter_number === projectStore.currentProject?.current_chapter)
      if (latest) {
        return latest
      }
    }

    return chapters.value[chapters.value.length - 1] || null
  }

  const cancelPendingFetch = () => {
    if (fetchAbortController) {
      fetchAbortController.abort()
      fetchAbortController = null
    }
  }

  const reset = () => {
    cancelPendingFetch()
    chapters.value = []
    currentChapter.value = null
    currentAttempt.value = null
    currentReview.value = null
    liveContent.value = ''
    retryingChapterNumber.value = null
    rewritingChapterNumber.value = null
    continuingChapterNumber.value = null
    manualApprovingChapterNumber.value = null
    resourceRefreshToken.value = 0
  }

  const fetchChapterDetails = async (chapterNumber: number) => {
    cancelPendingFetch()
    const controller = new AbortController()
    fetchAbortController = controller
    try {
      const selectedChapter = chapters.value.find(chapter => chapter.chapter_number === chapterNumber) || null
      if (selectedChapter) {
        currentChapter.value = selectedChapter
      }

      const [chapterResponse, attemptsResponse] = await Promise.all([
        api.get(`/projects/${projectId.value}/chapters/${chapterNumber}`, { signal: controller.signal }),
        api.get(`/projects/${projectId.value}/chapters/${chapterNumber}/attempts`, { signal: controller.signal }),
      ])

      const chapterDetail = mergeChapter(chapterResponse as unknown as Chapter)
      currentChapter.value = chapterDetail

      const attempts = attemptsResponse as unknown as ChapterAttempt[]
      currentAttempt.value = resolveCurrentAttempt(chapterDetail, attempts)
      liveContent.value = resolveLiveContent(chapterDetail, currentAttempt.value)

      const latestReviewedAttempt = [...attempts].reverse().find(item => ['reviewed', 'accepted', 'rejected'].includes(item.status)) || null
      const reviewAttempt = currentAttempt.value ?? latestReviewedAttempt

      if (reviewAttempt) {
        const reviewsResponse = await api.get(`/projects/${projectId.value}/chapters/${chapterNumber}/reviews`, { signal: controller.signal })
        const reviews = reviewsResponse as unknown as ChapterReview[]
        currentReview.value = reviews.find(review => review.attempt_id === reviewAttempt.id) || reviews[reviews.length - 1] || null
        return
      }

      currentReview.value = null
    } catch (error: any) {
      if (error?.name === 'CanceledError' || error?.name === 'AbortError') {
        return
      }
      console.error('Failed to fetch chapter details', error)
    } finally {
      if (fetchAbortController === controller) {
        fetchAbortController = null
      }
    }
  }

  const fetchChapters = async (preferredChapterNumber?: number | null) => {
    try {
      const response = await api.get(`/projects/${projectId.value}/chapters`)
      chapters.value = response as unknown as Chapter[]

      const targetChapter = resolveTargetChapter(preferredChapterNumber)
      if (!targetChapter) {
        currentChapter.value = null
        currentAttempt.value = null
        currentReview.value = null
        liveContent.value = ''
        return
      }

      currentChapter.value = targetChapter
      await fetchChapterDetails(targetChapter.chapter_number)
    } catch (error) {
      console.error('Failed to fetch chapters', error)
    }
  }

  const refreshResources = () => {
    resourceRefreshToken.value += 1
  }

  const refreshProjectState = async (preferredChapterNumber?: number | null) => {
    await projectStore.fetchProject(projectId.value)
    await fetchChapters(preferredChapterNumber)
  }

  const handleProjectEvent = async (event: ProjectEvent) => {
    if (event.type === 'content_chunk' && event.chapter_number === currentChapter.value?.chapter_number) {
      liveContent.value += getContentChunk(event)
      return
    }

    if (event.type === 'chapter_writing' && event.chapter_number === currentChapter.value?.chapter_number) {
      currentAttempt.value = null
      currentReview.value = null
      const data = event.data as { continuation?: boolean, saved_chars?: number }
      if (data.continuation !== true) {
        liveContent.value = ''
      }
    }

    if (event.type === 'chapter_truncated' && event.chapter_number === currentChapter.value?.chapter_number) {
      currentReview.value = null
    }

    if (event.type === 'chapter_paused' && event.chapter_number === currentChapter.value?.chapter_number) {
      currentReview.value = null
    }

    if (shouldRefreshResources(event.type)) {
      refreshResources()
    }

    if (event.type === 'writer_non_stream_succeeded' && event.chapter_number === currentChapter.value?.chapter_number && event.chapter_number != null) {
      await fetchChapterDetails(event.chapter_number)
      return
    }

    if (shouldRefreshProjectState(event.type)) {
      await refreshProjectState(event.chapter_number ?? currentChapter.value?.chapter_number)
    }
  }

  const toggleGeneration = async () => {
    if (generationDisabled.value) {
      return
    }

    if (projectStore.currentProject?.status === 'running') {
      await projectStore.pauseGeneration(projectId.value)
    } else if (projectStore.currentProject?.status === 'paused') {
      await projectStore.resumeGeneration(projectId.value)
    } else {
      await projectStore.startGeneration(projectId.value)
    }

    await refreshProjectState(currentChapter.value?.chapter_number)
  }

  const retryCurrentChapter = async () => {
    if (!currentChapter.value || retryingChapterNumber.value != null) {
      return
    }

    const confirmed = window.confirm(
      `确定重试第 ${currentChapter.value.chapter_number} 章吗？系统会清空本章当前正文并重新完整生成本章，后续章节保持不变。`
    )
    if (!confirmed) {
      return
    }

    retryingChapterNumber.value = currentChapter.value.chapter_number
    try {
      await projectStore.retryChapter(projectId.value, currentChapter.value.chapter_number)
      await refreshEvents()
      await refreshProjectState(currentChapter.value.chapter_number)
      refreshResources()
    } finally {
      retryingChapterNumber.value = null
    }
  }

  const continueCurrentChapter = async () => {
    if (!currentChapter.value || continuingChapterNumber.value != null || !canContinueCurrentChapter.value) {
      return
    }

    const hasLaterGeneratedChapters = chapters.value.some(chapter => {
      return chapter.chapter_number > currentChapter.value!.chapter_number
        && ['passed', 'failed', 'paused', 'reviewing', 'writing', 'queued'].includes(chapter.status)
    })
    const confirmed = window.confirm(
      hasLaterGeneratedChapters
        ? `确定对第 ${currentChapter.value.chapter_number} 章执行断点续写吗？系统会基于当前已保存正文继续补写本章，后续章节保持不变。`
        : `确定对第 ${currentChapter.value.chapter_number} 章执行断点续写吗？系统会基于当前已保存正文继续补写本章。`
    )
    if (!confirmed) {
      return
    }

    continuingChapterNumber.value = currentChapter.value.chapter_number
    try {
      await projectStore.continueChapter(projectId.value, currentChapter.value.chapter_number)
      await refreshEvents()
      await refreshProjectState(currentChapter.value.chapter_number)
      refreshResources()
    } finally {
      continuingChapterNumber.value = null
    }
  }

  const manualApproveCurrentChapter = async () => {
    if (!currentChapter.value || manualApprovingChapterNumber.value != null || !canManualApproveCurrentDraft.value) {
      return
    }

    const confirmed = window.confirm(`确定将第 ${currentChapter.value.chapter_number} 章当前草稿人工确认为终稿吗？系统会继续执行记忆更新和后续章节推进流程。`)
    if (!confirmed) {
      return
    }

    manualApprovingChapterNumber.value = currentChapter.value.chapter_number
    try {
      await projectStore.manualApproveChapter(projectId.value, currentChapter.value.chapter_number)
      await refreshEvents()
      await refreshProjectState(currentChapter.value.chapter_number)
      refreshResources()
    } finally {
      manualApprovingChapterNumber.value = null
    }
  }

  const rewriteFromChapter = async (chapter: Chapter | null) => {
    if (!chapter || rewritingChapterNumber.value != null || !canRewriteFromChapter(chapter)) {
      return
    }

    const confirmed = window.confirm(
      `确定从第 ${chapter.chapter_number} 章开始重写后续内容吗？系统会删除本章及之后的正文、评分、提示词、摘要与资源修订记录，并立即从本章重新开始生成。`
    )
    if (!confirmed) {
      return
    }

    rewritingChapterNumber.value = chapter.chapter_number
    try {
      await projectStore.rewriteFromChapter(projectId.value, chapter.chapter_number)
      await refreshEvents()
      await refreshProjectState(chapter.chapter_number)
      refreshResources()
    } finally {
      rewritingChapterNumber.value = null
    }
  }

  const rewriteFromCurrentChapter = async () => {
    await rewriteFromChapter(currentChapter.value)
  }

  const selectChapter = async (chapter: Chapter) => {
    currentChapter.value = chapter
    await fetchChapterDetails(chapter.chapter_number)
  }

  return {
    chapters,
    currentChapter,
    currentAttempt,
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
    isManualApprovedDraft,
    isAutoAcceptedDraft,
    canManualApproveCurrentDraft,
    canRewriteFromCurrent,
    canRewriteFromChapter,
    currentChapterNotice,
    currentChapterNoticeClass,
    fetchChapters,
    fetchChapterDetails,
    refreshProjectState,
    handleProjectEvent,
    toggleGeneration,
    continueCurrentChapter,
    retryCurrentChapter,
    rewriteFromChapter,
    rewriteFromCurrentChapter,
    manualApproveCurrentChapter,
    selectChapter,
    reset,
  }
}
