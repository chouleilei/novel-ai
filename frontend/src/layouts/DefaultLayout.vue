<script setup lang="ts">
import { RouterLink, RouterView } from 'vue-router'
import AuthGate from '@/components/AuthGate.vue'
</script>

<template>
  <AuthGate>
    <div class="min-h-screen flex flex-col">
      <header class="border-b border-[var(--color-border)] bg-white/90 backdrop-blur">
        <div class="mx-auto max-w-screen-2xl px-4 sm:px-6 lg:px-8">
          <div class="flex h-16 justify-between">
            <div class="flex">
              <div class="flex-shrink-0 flex items-center">
                <RouterLink to="/" class="text-lg font-semibold text-[var(--color-text)]">小说家</RouterLink>
              </div>
              <nav class="hidden sm:ml-8 sm:flex sm:space-x-6">
                <RouterLink
                  to="/"
                  class="border-transparent text-[var(--color-text-muted)] hover:border-[var(--color-border-strong)] hover:text-[var(--color-text)] inline-flex items-center px-1 pt-1 border-b-2 text-sm font-medium"
                  active-class="border-primary-500 text-[var(--color-text)]"
                >
                  项目列表
                </RouterLink>
                <RouterLink
                  to="/system/settings"
                  class="border-transparent text-[var(--color-text-muted)] hover:border-[var(--color-border-strong)] hover:text-[var(--color-text)] inline-flex items-center px-1 pt-1 border-b-2 text-sm font-medium"
                  active-class="border-primary-500 text-[var(--color-text)]"
                >
                  系统设置
                </RouterLink>
              </nav>
            </div>
          </div>
        </div>
      </header>

      <main class="mx-auto w-full max-w-screen-2xl flex-1 px-4 py-8 sm:px-6 lg:px-8">
        <RouterView />
      </main>
    </div>
  </AuthGate>
</template>
