import type { ProjectEvent, ProjectEventType } from '@/types/projectEvents'
import { PROJECT_EVENT_TYPES } from '@/types/projectEvents'

export const STREAM_PROJECT_EVENT_TYPES = PROJECT_EVENT_TYPES

const RESOURCE_REFRESH_EVENT_TYPES = new Set<string>([
  'memory_updated',
  'chapter_passed',
  'chapter_rewriting',
])

const PROJECT_REFRESH_EVENT_TYPES = new Set<string>([
  'chapter_writing',
  'chapter_truncated',
  'chapter_reviewing',
  'chapter_passed',
  'chapter_paused',
  'chapter_rewriting',
  'job_failed',
  'project_paused',
  'pipeline_complete',
  'writer_non_stream_started',
  'writer_stream_no_text_fallback',
])

const toRecord = (value: unknown): Record<string, unknown> => {
  if (typeof value === 'object' && value !== null && !Array.isArray(value)) {
    return { ...(value as Record<string, unknown>) }
  }
  return {}
}

const getNumber = (value: unknown, fallback = 0) => {
  if (typeof value === 'number' && Number.isFinite(value)) {
    return value
  }
  if (typeof value === 'string') {
    const parsed = Number(value)
    if (Number.isFinite(parsed)) {
      return parsed
    }
  }
  return fallback
}

const getNullableNumber = (value: unknown): number | null => {
  if (value == null) {
    return null
  }
  return getNumber(value, Number.NaN)
}

const getString = (value: unknown) => {
  return typeof value === 'string' ? value : ''
}

const getStringArray = (value: unknown) => {
  if (!Array.isArray(value)) {
    return []
  }
  return value.filter((item): item is string => typeof item === 'string' && item.trim().length > 0)
}

const getBoolean = (value: unknown) => {
  return value === true
}

const getEventData = (event: ProjectEvent) => toRecord(event.data)

export const isProjectEventType = (value: string): value is ProjectEventType => {
  return (PROJECT_EVENT_TYPES as readonly string[]).includes(value)
}

export const normalizeProjectEvent = (payload: unknown): ProjectEvent => {
  const source = toRecord(payload)
  const type = getString(source.type) || getString(source.event_type) || 'unknown'
  const chapterNumber = getNullableNumber(source.chapter_number)

  return {
    id: getNumber(source.id, 0),
    type,
    chapter_number: Number.isNaN(chapterNumber ?? Number.NaN) ? null : chapterNumber,
    data: toRecord(source.data ?? source.event_data),
    created_at: getString(source.created_at) || new Date().toISOString(),
  }
}

