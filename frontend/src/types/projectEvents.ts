export const PROJECT_EVENT_TYPES = [
  'pipeline_start',
  'chapter_writing',
  'chapter_truncated',
  'precheck_done',
  'prompt_generated',
  'prompt_generation_fallback',
  'prompt_updated',
  'content_chunk',
  'writer_non_stream_started',
  'writer_non_stream_succeeded',
  'writer_stream_no_text_fallback',
  'writer_stream_fallback_started',
  'writer_stream_fallback_succeeded',
  'chapter_reviewing',
  'chapter_scored',
  'memory_updated',
  'chapter_passed',
  'chapter_paused',
  'chapter_rewriting',
  'job_failed',
  'project_paused',
  'pipeline_complete',
] as const

export type ProjectEventType = (typeof PROJECT_EVENT_TYPES)[number]

export interface ProjectEventBase<TType extends string = string, TData extends object = Record<string, unknown>> {
  id: number
  type: TType
  chapter_number: number | null
  data: TData
  created_at: string
}

export interface PipelineStartEventData {
  chapter_number?: number
  resume?: boolean
}

export interface ChapterWritingEventData {
  attempt?: number
  continuation?: boolean
  continuation_source?: 'resume_saved_draft' | 'writer_truncated' | 'chapter_breakpoint_button'
  saved_chars?: number
}

export interface ChapterTruncatedEventData {
  attempt?: number
  accumulated_chars?: number
  reason?: string
}

export interface PromptGeneratedEventData {
  version_no?: number
  source?: string
  fallback?: Record<string, unknown>
}

export interface WriterTimeoutEventData {
  timeout_seconds?: number
}

export interface WriterFallbackEventData {
  waited_seconds?: number
  streamed_chars?: number
  fallback_chars?: number
  timeout_seconds?: number
  reason?: string
}

export interface ContentChunkEventData {
  chunk?: string
}

export interface ChapterScoredEventData {
  score?: number
  passed?: boolean
  review_thresholds?: Record<string, unknown>
}

export interface MemoryUpdatedEventData {
  chapter_number?: number
  character_revision_count?: number
  world_revision_count?: number
  applied_revision_count?: number
  needs_review_count?: number
  confidence_threshold?: number
}

export interface ChapterPassedEventData {
  score?: number | null
  manual?: boolean
  auto_accepted?: boolean
}

export interface ChapterPausedEventData {
  reason?: string
  stage?: string
  saved_chars?: number
}

export interface ChapterRewritingEventData {
  retry_count?: number
  queued?: boolean
  reason?: string
  error?: string
  failure_summary?: {
    overall_score?: number
    blocking_issue_count?: number
    top_blocking_issues?: string[]
    violated_instruction_count?: number
    top_violated_instructions?: string[]
    improvement_suggestion_count?: number
    top_improvement_suggestions?: string[]
  }
}

export interface JobFailedEventData {
  job_id?: string
  error?: string
}

export interface ProjectPausedEventData {
  reason?: string
  phase?: string
  cancelled_jobs?: number
  paused_chapters?: number
  next_chapter?: number
}

export interface PipelineCompleteEventData {
  project_id?: string
}

export interface ProjectEventDataMap {
  pipeline_start: PipelineStartEventData
  chapter_writing: ChapterWritingEventData
  chapter_truncated: ChapterTruncatedEventData
  precheck_done: Record<string, unknown>
  prompt_generated: PromptGeneratedEventData
  prompt_generation_fallback: Record<string, unknown>
  prompt_updated: Record<string, unknown>
  content_chunk: ContentChunkEventData
  writer_non_stream_started: WriterTimeoutEventData
  writer_non_stream_succeeded: WriterFallbackEventData
  writer_stream_no_text_fallback: WriterFallbackEventData
  writer_stream_fallback_started: WriterFallbackEventData
  writer_stream_fallback_succeeded: WriterFallbackEventData
  chapter_reviewing: Record<string, unknown>
  chapter_scored: ChapterScoredEventData
  memory_updated: MemoryUpdatedEventData
  chapter_passed: ChapterPassedEventData
  chapter_paused: ChapterPausedEventData
  chapter_rewriting: ChapterRewritingEventData
  job_failed: JobFailedEventData
  project_paused: ProjectPausedEventData
  pipeline_complete: PipelineCompleteEventData
}

export type KnownProjectEvent = {
  [Key in ProjectEventType]: ProjectEventBase<Key, ProjectEventDataMap[Key]>
}[ProjectEventType]

export type ProjectEvent = KnownProjectEvent | ProjectEventBase<string, Record<string, unknown>>
