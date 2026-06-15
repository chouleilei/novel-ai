<script setup lang="ts">
import { ref, onMounted, computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ArrowLeftIcon, BookOpenIcon, ListBulletIcon, XMarkIcon } from '@heroicons/vue/24/outline'
import { useProjectStore } from '@/stores/project'
import api from '@/api'
import type { Chapter } from '@/types'

const route = useRoute()
const router = useRouter()
const projectStore = useProjectStore()
const projectId = computed(() => route.params.id as string)

const chapters = ref<Chapter[]>([])
const loading = ref(false)
const exportFormat = ref<'txt' | 'markdown' | 'epub'>('txt')
const errorMessage = ref<string | null>(null)
const mobileCatalogOpen = ref(false)

const getErrorMessage = (error: any, fallback: string) => {
  return error?.response?.data?.detail || error?.message || fallback
}

const fetchChapters = async () => {
  loading.value = true
  errorMessage.value = null
  try {
    const res = await api.get(`/projects/${projectId.value}/chapters`)
    const chapterSummaries = (res as unknown as Chapter[]).filter(chapter => chapter.status === 'passed')
    const chapterDetails = await Promise.all(
      chapterSummaries.map(chapter => api.get(`/projects/${projectId.value}/chapters/${chapter.chapter_number}`))
    )
    chapters.value = chapterSummaries.map((chapter, index) => ({
      ...chapter,
      ...(chapterDetails[index] as unknown as Chapter),
    }))
  } catch (e) {
    errorMessage.value = getErrorMessage(e, '加载阅读页面失败。')
    console.error('Failed to fetch chapters', e)
  } finally {
    loading.value = false
  }
}

onMounted(async () => {
  await projectStore.fetchProject(projectId.value)
  await fetchChapters()
})

const exportNovel = async () => {
  errorMessage.value = null
  try {
    const res = await api.get(`/projects/${projectId.value}/export`, {
      params: { format: exportFormat.value },
      responseType: 'blob',
    })
    const url = window.URL.createObjectURL(res as unknown as Blob)
    const link = document.createElement('a')
    link.href = url
    const extension = exportFormat.value === 'markdown' ? 'md' : exportFormat.value
    link.setAttribute('download', `${projectStore.currentProject?.title || 'novel'}.${extension}`)
    document.body.appendChild(link)
    link.click()
    link.remove()
    window.URL.revokeObjectURL(url)
  } catch (e) {
    errorMessage.value = getErrorMessage(e, '导出文件失败。')
    console.error('Failed to export novel', e)
  }
}

const closeMobileCatalog = () => {
  mobileCatalogOpen.value = false
}

const openProjectList = async () => {
  await router.push('/')
}

const openLiveWriting = async () => {
  await router.push(`/projects/${projectId.value}/live`)
}
</script>

