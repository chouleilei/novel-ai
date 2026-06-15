import { createRouter, createWebHistory } from 'vue-router'
import DefaultLayout from '@/layouts/DefaultLayout.vue'

const ROUTE_CHUNK_RELOAD_KEY = 'novel-ai:route-chunk-reload'
const RECOVERABLE_ROUTE_ERROR_MESSAGES = [
  'Failed to fetch dynamically imported module',
  'Importing a module script failed',
  'error loading dynamically imported module',
  'Unable to preload CSS',
]

const isRecoverableRouteChunkError = (error: unknown) => {
  const message = error instanceof Error ? error.message : String(error || '')
  const normalizedMessage = message.toLowerCase()

  return RECOVERABLE_ROUTE_ERROR_MESSAGES.some(pattern => normalizedMessage.includes(pattern.toLowerCase()))
}

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    {
      path: '/',
      component: DefaultLayout,
      children: [
        {
          path: '',
          name: 'home',
          component: () => import('@/views/ProjectList.vue'),
        },
        {
          path: 'system/settings',
          name: 'system-settings',
          component: () => import('@/views/SystemSettings.vue'),
        },
        {
          path: 'projects/new',
          name: 'project-new',
          component: () => import('@/views/ProjectSetup.vue'),
        },
        {
          path: 'projects/:id',
          name: 'project-detail',
          component: () => import('@/views/ProjectSetup.vue'),
        },
        {
          path: 'projects/:id/prompt/:chapterNumber',
          name: 'prompt-editor',
          component: () => import('@/views/PromptEditor.vue'),
        },
        {
          path: 'projects/:id/live',
          name: 'live-writing',
          component: () => import('@/views/LiveWriting.vue'),
        },
        {
          path: 'projects/:id/read',
          name: 'novel-reader',
          component: () => import('@/views/NovelReader.vue'),
        },
      ],
    },
  ],
})

router.onError((error, to) => {
  if (!isRecoverableRouteChunkError(error) || typeof window === 'undefined') {
    console.error('Route navigation failed', error)
    return
  }

  const failedPath = to.fullPath || to.path
  const previousFailedPath = window.sessionStorage.getItem(ROUTE_CHUNK_RELOAD_KEY)

  if (previousFailedPath === failedPath) {
    window.sessionStorage.removeItem(ROUTE_CHUNK_RELOAD_KEY)
    console.error('Route chunk recovery failed after reload', error)
    return
  }

  window.sessionStorage.setItem(ROUTE_CHUNK_RELOAD_KEY, failedPath)
  window.location.assign(failedPath)
})

router.afterEach((to, _from, failure) => {
  if (failure || typeof window === 'undefined') {
    return
  }

  if (window.sessionStorage.getItem(ROUTE_CHUNK_RELOAD_KEY) === to.fullPath) {
    window.sessionStorage.removeItem(ROUTE_CHUNK_RELOAD_KEY)
  }
})

export default router