export const formatProjectEventText = (event: ProjectEvent) => {
  const data = getEventData(event)
  const chapterLabel = event.chapter_number ? `第 ${event.chapter_number} 章` : '项目'

  switch (event.type) {
    case 'pipeline_start':
      return `${chapterLabel}开始执行`
    case 'chapter_writing': {
      const attempt = getNullableNumber(data.attempt)
      if (getBoolean(data.continuation)) {
        const savedChars = getNullableNumber(data.saved_chars)
        const continuationSource = getString(data.continuation_source)
        const actionLabel = continuationSource === 'resume_saved_draft'
          ? '恢复已保存草稿并继续写作'
          : continuationSource === 'chapter_breakpoint_button'
            ? '按章节断点继续写作'
          : '从断点继续写作'
        return savedChars != null && !Number.isNaN(savedChars)
          ? `${chapterLabel}${actionLabel}（已保留 ${savedChars} 字）`
          : `${chapterLabel}${actionLabel}`
      }
      return attempt ? `${chapterLabel}开始写作（第 ${attempt} 次尝试）` : `${chapterLabel}开始写作`
    }
    case 'chapter_truncated': {
      const accumulatedChars = getNullableNumber(data.accumulated_chars)
      const reason = getString(data.reason)
      if (accumulatedChars != null && !Number.isNaN(accumulatedChars)) {
        return reason
          ? `${chapterLabel}检测到正文截断，已保留 ${accumulatedChars} 字并准备续写（${reason}）`
          : `${chapterLabel}检测到正文截断，已保留 ${accumulatedChars} 字并准备续写`
      }
      return `${chapterLabel}检测到正文截断，正在从断点继续`
    }
    case 'precheck_done':
      return `${chapterLabel}连续性预检查完成`
    case 'prompt_generated':
      return `${chapterLabel}已生成系统提示词`
    case 'prompt_generation_fallback':
      return `${chapterLabel}提示词生成超时，已切换为兜底提示词`
    case 'prompt_updated':
      return `${chapterLabel}提示词已更新`
    case 'content_chunk':
      return `${chapterLabel}正文更新中`
    case 'writer_non_stream_started':
      return `${chapterLabel}正在使用非流式写作，正文会在完成后一次性返回`
    case 'writer_non_stream_succeeded':
      return `${chapterLabel}非流式写作完成，已拿到完整正文`
    case 'writer_stream_no_text_fallback':
      return `${chapterLabel}流式长时间未返回正文，已回退到当前已拿到的内容`
    case 'writer_stream_fallback_started':
      return `${chapterLabel}流式输出异常，正在回退到非流式补全`
    case 'writer_stream_fallback_succeeded':
      return `${chapterLabel}已通过非流式补全正文`
    case 'chapter_reviewing':
      return `${chapterLabel}进入评审`
    case 'chapter_scored': {
      const score = data.score
      return `${chapterLabel}评审完成，得分 ${typeof score === 'number' ? score : '-'}`
    }
    case 'memory_updated': {
      const needsReviewCount = getNumber(data.needs_review_count, 0)
      if (needsReviewCount > 0) {
        return `${chapterLabel}记忆与资源已更新，发现 ${needsReviewCount} 条待审核高风险提案，生成继续进行`
      }
      return `${chapterLabel}记忆与资源已更新`
    }
    case 'chapter_passed':
      if (getBoolean(data.manual)) {
        return `${chapterLabel}已人工通过当前草稿`
      }
      if (getBoolean(data.auto_accepted)) {
        return `${chapterLabel}评审未通过，但已按项目设置自动放行`
      }
      return `${chapterLabel}通过评审`
    case 'chapter_rewriting':
      if (getString(data.reason) === 'review_failed') {
        const failureSummary = toRecord(data.failure_summary)
        const topBlockingIssues = getStringArray(failureSummary.top_blocking_issues)
        const overallScore = getNullableNumber(failureSummary.overall_score)
        const primaryIssue = topBlockingIssues[0]
        if (primaryIssue && overallScore != null && !Number.isNaN(overallScore)) {
          return `${chapterLabel}评审未通过（${overallScore.toFixed(1)} 分），已进入重写：${primaryIssue}`
        }
        if (primaryIssue) {
          return `${chapterLabel}评审未通过，已进入重写：${primaryIssue}`
        }
        if (overallScore != null && !Number.isNaN(overallScore)) {
          return `${chapterLabel}评审未通过（${overallScore.toFixed(1)} 分），已进入重写`
        }
      }
      return `${chapterLabel}已进入重写队列`
    case 'chapter_paused':
      return `${chapterLabel}已停止，保留当前内容`
    case 'job_failed': {
      const error = getString(data.error)
      return error ? `${chapterLabel}执行失败：${error}` : `${chapterLabel}执行失败`
    }
    case 'project_paused': {
      const phase = getString(data.phase)
      const reason = getString(data.reason)
      if (phase === 'requested') {
        return '已收到停止请求，正在等待当前步骤中断'
      }
      if (reason === 'memory_needs_review') {
        return '检测到高风险设定变更，提案已进入审核列表，后续生成不受阻塞'
      }
      if (reason === 'waiting_manual_resume') {
        return '当前章完成，等待手动继续'
      }
      if (reason === 'max_retries_exceeded') {
        return '达到最大重试次数，项目已暂停'
      }
      if (reason === 'writer_provider_unavailable') {
        return '写作模型不可用，请检查 API Key、模型权限或网关配置'
      }
      if (reason === 'critic_provider_unavailable') {
        return '评审模型不可用，请检查 API Key、模型权限或网关配置'
      }
      return '项目已暂停'
    }
    case 'pipeline_complete':
      return '全书生成完成'
    default:
      return `${chapterLabel}${event.type}`
  }
}

export const shouldRefreshResources = (eventType: string) => {
  return RESOURCE_REFRESH_EVENT_TYPES.has(eventType)
}

export const shouldRefreshProjectState = (eventType: string) => {
  return PROJECT_REFRESH_EVENT_TYPES.has(eventType)
}

export const getContentChunk = (event: ProjectEvent) => {
  const chunk = getEventData(event).chunk
  return typeof chunk === 'string' ? chunk : ''
}

export const compactTimelineEvents = (events: ProjectEvent[], contentChunkWindowMs = 60_000) => {
  const compacted: ProjectEvent[] = []

  for (const event of events) {
    if (event.type !== 'content_chunk') {
      compacted.push(event)
      continue
    }

    const previous = compacted[compacted.length - 1]
    if (previous?.type !== 'content_chunk' || previous.chapter_number !== event.chapter_number) {
      compacted.push(event)
      continue
    }

    const previousTime = new Date(previous.created_at).getTime()
    const currentTime = new Date(event.created_at).getTime()
    if (Number.isNaN(previousTime) || Number.isNaN(currentTime) || Math.abs(previousTime - currentTime) >= contentChunkWindowMs) {
      compacted.push(event)
    }
  }

  return compacted
}
