<script setup lang="ts">
import type { ProviderChannel } from '@/types'
import { PlusIcon, PencilSquareIcon, TrashIcon, CheckCircleIcon, XMarkIcon } from '@heroicons/vue/20/solid'

const props = defineProps<{
  channels: ProviderChannel[]
  loading?: boolean
}>()

const emit = defineEmits<{
  create: []
  edit: [channel: ProviderChannel]
  delete: [id: string]
}>()

const handleDelete = (channel: ProviderChannel) => {
  if (window.confirm(`确定要删除渠道 "${channel.name}" 吗？此操作不可恢复。`)) {
    emit('delete', channel.id)
  }
}
</script>

<template>
  <div class="card">
    <div class="card-header flex items-center justify-between">
      <h3 class="text-lg font-medium leading-6 text-gray-900">模型服务渠道</h3>
      <button class="btn btn-primary text-sm flex items-center gap-1" @click="emit('create')" :disabled="loading">
        <PlusIcon class="h-4 w-4" />
        新增渠道
      </button>
    </div>

    <div class="card-body p-0">
      <div v-if="loading && channels.length === 0" class="p-8 text-center text-gray-500">
        加载中...
      </div>
      
      <div v-else-if="channels.length === 0" class="p-12 text-center">
        <div class="mx-auto h-12 w-12 text-gray-400">
          <svg fill="none" viewBox="0 0 24 24" stroke="currentColor" aria-hidden="true">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M19.428 15.428a2 2 0 00-1.022-.547l-2.387-.477a6 6 0 00-3.86.517l-.318.158a6 6 0 01-3.86.517L6.05 15.21a2 2 0 00-1.806.547M8 4h8l-1 1v5.172a2 2 0 00.586 1.414l5 5c1.26 1.26.367 3.414-1.415 3.414H4.828c-1.782 0-2.674-2.154-1.414-3.414l5-5A2 2 0 009 10.172V5L8 4z" />
          </svg>
        </div>
        <h3 class="mt-2 text-sm font-medium text-gray-900">暂无渠道</h3>
        <p class="mt-1 text-sm text-gray-500">开始添加您的第一个模型服务渠道。</p>
        <div class="mt-6">
          <button class="btn btn-primary" @click="emit('create')">
            <PlusIcon class="h-4 w-4 mr-1 inline" />
            新增渠道
          </button>
        </div>
      </div>

      <div v-else class="overflow-x-auto">
        <table class="min-w-full divide-y divide-[var(--color-border)]">
          <thead class="bg-stone-50">
            <tr>
              <th scope="col" class="py-3.5 pl-4 pr-3 text-left text-sm font-semibold text-gray-900 sm:pl-6">名称</th>
              <th scope="col" class="px-3 py-3.5 text-left text-sm font-semibold text-gray-900">供应商</th>
              <th scope="col" class="px-3 py-3.5 text-left text-sm font-semibold text-gray-900">状态</th>
              <th scope="col" class="px-3 py-3.5 text-left text-sm font-semibold text-gray-900">默认模型</th>
              <th scope="col" class="px-3 py-3.5 text-left text-sm font-semibold text-gray-900">Base URL</th>
              <th scope="col" class="relative py-3.5 pl-3 pr-4 sm:pr-6">
                <span class="sr-only">操作</span>
              </th>
            </tr>
          </thead>
          <tbody class="divide-y divide-[var(--color-border)] bg-white">
            <tr v-for="channel in channels" :key="channel.id" class="hover:bg-stone-50 transition-colors">
              <td class="whitespace-nowrap py-4 pl-4 pr-3 text-sm font-medium text-gray-900 sm:pl-6">
                {{ channel.name }}
              </td>
              <td class="whitespace-nowrap px-3 py-4 text-sm text-gray-500">
                <span v-if="channel.provider === 'openai_compatible'" class="inline-flex items-center rounded-md bg-blue-50 px-2 py-1 text-xs font-medium text-blue-700 ring-1 ring-inset ring-blue-700/10">OpenAI 兼容</span>
                <span v-else-if="channel.provider === 'mock'" class="inline-flex items-center rounded-md bg-gray-50 px-2 py-1 text-xs font-medium text-gray-600 ring-1 ring-inset ring-gray-500/10">Mock</span>
                <span v-else class="inline-flex items-center rounded-md bg-gray-50 px-2 py-1 text-xs font-medium text-gray-600 ring-1 ring-inset ring-gray-500/10">{{ channel.provider }}</span>
              </td>
              <td class="whitespace-nowrap px-3 py-4 text-sm text-gray-500">
                <span v-if="channel.is_enabled" class="inline-flex items-center gap-1 text-emerald-600">
                  <CheckCircleIcon class="h-4 w-4" /> 启用
                </span>
                <span v-else class="inline-flex items-center gap-1 text-gray-400">
                  <XMarkIcon class="h-4 w-4" /> 停用
                </span>
              </td>
              <td class="whitespace-nowrap px-3 py-4 text-sm text-gray-500">
                {{ channel.default_model_name || '-' }}
              </td>
              <td class="whitespace-nowrap px-3 py-4 text-sm text-gray-500">
                <div class="max-w-[200px] truncate" :title="channel.base_url">
                  {{ channel.base_url || '-' }}
                </div>
              </td>
              <td class="relative whitespace-nowrap py-4 pl-3 pr-4 text-right text-sm font-medium sm:pr-6">
                <div class="flex items-center justify-end gap-2">
                  <button class="text-primary-600 hover:text-primary-900 p-1" title="编辑" @click="emit('edit', channel)">
                    <PencilSquareIcon class="h-5 w-5" />
                  </button>
                  <button class="text-red-600 hover:text-red-900 p-1" title="删除" @click="handleDelete(channel)">
                    <TrashIcon class="h-5 w-5" />
                  </button>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>
</template>
