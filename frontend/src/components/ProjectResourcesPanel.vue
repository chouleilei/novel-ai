<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import api from '@/api'
import type {
  ChapterSummaryResource,
  CharacterResource,
  CharacterRevisionResource,
  WorldSettingResource,
  WorldSettingRevisionResource,
} from '@/types'

const props = defineProps<{
  projectId: string
  currentChapterNumber?: number
  refreshToken?: number
}>()
const emit = defineEmits<{
  (event: 'resources-updated'): void
}>()

const activeTab = ref<'summaries' | 'characters' | 'world' | 'revisions'>('summaries')
const loading = ref(false)
const actionLoading = ref(false)
const errorMessage = ref<string | null>(null)
const summaries = ref<ChapterSummaryResource[]>([])
const characters = ref<CharacterResource[]>([])
const worldSettings = ref<WorldSettingResource[]>([])
const characterRevisions = ref<CharacterRevisionResource[]>([])
const worldRevisions = ref<WorldSettingRevisionResource[]>([])

const resourcePendingHint = computed(() => {
  if (!props.currentChapterNumber) {
    return '项目开始生成后，这里会逐步显示摘要、角色和世界观资源。'
  }
  return `第 ${props.currentChapterNumber} 章的资源会在章节通过评审并完成记忆整理后生成，写作中或评审中暂时为空是正常现象。`
})

const currentSummary = computed(() => {
  if (!props.currentChapterNumber) {
    return null
  }
  return summaries.value.find(item => item.chapter_number === props.currentChapterNumber) || null
})

const remainingSummaries = computed(() => {
  return summaries.value
    .filter(item => item.chapter_number !== props.currentChapterNumber)
    .sort((a, b) => b.chapter_number - a.chapter_number)
})

const groupedWorldSettings = computed(() => {
  const groups: Record<string, WorldSettingResource[]> = {}
  for (const item of worldSettings.value) {
    const key = item.category || 'general'
    if (!groups[key]) {
      groups[key] = []
    }
    groups[key].push(item)
  }
  return Object.entries(groups)
    .sort(([left], [right]) => left.localeCompare(right, 'zh-CN'))
    .map(([category, items]) => ({
      category,
      items: items.sort((a, b) => a.name.localeCompare(b.name, 'zh-CN')),
    }))
})

const pendingCharacterRevisions = computed(() => {
  return characterRevisions.value.filter(item => item.apply_mode === 'needs_review')
})

const pendingWorldRevisions = computed(() => {
  return worldRevisions.value.filter(item => item.apply_mode === 'needs_review')
})

const resolvedCharacterRevisions = computed(() => {
  return characterRevisions.value.filter(item => item.apply_mode !== 'needs_review')
})

const resolvedWorldRevisions = computed(() => {
  return worldRevisions.value.filter(item => item.apply_mode !== 'needs_review')
})

const fetchResources = async () => {
  if (!props.projectId) {
    return
  }

  loading.value = true
  errorMessage.value = null
  try {
    const [
      summaryRes,
      characterRes,
      worldRes,
      characterRevisionRes,
      worldRevisionRes,
    ] = await Promise.all([
      api.get(`/projects/${props.projectId}/summaries`),
      api.get(`/projects/${props.projectId}/characters`),
      api.get(`/projects/${props.projectId}/world-settings`),
      api.get(`/projects/${props.projectId}/character-revisions`),
      api.get(`/projects/${props.projectId}/world-setting-revisions`),
    ])

    summaries.value = (summaryRes as unknown as ChapterSummaryResource[]).sort((a, b) => b.chapter_number - a.chapter_number)
    characters.value = (characterRes as unknown as CharacterResource[]).sort((a, b) => a.name.localeCompare(b.name, 'zh-CN'))
    worldSettings.value = worldRes as unknown as WorldSettingResource[]
    characterRevisions.value = characterRevisionRes as unknown as CharacterRevisionResource[]
    worldRevisions.value = worldRevisionRes as unknown as WorldSettingRevisionResource[]
  } catch (e: any) {
    errorMessage.value = e?.response?.data?.detail || e?.message || '加载项目资源失败。'
    console.error('Failed to fetch project resources', e)
  } finally {
    loading.value = false
  }
}

const getCharacterStatus = (profile?: Record<string, any>) => {
  if (!profile) {
    return ''
  }
  return profile.current_status || profile.latest_state || profile.notes || ''
}

const getCharacterHighlights = (profile?: Record<string, any>) => {
  if (!profile) {
    return []
  }
  const ignoredKeys = new Set(['current_status', 'latest_state', 'notes', 'name', 'role'])
  return Object.entries(profile)
    .filter(([key, value]) => !ignoredKeys.has(key) && value != null && value !== '')
    .slice(0, 3)
}

