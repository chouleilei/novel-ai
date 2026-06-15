import { defineStore } from 'pinia'
import { ref } from 'vue'
import api from '@/api'
import type { Project, ModelConfig, ChapterOutline, ModelConnectivityResult } from '@/types'

export const useProjectStore = defineStore('project', () => {
  const projects = ref<Project[]>([])
  const currentProject = ref<Project | null>(null)
  const loading = ref(false)
  const error = ref<string | null>(null)

  const getErrorMessage = (err: any, fallback: string) => {
    return err?.response?.data?.detail || err?.message || fallback
  }

  const upsertProject = (project: Project) => {
    const index = projects.value.findIndex(item => item.id === project.id)
    if (index >= 0) {
      projects.value[index] = {
        ...projects.value[index],
        ...project,
      }
      return
    }
    projects.value.unshift(project)
  }

  const fetchProjects = async () => {
    loading.value = true
    error.value = null
    try {
      const response = await api.get('/projects')
      projects.value = response as unknown as Project[]
    } catch (err: any) {
      error.value = getErrorMessage(err, 'Failed to fetch projects')
    } finally {
      loading.value = false
    }
  }

  const fetchProject = async (id: string) => {
    loading.value = true
    error.value = null
    try {
      const response = await api.get(`/projects/${id}`)
      currentProject.value = response as unknown as Project
      upsertProject(currentProject.value)
    } catch (err: any) {
      currentProject.value = null
      error.value = getErrorMessage(err, 'Failed to fetch project')
    } finally {
      loading.value = false
    }
  }

  const createProject = async (data: Partial<Project>) => {
    loading.value = true
    error.value = null
    try {
      const response = await api.post('/projects', data)
      const created = response as unknown as { id: string }
      await fetchProject(created.id)
      return currentProject.value as Project
    } catch (err: any) {
      error.value = getErrorMessage(err, 'Failed to create project')
      throw err
    } finally {
      loading.value = false
    }
  }

  const updateProject = async (id: string, data: Partial<Project>) => {
    loading.value = true
    error.value = null
    try {
      const response = await api.put(`/projects/${id}`, data)
      currentProject.value = response as unknown as Project
      upsertProject(currentProject.value)
      return currentProject.value
    } catch (err: any) {
      error.value = getErrorMessage(err, 'Failed to update project')
      throw err
    } finally {
      loading.value = false
    }
  }

  const deleteProject = async (id: string) => {
    loading.value = true
    error.value = null
    try {
      await api.delete(`/projects/${id}`)
      projects.value = projects.value.filter(item => item.id !== id)
      if (currentProject.value?.id === id) {
        currentProject.value = null
      }
    } catch (err: any) {
      error.value = getErrorMessage(err, 'Failed to delete project')
      throw err
    } finally {
      loading.value = false
    }
  }

  const copyProject = async (id: string) => {
    loading.value = true
    error.value = null
    try {
      const response = await api.post(`/projects/${id}/copy`)
      const copiedProject = response as unknown as Project
      currentProject.value = copiedProject
      upsertProject(copiedProject)
      return copiedProject
    } catch (err: any) {
      error.value = getErrorMessage(err, 'Failed to copy project')
      throw err
    } finally {
      loading.value = false
    }
  }

  const fetchModels = async (id: string) => {
    error.value = null
    try {
      const response = await api.get(`/projects/${id}/models`)
      return response as unknown as ModelConfig[]
    } catch (err: any) {
      error.value = getErrorMessage(err, 'Failed to fetch models')
      throw err
    }
  }

  const fetchProjectDefaults = async () => {
    error.value = null
    try {
      const response = await api.get('/projects/defaults')
      return response as unknown as Partial<Project>
    } catch (err: any) {
      error.value = getErrorMessage(err, 'Failed to fetch project defaults')
      throw err
    }
  }

  const fetchOutlines = async (id: string) => {
    error.value = null
    try {
      const response = await api.get(`/projects/${id}/outlines`)
      return response as unknown as ChapterOutline[]
    } catch (err: any) {
      error.value = getErrorMessage(err, 'Failed to fetch outlines')
      throw err
    }
  }

  const updateModels = async (id: string, models: ModelConfig[]) => {
    loading.value = true
    error.value = null
    try {
      await api.put(`/projects/${id}/models`, models)
    } catch (err: any) {
      error.value = getErrorMessage(err, 'Failed to update models')
      throw err
    } finally {
      loading.value = false
    }
  }

  const testModelConnectivity = async (id: string, model: ModelConfig) => {
    error.value = null
    try {
      const response = await api.post(`/projects/${id}/models/test`, model)
      return response as unknown as ModelConnectivityResult
    } catch (err: any) {
      const detail = err?.response?.data?.detail
      if (detail && typeof detail === 'object') {
        return detail as ModelConnectivityResult
      }
      error.value = getErrorMessage(err, 'Failed to test model connectivity')
      throw err
    }
  }

  const importOutlines = async (id: string, outlines: ChapterOutline[]) => {
    loading.value = true
    error.value = null
    try {
      await api.post(`/projects/${id}/outlines/import`, outlines)
      await fetchProject(id)
    } catch (err: any) {
      error.value = getErrorMessage(err, 'Failed to import outlines')
      throw err
    } finally {
      loading.value = false
    }
  }

  const startGeneration = async (id: string) => {
    loading.value = true
    error.value = null
    try {
      await api.post(`/projects/${id}/start`)
      await fetchProject(id)
    } catch (err: any) {
      error.value = getErrorMessage(err, 'Failed to start generation')
      throw err
    } finally {
      loading.value = false
    }
  }

  const pauseGeneration = async (id: string) => {
    loading.value = true
    error.value = null
    try {
      await api.post(`/projects/${id}/pause`)
      await fetchProject(id)
    } catch (err: any) {
      error.value = getErrorMessage(err, 'Failed to pause generation')
      throw err
    } finally {
      loading.value = false
    }
  }

  const resumeGeneration = async (id: string) => {
    loading.value = true
    error.value = null
    try {
      await api.post(`/projects/${id}/resume`)
      await fetchProject(id)
    } catch (err: any) {
      error.value = getErrorMessage(err, 'Failed to resume generation')
      throw err
    } finally {
      loading.value = false
    }
  }

  const retryChapter = async (id: string, chapterNumber: number) => {
    loading.value = true
    error.value = null
    try {
      await api.post(`/projects/${id}/chapters/${chapterNumber}/retry`)
      await fetchProject(id)
    } catch (err: any) {
      error.value = getErrorMessage(err, 'Failed to retry chapter')
      throw err
    } finally {
      loading.value = false
    }
  }

  const rewriteFromChapter = async (id: string, chapterNumber: number) => {
    loading.value = true
    error.value = null
    try {
      await api.post(`/projects/${id}/chapters/${chapterNumber}/rewrite-from-here`)
      await fetchProject(id)
    } catch (err: any) {
      error.value = getErrorMessage(err, 'Failed to rewrite from chapter')
      throw err
    } finally {
      loading.value = false
    }
  }

  const manualApproveChapter = async (id: string, chapterNumber: number) => {
    loading.value = true
    error.value = null
    try {
      await api.post(`/projects/${id}/chapters/${chapterNumber}/manual-approve`)
      await fetchProject(id)
    } catch (err: any) {
      error.value = getErrorMessage(err, 'Failed to manual approve chapter')
      throw err
    } finally {
      loading.value = false
    }
  }

  const continueChapter = async (id: string, chapterNumber: number) => {
    loading.value = true
    error.value = null
    try {
      await api.post(`/projects/${id}/chapters/${chapterNumber}/continue`)
      await fetchProject(id)
    } catch (err: any) {
      error.value = getErrorMessage(err, 'Failed to continue chapter from breakpoint')
      throw err
    } finally {
      loading.value = false
    }
  }

  const resetProjectScope = () => {
    currentProject.value = null
    error.value = null
    loading.value = false
  }

  return {
    projects,
    currentProject,
    loading,
    error,
    fetchProjects,
    fetchProject,
    createProject,
    updateProject,
    deleteProject,
    copyProject,
    fetchModels,
    fetchProjectDefaults,
    fetchOutlines,
    updateModels,
    testModelConnectivity,
    importOutlines,
    startGeneration,
    pauseGeneration,
    resumeGeneration,
    retryChapter,
    rewriteFromChapter,
    manualApproveChapter,
    continueChapter,
    resetProjectScope,
  }
})