<template>
  <div class="relative mx-auto max-w-6xl pb-20 lg:pb-0">
    <div v-if="errorMessage" class="mb-6 rounded-md bg-red-50 p-4 text-sm text-red-700">
      {{ errorMessage }}
    </div>

    <div class="fixed right-5 top-20 z-40 hidden overflow-hidden rounded-xl border border-[var(--color-border)] bg-white/95 shadow-lg shadow-slate-900/5 backdrop-blur lg:flex">
      <button
        type="button"
        class="inline-flex items-center gap-2 border-r border-[var(--color-border)] px-3 py-2 text-sm font-medium text-[var(--color-text-muted)] transition hover:bg-[var(--color-surface-muted)] hover:text-[var(--color-text)]"
        @click="openProjectList"
      >
        <ListBulletIcon class="h-4 w-4" aria-hidden="true" />
        项目列表
      </button>
      <button
        type="button"
        class="inline-flex items-center gap-2 px-3 py-2 text-sm font-medium text-[var(--color-text-muted)] transition hover:bg-[var(--color-surface-muted)] hover:text-[var(--color-text)]"
        @click="openLiveWriting"
      >
        <ArrowLeftIcon class="h-4 w-4" aria-hidden="true" />
        实时写作
      </button>
    </div>

    <div class="mb-8 overflow-hidden rounded-xl border border-[var(--color-border)] bg-white p-6 shadow-[0_1px_2px_rgba(16,24,40,0.04)] md:p-8">
      <div class="flex flex-col gap-6 lg:flex-row lg:items-end lg:justify-between">
        <div>
          <p class="text-xs font-semibold tracking-wide text-[var(--color-text-muted)]">阅读室</p>
          <h1 class="mt-3 font-serif text-3xl font-bold text-[var(--color-text)] md:text-4xl">{{ projectStore.currentProject?.title }}</h1>
          <p class="mt-3 max-w-2xl text-sm text-[var(--color-text-muted)]">
            共 {{ chapters.length }} 章 · {{ projectStore.currentProject?.genre || '未设置题材' }} · {{ projectStore.currentProject?.style || '未设置风格' }}
          </p>
        </div>
        <div class="flex flex-wrap gap-3">
          <button @click="openLiveWriting" class="btn btn-secondary">
            返回实时写作
          </button>
          <select v-model="exportFormat" class="input-field w-36">
            <option value="txt">TXT</option>
            <option value="markdown">Markdown</option>
            <option value="epub">EPUB</option>
          </select>
          <button @click="exportNovel" class="btn btn-primary" :disabled="chapters.length === 0">
            导出文件
          </button>
        </div>
      </div>
    </div>

    <div v-if="loading" class="text-center py-12">
      <div class="inline-block animate-spin rounded-full h-8 w-8 border-b-2 border-primary-600"></div>
      <p class="mt-2 text-gray-500">正在加载小说...</p>
    </div>

    <div v-else-if="chapters.length === 0" class="text-center py-12 bg-white/90 rounded-3xl shadow-sm border border-[var(--color-border)]">
      <p class="text-gray-500">还没有已完成章节。</p>
    </div>

    <div v-else class="grid gap-6 lg:grid-cols-[260px_minmax(0,1fr)]">
      <aside class="card hidden h-fit lg:sticky lg:top-8 lg:block">
        <div class="card-header bg-stone-50">
          <h2 class="text-sm font-semibold uppercase tracking-[0.2em] text-gray-500">章节目录</h2>
        </div>
        <div class="max-h-[70vh] overflow-y-auto p-3">
          <a
            v-for="chapter in chapters"
            :key="chapter.chapter_number"
            :href="`#chapter-${chapter.chapter_number}`"
            class="mb-2 block rounded-lg border border-transparent bg-[var(--color-surface-muted)] px-4 py-3 text-sm transition hover:border-[var(--color-border)] hover:bg-white"
          >
            <p class="font-semibold text-gray-900">第 {{ chapter.chapter_number }} 章</p>
            <p class="mt-1 line-clamp-2 text-xs text-gray-500">{{ chapter.final_content?.slice(0, 42) || '暂无章节内容' }}</p>
          </a>
        </div>
      </aside>

      <div class="space-y-10 rounded-xl border border-[var(--color-border)] bg-white p-6 shadow-[0_1px_2px_rgba(16,24,40,0.04)] md:p-10">
        <article
          v-for="chapter in chapters"
          :id="`chapter-${chapter.chapter_number}`"
          :key="chapter.chapter_number"
          class="scroll-mt-4 border-b border-[var(--color-border)] pb-10 last:border-b-0 last:pb-0 lg:scroll-mt-8"
        >
          <div class="mb-8 text-center">
            <p class="text-xs font-semibold tracking-wide text-gray-400">第 {{ chapter.chapter_number }} 章</p>
            <h2 class="mt-3 text-3xl font-bold font-serif text-gray-900">第 {{ chapter.chapter_number }} 章</h2>
          </div>
          <div class="whitespace-pre-wrap font-serif text-[17px] leading-8 text-gray-800">
            {{ chapter.final_content || '暂无章节内容。' }}
          </div>
        </article>
      </div>

      <div class="fixed inset-x-0 bottom-4 z-40 flex justify-center px-4 pb-[env(safe-area-inset-bottom)] lg:hidden">
        <div class="grid grid-cols-3 overflow-hidden rounded-full border border-[var(--color-border)] bg-white/95 shadow-xl shadow-slate-900/10 backdrop-blur">
          <button
            type="button"
            class="inline-flex min-w-20 flex-col items-center justify-center gap-0.5 px-4 py-2 text-xs font-medium text-[var(--color-text)] active:bg-[var(--color-surface-muted)]"
            aria-label="打开章节目录"
            @click="mobileCatalogOpen = true"
          >
            <BookOpenIcon class="h-5 w-5" aria-hidden="true" />
            目录
          </button>
          <button
            type="button"
            class="inline-flex min-w-20 flex-col items-center justify-center gap-0.5 border-x border-[var(--color-border)] px-4 py-2 text-xs font-medium text-[var(--color-text-muted)] active:bg-[var(--color-surface-muted)]"
            @click="openLiveWriting"
          >
            <ArrowLeftIcon class="h-5 w-5" aria-hidden="true" />
            实时
          </button>
          <button
            type="button"
            class="inline-flex min-w-20 flex-col items-center justify-center gap-0.5 px-4 py-2 text-xs font-medium text-[var(--color-text-muted)] active:bg-[var(--color-surface-muted)]"
            @click="openProjectList"
          >
            <ListBulletIcon class="h-5 w-5" aria-hidden="true" />
            项目
          </button>
        </div>
      </div>

      <div v-if="mobileCatalogOpen" class="fixed inset-0 z-50 lg:hidden" role="dialog" aria-modal="true" aria-label="章节目录">
        <button
          type="button"
          class="absolute inset-0 h-full w-full bg-slate-950/35"
          aria-label="关闭章节目录"
          @click="closeMobileCatalog"
        ></button>
        <div class="absolute inset-x-0 bottom-0 max-h-[78vh] overflow-hidden rounded-t-2xl border border-[var(--color-border)] bg-white shadow-2xl">
          <div class="flex items-center justify-between border-b border-[var(--color-border)] px-4 py-3">
            <div>
              <p class="text-sm font-semibold text-[var(--color-text)]">章节目录</p>
              <p class="mt-0.5 text-xs text-[var(--color-text-muted)]">共 {{ chapters.length }} 章</p>
            </div>
            <button
              type="button"
              class="inline-flex h-9 w-9 items-center justify-center rounded-lg border border-[var(--color-border)] bg-white text-[var(--color-text-muted)]"
              aria-label="关闭章节目录"
              @click="closeMobileCatalog"
            >
              <XMarkIcon class="h-5 w-5" aria-hidden="true" />
            </button>
          </div>
          <div class="max-h-[60vh] overflow-y-auto p-3">
            <a
              v-for="chapter in chapters"
              :key="`mobile-${chapter.chapter_number}`"
              :href="`#chapter-${chapter.chapter_number}`"
              class="mb-2 block rounded-lg border border-[var(--color-border)] bg-[var(--color-surface-muted)] px-4 py-3 text-sm transition active:bg-white"
              @click="closeMobileCatalog"
            >
              <p class="font-semibold text-gray-900">第 {{ chapter.chapter_number }} 章</p>
              <p class="mt-1 line-clamp-2 text-xs text-gray-500">{{ chapter.final_content?.slice(0, 42) || '暂无章节内容' }}</p>
            </a>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
