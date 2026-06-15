<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useProjectStore } from '@/stores/project'
import type { Project } from '@/types'
import { formatDate } from '@/utils/datetime'

const router = useRouter()
const projectStore = useProjectStore()
const deletingProjectId = ref<string | null>(null)
const copyingProjectId = ref<string | null>(null)

onMounted(() => {
  projectStore.fetchProjects()
})

const createNewProject = () => {
  router.push('/projects/new')
}

const openProject = (id: string) => {
  router.push(`/projects/${id}`)
}

const deleteProject = async (project: Project) => {
  if (deletingProjectId.value) {
    return
  }
  const confirmed = window.confirm(`确定删除项目《${project.title}》吗？该操作会删除章节、事件和资源记录，且无法恢复。`)
  if (!confirmed) {
    return
  }

  deletingProjectId.value = project.id
  try {
    await projectStore.deleteProject(project.id)
  } finally {
    deletingProjectId.value = null
  }
}

const copyProject = async (project: Project) => {
  if (copyingProjectId.value || deletingProjectId.value) {
    return
  }

  const confirmed = window.confirm(`确定复制项目《${project.title}》吗？系统会复制项目设置、模型配置和章节大纲，但不会复制已创作正文、事件、评审和资源记录。`)
  if (!confirmed) {
    return
  }

  copyingProjectId.value = project.id
  try {
    const copiedProject = await projectStore.copyProject(project.id)
    await router.push(`/projects/${copiedProject.id}`)
  } finally {
    copyingProjectId.value = null
  }
}

const getStatusBadgeClass = (status: string) => {
  switch (status) {
    case 'draft': return 'badge-secondary'
    case 'ready': return 'badge-info'
    case 'running': return 'badge-primary'
    case 'paused': return 'badge-warning'
    case 'completed': return 'badge-success'
    case 'failed': return 'badge-danger'
    default: return 'badge-secondary'
  }
}

const getStatusLabel = (status: string) => {
  switch (status) {
    case 'draft': return '草稿'
    case 'ready': return '就绪'
    case 'running': return '生成中'
    case 'paused': return '已暂停'
    case 'completed': return '已完成'
    case 'failed': return '失败'
    default: return status
  }
}

const getProgressWidth = (project: Project) => {
  if (!project.total_chapters) {
    return 0
  }
  return Math.min((project.current_chapter / project.total_chapters) * 100, 100)
}

const formatUpdatedAt = (value?: string) => {
  return formatDate(value, '暂无')
}

const getGenreAccent = (genre?: string | null) => {
  const text = genre || ''
  if (text.includes('悬疑') || text.includes('惊悚')) return 'from-stone-700 via-stone-600 to-amber-700'
  if (text.includes('科幻')) return 'from-teal-700 via-cyan-700 to-sky-600'
  if (text.includes('仙侠') || text.includes('玄幻')) return 'from-emerald-700 via-teal-700 to-lime-600'
  if (text.includes('言情') || text.includes('爱情')) return 'from-rose-500 via-orange-400 to-amber-300'
  if (text.includes('历史')) return 'from-amber-800 via-orange-700 to-stone-600'
  return 'from-teal-700 via-emerald-600 to-amber-500'
}

const getProgressLabel = (project: Project) => {
  if (!project.total_chapters) {
    return '尚未开始'
  }
  if (project.status === 'completed') {
    return '已完结'
  }
  if (project.current_chapter <= 0) {
    return '等待首章启动'
  }
  return `推进到第 ${project.current_chapter} 章`
}
</script>