const getWorldSettingPreview = (setting: WorldSettingResource) => {
  const payload = setting.setting_json || {}
  if (typeof payload.description === 'string' && payload.description.trim()) {
    return payload.description
  }
  const entries = Object.entries(payload).slice(0, 3)
  if (entries.length === 0) {
    return '暂无结构化说明'
  }
  return entries.map(([key, value]) => `${key}: ${typeof value === 'string' ? value : JSON.stringify(value)}`).join(' · ')
}

const formatSummaryItem = (item: unknown) => {
  if (typeof item === 'string') {
    return item
  }
  if (!item || typeof item !== 'object') {
    return String(item ?? '')
  }

  const record = item as Record<string, any>
  const candidate = [
    record.event,
    record.description,
    record.thread,
    record.title,
    record.significance,
    record.comment,
  ].find(value => typeof value === 'string' && value.trim())

  return typeof candidate === 'string' ? candidate.trim() : JSON.stringify(record)
}

const applyRevision = async (kind: 'character' | 'world', revisionId: string) => {
  actionLoading.value = true
  errorMessage.value = null
  try {
    const path = kind === 'character'
      ? `/projects/${props.projectId}/character-revisions/${revisionId}/apply`
      : `/projects/${props.projectId}/world-setting-revisions/${revisionId}/apply`
    await api.post(path)
    await fetchResources()
    emit('resources-updated')
  } catch (e: any) {
    errorMessage.value = e?.response?.data?.detail || e?.message || '应用提案失败。'
  } finally {
    actionLoading.value = false
  }
}

const rejectRevision = async (kind: 'character' | 'world', revisionId: string) => {
  actionLoading.value = true
  errorMessage.value = null
  try {
    const path = kind === 'character'
      ? `/projects/${props.projectId}/character-revisions/${revisionId}/reject`
      : `/projects/${props.projectId}/world-setting-revisions/${revisionId}/reject`
    await api.post(path)
    await fetchResources()
    emit('resources-updated')
  } catch (e: any) {
    errorMessage.value = e?.response?.data?.detail || e?.message || '拒绝提案失败。'
  } finally {
    actionLoading.value = false
  }
}

watch(
  () => [props.projectId, props.refreshToken],
  () => {
    void fetchResources()
  },
  { immediate: true },
)
</script>

