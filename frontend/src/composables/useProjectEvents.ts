import { ref, type Ref } from 'vue'
import api from '@/api'
import type { ProjectEvent } from '@/types/projectEvents'
import { normalizeProjectEvent, STREAM_PROJECT_EVENT_TYPES } from '@/utils/projectEvents'

type ProjectEventHandler = (event: ProjectEvent) => Promise<void> | void

export type ConnectionStatus = 'disconnected' | 'connecting' | 'connected' | 'reconnecting'

const MAX_RECONNECT_ATTEMPTS = 10
const BASE_RECONNECT_DELAY_MS = 1000
const MAX_RECONNECT_DELAY_MS = 30000

export const useProjectEvents = (projectId: Ref<string>) => {
  const events = ref<ProjectEvent[]>([])
  const lastEventId = ref(0)
  const connectionStatus = ref<ConnectionStatus>('disconnected')
  let eventSource: EventSource | null = null
  let reconnectAttempt = 0
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null

  const upsertEvent = (event: ProjectEvent) => {
    lastEventId.value = Math.max(lastEventId.value, event.id)
    events.value = [event, ...events.value.filter(item => item.id !== event.id)].slice(0, 50)
  }

  const fetchHistoricalEvents = async () => {
    try {
      const response = await api.get(`/projects/${projectId.value}/events`)
      const items = Array.isArray(response) ? response.map(normalizeProjectEvent) : []
      lastEventId.value = items[items.length - 1]?.id || 0
      events.value = items.reverse().slice(0, 50)
    } catch (error) {
      console.error('Failed to fetch events', error)
    }
  }

  const clearReconnectTimer = () => {
    if (reconnectTimer !== null) {
      clearTimeout(reconnectTimer)
      reconnectTimer = null
    }
  }

  const disconnect = () => {
    clearReconnectTimer()
    reconnectAttempt = 0
    if (eventSource) {
      eventSource.close()
      eventSource = null
    }
    connectionStatus.value = 'disconnected'
  }

  const reset = () => {
    disconnect()
    events.value = []
    lastEventId.value = 0
  }

  const handleIncomingEvent = (messageEvent: MessageEvent, onEvent: ProjectEventHandler) => {
    try {
      const event = normalizeProjectEvent(JSON.parse(messageEvent.data))
      upsertEvent(event)
      void Promise.resolve(onEvent(event)).catch(error => {
        console.error('Failed to apply SSE event', error)
      })
    } catch (error) {
      console.error('Failed to handle SSE payload', error)
    }
  }

  const getReconnectDelay = () => {
    const delay = Math.min(
      BASE_RECONNECT_DELAY_MS * Math.pow(2, reconnectAttempt),
      MAX_RECONNECT_DELAY_MS
    )
    return delay + Math.random() * 500
  }

  const connect = (onEvent: ProjectEventHandler) => {
    disconnect()
    connectionStatus.value = 'connecting'

    const afterId = lastEventId.value
    const source = new EventSource(`/api/projects/${projectId.value}/events/stream?after_id=${afterId}`)
    eventSource = source

    source.onopen = () => {
      reconnectAttempt = 0
      connectionStatus.value = 'connected'
    }

    source.onmessage = (messageEvent) => {
      handleIncomingEvent(messageEvent, onEvent)
    }

    for (const eventType of STREAM_PROJECT_EVENT_TYPES) {
      source.addEventListener(eventType, (messageEvent) => {
        handleIncomingEvent(messageEvent as MessageEvent, onEvent)
      })
    }

    source.onerror = () => {
      if (source.readyState === EventSource.CLOSED) {
        connectionStatus.value = 'disconnected'
      } else {
        connectionStatus.value = 'reconnecting'
      }

      source.close()
      eventSource = null

      if (reconnectAttempt < MAX_RECONNECT_ATTEMPTS) {
        const delay = getReconnectDelay()
        reconnectAttempt += 1
        reconnectTimer = setTimeout(() => {
          connect(onEvent)
        }, delay)
      } else {
        connectionStatus.value = 'disconnected'
        console.error('SSE: max reconnect attempts reached')
      }
    }
  }

  return {
    events,
    lastEventId,
    connectionStatus,
    fetchHistoricalEvents,
    connect,
    disconnect,
    reset,
  }
}