<template>
  <div>
    <div class="sm:flex sm:items-center sm:justify-between mb-8">
      <div>
        <h1 class="text-2xl font-bold text-gray-900">项目列表</h1>
        <p class="mt-2 text-sm text-gray-700">管理你的小说生成项目。</p>
      </div>
      <div class="mt-4 sm:mt-0">
        <button @click="createNewProject" class="btn btn-primary">
          新建项目
        </button>
      </div>
    </div>

    <div v-if="projectStore.loading" class="text-center py-12">
      <div class="inline-block animate-spin rounded-full h-8 w-8 border-b-2 border-primary-600"></div>
      <p class="mt-2 text-gray-500">正在加载项目...</p>
    </div>

    <div v-else-if="projectStore.error" class="bg-red-50 p-4 rounded-md">
      <div class="flex">
        <div class="flex-shrink-0">
          <svg class="h-5 w-5 text-red-400" viewBox="0 0 20 20" fill="currentColor">
            <path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zM8.707 7.293a1 1 0 00-1.414 1.414L8.586 10l-1.293 1.293a1 1 0 101.414 1.414L10 11.414l1.293 1.293a1 1 0 001.414-1.414L11.414 10l1.293-1.293a1 1 0 00-1.414-1.414L10 8.586 8.707 7.293z" clip-rule="evenodd" />
          </svg>
        </div>
        <div class="ml-3">
          <h3 class="text-sm font-medium text-red-800">加载项目失败</h3>
          <div class="mt-2 text-sm text-red-700">
            <p>{{ projectStore.error }}</p>
          </div>
        </div>
      </div>
    </div>

    <div v-else-if="projectStore.projects.length === 0" class="text-center py-12 bg-white rounded-lg shadow border border-gray-200">
      <svg class="mx-auto h-12 w-12 text-gray-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" aria-hidden="true">
        <path vector-effect="non-scaling-stroke" stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 13h6m-3-3v6m-9 1V7a2 2 0 012-2h6l2 2h6a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2z" />
      </svg>
      <h3 class="mt-2 text-sm font-medium text-gray-900">还没有项目</h3>
      <p class="mt-1 text-sm text-gray-500">先创建一个新项目开始使用吧。</p>
      <div class="mt-6">
        <button @click="createNewProject" class="btn btn-primary">
          新建项目
        </button>
      </div>
    </div>

    <div v-else class="grid grid-cols-1 gap-6 sm:grid-cols-2 xl:grid-cols-3">
      <div 
        v-for="project in projectStore.projects" 
        :key="project.id" 
        class="card cursor-pointer overflow-hidden transition-all duration-300 hover:-translate-y-1 hover:shadow-xl"
        @click="openProject(project.id)"
      >
        <div class="h-2 bg-gradient-to-r" :class="getGenreAccent(project.genre)"></div>
        <div class="card-body">
          <div class="flex flex-wrap justify-between gap-3">
            <div class="min-w-0 flex-1">
              <p class="text-[11px] font-semibold tracking-wide text-gray-400">创作项目</p>
              <h3 class="mt-2 text-xl font-semibold text-gray-900 truncate">{{ project.title || '未命名项目' }}</h3>
              <p class="mt-2 text-sm text-gray-500">{{ project.genre || '待补充题材' }}</p>
            </div>
            <div class="flex items-center gap-2 shrink-0">
              <span :class="['badge', getStatusBadgeClass(project.status)]">
                {{ getStatusLabel(project.status) }}
              </span>
              <button
                class="text-xs font-medium text-teal-700 hover:text-teal-800 disabled:cursor-not-allowed disabled:text-teal-300"
                :disabled="copyingProjectId === project.id || deletingProjectId !== null || projectStore.loading"
                @click.stop="copyProject(project)"
              >
                {{ copyingProjectId === project.id ? '复制中...' : '复制' }}
              </button>
              <button
                class="text-xs font-medium text-red-600 hover:text-red-700 disabled:cursor-not-allowed disabled:text-red-300"
                :disabled="deletingProjectId === project.id || copyingProjectId !== null || projectStore.loading"
                @click.stop="deleteProject(project)"
              >
                {{ deletingProjectId === project.id ? '删除中...' : '删除' }}
              </button>
            </div>
          </div>
          <div class="mt-5 grid grid-cols-2 gap-3 text-sm">
            <div class="rounded-lg bg-[var(--color-surface-muted)] px-4 py-3">
              <p class="text-xs uppercase tracking-wide text-gray-400">进度</p>
              <p class="mt-1 font-medium text-gray-800">{{ project.current_chapter }} / {{ project.total_chapters }} 章</p>
            </div>
            <div class="rounded-lg bg-[var(--color-surface-muted)] px-4 py-3">
              <p class="text-xs uppercase tracking-wide text-gray-400">模式</p>
              <p class="mt-1 font-medium" :class="project.auto_mode !== false ? 'text-teal-700' : 'text-amber-700'">
                {{ project.auto_mode !== false ? '自动推进' : '手动控制' }}
              </p>
            </div>
          </div>
          <div class="mt-5">
            <div class="mb-2 flex items-center justify-between text-xs text-gray-500">
              <span>{{ getProgressLabel(project) }}</span>
              <span>{{ Math.round(getProgressWidth(project)) }}%</span>
            </div>
            <div class="h-2.5 w-full rounded-full bg-stone-200">
              <div class="h-2.5 rounded-full bg-gradient-to-r from-teal-700 via-emerald-600 to-amber-500" :style="{ width: `${getProgressWidth(project)}%` }"></div>
            </div>
          </div>
        </div>
        <div class="card-footer flex flex-col gap-1 bg-stone-50 text-xs text-gray-500 sm:flex-row sm:items-center sm:justify-between">
          <span>更新于：{{ formatUpdatedAt(project.updated_at) }}</span>
          <span class="font-medium">{{ project.style || '未设置风格' }}</span>
        </div>
      </div>
    </div>
  </div>
</template>