<template>
  <div class="card min-h-0 flex-1 overflow-hidden flex flex-col">
    <div class="card-header bg-gray-50 py-3">
      <div class="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div class="flex gap-2 overflow-x-auto rounded-md bg-gray-100 p-1 text-xs">
          <button
            class="shrink-0 whitespace-nowrap rounded px-2 py-1 transition-colors"
            :class="activeTab === 'summaries' ? 'bg-white text-gray-900 shadow-sm' : 'text-gray-500'"
            @click="activeTab = 'summaries'"
          >
            摘要
          </button>
          <button
            class="shrink-0 whitespace-nowrap rounded px-2 py-1 transition-colors"
            :class="activeTab === 'characters' ? 'bg-white text-gray-900 shadow-sm' : 'text-gray-500'"
            @click="activeTab = 'characters'"
          >
            角色
          </button>
          <button
            class="shrink-0 whitespace-nowrap rounded px-2 py-1 transition-colors"
            :class="activeTab === 'world' ? 'bg-white text-gray-900 shadow-sm' : 'text-gray-500'"
            @click="activeTab = 'world'"
          >
            世界观
          </button>
          <button
            class="shrink-0 whitespace-nowrap rounded px-2 py-1 transition-colors"
            :class="activeTab === 'revisions' ? 'bg-white text-gray-900 shadow-sm' : 'text-gray-500'"
            @click="activeTab = 'revisions'"
          >
            审核提案
          </button>
        </div>
        <button @click="fetchResources" class="self-end text-xs text-gray-500 hover:text-gray-800 sm:self-auto" :disabled="loading || actionLoading">
          刷新
        </button>
      </div>
    </div>

    <div class="overflow-y-auto flex-1 p-4">
      <div v-if="loading" class="flex items-center justify-center py-6 text-sm text-gray-500">
        正在加载资源...
      </div>
      <div v-else-if="errorMessage" class="rounded-md border border-red-100 bg-red-50 px-3 py-3 text-sm text-red-700">
        {{ errorMessage }}
      </div>

      <template v-else-if="activeTab === 'summaries'">
        <div v-if="currentSummary" class="mb-4 rounded-lg border border-primary-200 bg-primary-50 p-3">
          <div class="mb-2 flex items-center justify-between">
            <h4 class="text-sm font-semibold text-primary-900">第 {{ currentSummary.chapter_number }} 章摘要</h4>
            <span v-if="currentSummary.emotional_tone" class="text-xs text-primary-700">
              {{ currentSummary.emotional_tone }}
            </span>
          </div>
          <p class="whitespace-pre-wrap text-sm text-gray-700">{{ currentSummary.summary_text }}</p>
          <p v-if="currentSummary.time_location" class="mt-2 text-xs text-gray-500">
            {{ currentSummary.time_location }}
          </p>
          <div v-if="currentSummary.key_events?.length" class="mt-3">
            <h5 class="mb-1 text-xs font-medium uppercase tracking-wide text-gray-500">关键事件</h5>
            <ul class="space-y-1 text-xs text-gray-700">
              <li v-for="item in currentSummary.key_events" :key="formatSummaryItem(item)">• {{ formatSummaryItem(item) }}</li>
            </ul>
          </div>
          <div v-if="currentSummary.unresolved_threads?.length" class="mt-3">
            <h5 class="mb-1 text-xs font-medium uppercase tracking-wide text-gray-500">未收束线索</h5>
            <ul class="space-y-1 text-xs text-orange-700">
              <li v-for="item in currentSummary.unresolved_threads" :key="formatSummaryItem(item)">• {{ formatSummaryItem(item) }}</li>
            </ul>
          </div>
        </div>

        <div v-if="remainingSummaries.length" class="space-y-3">
          <div v-for="summary in remainingSummaries" :key="summary.chapter_number" class="rounded-md border border-gray-200 bg-white p-3">
            <div class="mb-1 flex items-center justify-between">
              <h4 class="text-sm font-medium text-gray-900">第 {{ summary.chapter_number }} 章</h4>
              <span v-if="summary.emotional_tone" class="text-xs text-gray-500">{{ summary.emotional_tone }}</span>
            </div>
            <p class="text-sm text-gray-600">{{ summary.summary_text }}</p>
          </div>
        </div>

        <div v-if="!currentSummary && remainingSummaries.length === 0" class="py-6 text-center text-sm text-gray-500">
          <p>暂无摘要。</p>
          <p class="mt-2 text-xs text-gray-400">{{ resourcePendingHint }}</p>
        </div>
      </template>

      <template v-else-if="activeTab === 'characters'">
        <div v-if="characters.length" class="space-y-3">
          <div v-for="character in characters" :key="character.name" class="rounded-md border border-gray-200 bg-white p-3">
            <div class="mb-2 flex items-center justify-between gap-2">
              <h4 class="text-sm font-semibold text-gray-900">{{ character.name }}</h4>
              <span v-if="character.role" class="rounded bg-gray-100 px-2 py-0.5 text-xs text-gray-600">
                {{ character.role }}
              </span>
            </div>
            <p v-if="getCharacterStatus(character.profile_json)" class="text-sm text-gray-700">
              {{ getCharacterStatus(character.profile_json) }}
            </p>
            <ul v-if="getCharacterHighlights(character.profile_json).length" class="mt-2 space-y-1 text-xs text-gray-500">
              <li v-for="[key, value] in getCharacterHighlights(character.profile_json)" :key="`${character.name}-${key}`">
                {{ key }}: {{ typeof value === 'string' ? value : JSON.stringify(value) }}
              </li>
            </ul>
          </div>
        </div>
        <div v-else class="py-6 text-center text-sm text-gray-500">
          <p>暂无角色信息。</p>
          <p class="mt-2 text-xs text-gray-400">{{ resourcePendingHint }}</p>
        </div>
      </template>

      <template v-else-if="activeTab === 'world'">
        <div v-if="groupedWorldSettings.length" class="space-y-4">
          <div v-for="group in groupedWorldSettings" :key="group.category">
            <h4 class="mb-2 text-xs font-semibold uppercase tracking-wide text-gray-500">{{ group.category }}</h4>
            <div class="space-y-2">
              <div v-for="setting in group.items" :key="`${setting.category}-${setting.name}`" class="rounded-md border border-gray-200 bg-white p-3">
                <h5 class="text-sm font-medium text-gray-900">{{ setting.name }}</h5>
                <p class="mt-1 text-sm text-gray-600">{{ getWorldSettingPreview(setting) }}</p>
              </div>
            </div>
          </div>
        </div>
        <div v-else class="py-6 text-center text-sm text-gray-500">
          <p>暂无世界观设定。</p>
          <p class="mt-2 text-xs text-gray-400">{{ resourcePendingHint }}</p>
        </div>
      </template>

      <template v-else>
        <div class="space-y-5">
          <section class="rounded-lg border border-amber-200 bg-amber-50 p-3">
            <div class="mb-3 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              <div>
                <h4 class="text-sm font-semibold text-amber-900">待人工复核</h4>
                <p class="mt-1 text-xs text-amber-700">
                  检测到高风险人物或世界观更新时，会先进入这里待审核；提案处理前不会阻塞后续生成，确认后才写入正式资源。
                </p>
              </div>
              <span class="rounded bg-amber-100 px-2 py-1 text-xs font-medium text-amber-800">
                {{ pendingCharacterRevisions.length + pendingWorldRevisions.length }} 条
              </span>
            </div>

            <div v-if="pendingCharacterRevisions.length || pendingWorldRevisions.length" class="space-y-3">
              <div
                v-for="revision in pendingCharacterRevisions"
                :key="revision.id"
                class="rounded-md border border-white/70 bg-white p-3"
              >
                <div class="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                  <div>
                    <p class="text-sm font-medium text-gray-900">角色 · {{ revision.character_name }}</p>
                    <p class="mt-1 text-xs text-gray-500">第 {{ revision.chapter_number }} 章 · {{ revision.change_type }}</p>
                  </div>
                  <span class="text-xs text-amber-700">置信度 {{ revision.confidence ?? '未知' }}</span>
                </div>
                <pre class="mt-2 overflow-x-auto rounded bg-gray-50 p-2 text-xs text-gray-700">{{ JSON.stringify(revision.patch_json || {}, null, 2) }}</pre>
                <div class="mt-3 flex flex-wrap gap-2">
                  <button class="btn btn-sm btn-primary" :disabled="actionLoading" @click="applyRevision('character', revision.id)">应用</button>
                  <button class="btn btn-sm btn-secondary" :disabled="actionLoading" @click="rejectRevision('character', revision.id)">驳回</button>
                </div>
              </div>

              <div
                v-for="revision in pendingWorldRevisions"
                :key="revision.id"
                class="rounded-md border border-white/70 bg-white p-3"
              >
                <div class="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                  <div>
                    <p class="text-sm font-medium text-gray-900">世界观 · {{ revision.category }} / {{ revision.name }}</p>
                    <p class="mt-1 text-xs text-gray-500">第 {{ revision.chapter_number }} 章 · {{ revision.change_type }}</p>
                  </div>
                  <span class="text-xs text-amber-700">置信度 {{ revision.confidence ?? '未知' }}</span>
                </div>
                <pre class="mt-2 overflow-x-auto rounded bg-gray-50 p-2 text-xs text-gray-700">{{ JSON.stringify(revision.patch_json || {}, null, 2) }}</pre>
                <div class="mt-3 flex flex-wrap gap-2">
                  <button class="btn btn-sm btn-primary" :disabled="actionLoading" @click="applyRevision('world', revision.id)">应用</button>
                  <button class="btn btn-sm btn-secondary" :disabled="actionLoading" @click="rejectRevision('world', revision.id)">驳回</button>
                </div>
              </div>
            </div>

            <div v-else class="py-4 text-center text-sm text-amber-700">
              当前没有待处理提案。
            </div>
          </section>

          <section>
            <h4 class="mb-2 text-sm font-semibold text-gray-900">最近处理记录</h4>
            <div v-if="resolvedCharacterRevisions.length || resolvedWorldRevisions.length" class="space-y-2">
              <div
                v-for="revision in [...resolvedCharacterRevisions, ...resolvedWorldRevisions].sort((a, b) => b.created_at.localeCompare(a.created_at))"
                :key="revision.id"
                class="rounded-md border border-gray-200 bg-white p-3 text-sm"
              >
                <div class="flex items-center justify-between gap-2">
                  <span class="font-medium text-gray-900">
                    {{ 'character_name' in revision ? `角色 · ${revision.character_name}` : `世界观 · ${revision.category} / ${revision.name}` }}
                  </span>
                  <span class="text-xs" :class="revision.apply_mode === 'applied' ? 'text-green-700' : 'text-gray-500'">
                    {{ revision.apply_mode === 'applied' ? '已应用' : '已驳回' }}
                  </span>
                </div>
                <p class="mt-1 text-xs text-gray-500">第 {{ revision.chapter_number }} 章 · {{ revision.change_type }}</p>
              </div>
            </div>
            <div v-else class="py-4 text-center text-sm text-gray-500">
              暂无处理记录。
            </div>
          </section>
        </div>
      </template>
    </div>
  </div>
</template>
