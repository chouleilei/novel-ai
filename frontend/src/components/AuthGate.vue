<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { checkAuthSession, getSavedUsername, loginWithBasicAuth } from '@/utils/auth'

const authenticated = ref(false)
const checking = ref(true)
const submitting = ref(false)
const errorMessage = ref<string | null>(null)
const username = ref(getSavedUsername())
const password = ref('')

const verifySession = async () => {
  checking.value = true
  try {
    authenticated.value = await checkAuthSession()
  } catch {
    authenticated.value = false
  } finally {
    checking.value = false
  }
}

const submitLogin = async () => {
  if (!username.value.trim() || !password.value) {
    errorMessage.value = '请输入用户名和密码。'
    return
  }

  submitting.value = true
  errorMessage.value = null
  try {
    await loginWithBasicAuth({
      username: username.value.trim(),
      password: password.value,
    })
    password.value = ''
    authenticated.value = true
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '登录失败，请稍后重试。'
  } finally {
    submitting.value = false
  }
}

const handleAuthRequired = () => {
  authenticated.value = false
}

onMounted(() => {
  void verifySession()
  window.addEventListener('novel-ai:auth-required', handleAuthRequired)
})

onUnmounted(() => {
  window.removeEventListener('novel-ai:auth-required', handleAuthRequired)
})
</script>

<template>
  <div v-if="checking" class="flex min-h-screen items-center justify-center bg-[var(--color-background)] px-4">
    <div class="card w-full max-w-sm">
      <div class="card-body text-center text-sm text-[var(--color-text-muted)]">
        正在确认登录状态...
      </div>
    </div>
  </div>

  <slot v-else-if="authenticated" />

  <div v-else class="flex min-h-screen items-center justify-center bg-[var(--color-background)] px-4 py-10">
    <div class="w-full max-w-[420px]">
      <div class="mb-8 text-center">
        <p class="text-xs font-semibold uppercase tracking-[0.2em] text-[var(--color-text-muted)]">Novel AI</p>
        <h1 class="mt-3 text-3xl font-semibold text-[var(--color-text)]">登录创作工作台</h1>
        <p class="mt-3 text-sm text-[var(--color-text-muted)]">使用部署时配置的访问账号继续。</p>
      </div>

      <form class="card" autocomplete="on" @submit.prevent="submitLogin">
        <div class="card-body space-y-5">
          <div>
            <label for="novel-ai-username" class="block text-sm font-medium text-[var(--color-text)]">用户名</label>
            <input
              id="novel-ai-username"
              v-model="username"
              name="username"
              type="text"
              autocomplete="username"
              autocapitalize="none"
              spellcheck="false"
              class="input-field mt-2"
            />
          </div>

          <div>
            <label for="novel-ai-password" class="block text-sm font-medium text-[var(--color-text)]">密码</label>
            <input
              id="novel-ai-password"
              v-model="password"
              name="password"
              type="password"
              autocomplete="current-password"
              class="input-field mt-2"
            />
          </div>

          <div v-if="errorMessage" class="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700" role="alert">
            {{ errorMessage }}
          </div>
        </div>

        <div class="card-footer">
          <button type="submit" class="btn btn-primary w-full" :disabled="submitting">
            {{ submitting ? '正在登录...' : '登录' }}
          </button>
        </div>
      </form>
    </div>
  </div>
</template>
