export type ProjectStatus = 'draft' | 'ready' | 'running' | 'paused' | 'completed' | 'failed'

export interface ModelExtraConfig {
  api_key?: string
  api_key_env_var?: string
  has_api_key?: boolean
  can_save_api_key?: boolean
  clear_api_key?: boolean
  supports_json_schema_output?: boolean
  custom_params?: Record<string, any> | null
  [key: string]: any
}

export interface Project {
  id: string
  title: string
  genre?: string | null
  style?: string | null
  global_prompt?: string
  total_chapters: number
  current_chapter: number
  status: ProjectStatus
  auto_mode?: boolean
  auto_accept_critic_failed?: boolean
  auto_accept_on_max_retries?: boolean
  hard_review_gates_enabled?: boolean
  max_retries?: number
  writer_streaming_enabled?: boolean
  generation_mode?: 'standard' | 'rush'
  rush_previous_chapter_count?: number
  last_error?: string | null
  created_at?: string
  updated_at?: string
}

export interface ModelConfig {
  id?: string
  project_id?: string
  channel_id?: string
  role: 'writer' | 'critic' | 'memory' | 'prompt_builder'
  provider: 'mock' | 'openai_compatible'
  base_url: string
  model_name: string
  temperature?: number | null
  max_tokens?: number | null
  extra_config?: ModelExtraConfig
}

export interface ProviderChannel {
  id: string
  name: string
  provider: 'mock' | 'openai_compatible'
  base_url: string
  default_model_name: string
  api_key_env_var?: string
  has_api_key?: boolean
  is_enabled: boolean
  created_at?: string
  updated_at?: string
}

export interface ChannelFormData {
  id?: string
  name?: string
  provider?: 'mock' | 'openai_compatible'
  base_url?: string
  default_model_name?: string
  api_key?: string
  api_key_env_var?: string
  clear_api_key?: boolean
  is_enabled?: boolean
  discovered_models?: ProviderChannelModel[]
}

export interface ProviderChannelModel {
  id: string
  model_name: string
  display_name: string
  provider_model_id: string
  owned_by?: string
  is_default: boolean
  is_enabled: boolean
}

export interface ChannelDiscoverRequest {
  provider: 'mock' | 'openai_compatible'
  base_url: string
  api_key?: string
}

export interface ChannelDiscoverResponse {
  success: boolean
  message: string
  connection_status: 'ok' | 'error' | 'unauthorized'
  models?: ProviderChannelModel[]
  models_count?: number
  default_model_name?: string
}

export interface ModelConnectivityResult {
  success: boolean
  role: ModelConfig['role']
  provider: ModelConfig['provider']
  base_url: string
  model_name: string
  latency_ms: number
  message: string
  output_preview?: string | null
  raw_preview?: string | null
  api_key_source: 'inline' | 'saved' | 'env_var' | 'not_required'
}

export interface SystemProjectDefaults {
  default_total_chapters: number
  default_auto_mode: boolean
  default_max_retries: number
}

export interface SystemRuntimeReviewPolicy {
  overall_score_threshold: number
  outline_score_threshold: number
  instruction_score_threshold: number
}

export interface SystemRuntimeMemoryPolicy {
  auto_apply_confidence_threshold: number
}

export interface SystemRuntimeContextBudget {
  writer_target_input_tokens: number
  writer_hard_limit_tokens: number
  critic_target_input_tokens: number
  critic_hard_limit_tokens: number
}

export interface SystemRuntimeExecutionPolicy {
  llm_stage_timeout_seconds: number
}

export interface SystemRuntimeSettings {
  review_policy: SystemRuntimeReviewPolicy
  memory_policy: SystemRuntimeMemoryPolicy
  context_budget: SystemRuntimeContextBudget
  execution: SystemRuntimeExecutionPolicy
}

export interface SystemSettingsResponse {
  project_defaults: SystemProjectDefaults
  model_configs: ModelConfig[]
  runtime_settings: SystemRuntimeSettings
}

export type ChapterStatus = 'pending' | 'queued' | 'writing' | 'reviewing' | 'passed' | 'failed' | 'paused'

export interface Chapter {
  id?: string
  project_id?: string
  chapter_number: number
  status: ChapterStatus
  has_active_generation_job?: boolean
  retry_count: number
  final_content?: string | null
  final_score?: number | null
  accepted_attempt_id?: string
  auto_accepted?: boolean
  improvement_notes?: string | null
  last_error?: string | null
  created_at?: string
  updated_at?: string
}

export interface ChapterOutline {
  id?: string
  project_id?: string
  chapter_number: number
  outline_text: string
  tags?: Record<string, any>
}

export interface ChapterPrompt {
  id?: string
  project_id?: string
  chapter_number?: number
  version_no: number
  generated_system_prompt: string
  user_edited_prompt?: string
  effective_system_prompt: string
  source_payload?: Record<string, any>
  status: 'generated' | 'edited' | 'approved' | 'superseded'
  created_at?: string
}

export interface ChapterAttempt {
  id: string
  chapter_id?: string
  attempt_no: number
  prompt_version_id?: string
  input_snapshot?: Record<string, any>
  input_tokens?: number
  output_tokens?: number
  content?: string | null
  status: 'running' | 'reviewed' | 'accepted' | 'rejected' | 'errored'
  started_at: string
  finished_at?: string
}

export interface ChapterReview {
  id?: string
  attempt_id: string
  overall_score: number
  passed: boolean
  outline_score?: number
  instruction_score?: number
  continuity_score?: number
  character_score?: number
  writing_score?: number
  blocking_issues?: any[]
  uncovered_outline_points?: any[]
  violated_instructions?: any[]
  improvement_suggestions?: any[]
  non_scoring_notes?: any[]
  raw_json?: Record<string, any>
  created_at: string
}

export interface ChapterSummaryResource {
  chapter_number: number
  summary_text: string
  key_events?: Array<string | Record<string, any>>
  unresolved_threads?: string[]
  emotional_tone?: string | null
  time_location?: string | null
}

export interface CharacterResource {
  name: string
  role?: string | null
  profile_json?: Record<string, any>
  is_active: boolean
}

export interface CharacterRevisionResource {
  id: string
  chapter_number: number
  character_name: string
  change_type: string
  patch_json?: Record<string, any>
  confidence?: number | null
  apply_mode: 'needs_review' | 'applied' | 'rejected' | 'auto_safe'
  created_at: string
}

export interface WorldSettingResource {
  category: string
  name: string
  setting_json?: Record<string, any>
}

export interface WorldSettingRevisionResource {
  id: string
  chapter_number: number
  category: string
  name: string
  change_type: string
  patch_json?: Record<string, any>
  confidence?: number | null
  apply_mode: 'needs_review' | 'applied' | 'rejected' | 'auto_safe'
  created_at: string
}
