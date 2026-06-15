<script setup lang="ts">
import { computed } from 'vue'
import type { ProjectEvent } from '@/types/projectEvents'
import { formatTime } from '@/utils/datetime'
import { compactTimelineEvents, formatProjectEventText } from '@/utils/projectEvents'

const props = defineProps<{
  events: ProjectEvent[]
}>()

const visibleEvents = computed(() => compactTimelineEvents(props.events).slice(0, 50))
</script>

<template>
  <div class="flex-1 overflow-y-auto bg-white p-3 text-xs">
    <div v-for="event in visibleEvents" :key="event.id" class="mb-2 grid grid-cols-[auto_1fr] gap-2 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface-muted)] px-3 py-2">
      <span class="mt-1 h-1.5 w-1.5 rounded-full bg-[var(--color-primary)]"></span>
      <div class="min-w-0">
        <div class="text-[11px] text-[var(--color-text-muted)]">{{ formatTime(event.created_at, '--:--:--') }}</div>
        <div class="mt-0.5 truncate text-[var(--color-text)]">{{ formatProjectEventText(event) }}</div>
      </div>
    </div>
  </div>
</template>
