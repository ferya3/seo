<script setup lang="ts">
import type { Account } from '~/composables/useAuth'

const api = useApi()
const auth = useAuth()
const router = useRouter()

const email = ref('')
const password = ref('')
const error = ref<string | null>(null)
const busy = ref(false)

async function submit() {
  busy.value = true
  error.value = null
  try {
    const answer = await api.post<{ user: Account, token: string }>('/v1/auth/login', {
      email: email.value,
      password: password.value,
    })
    auth.signIn(answer.token, answer.user)
    router.push('/workflows')
  } catch (failure) {
    error.value = (failure as Error).message
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="panel" style="max-width: 420px; margin: 40px auto;">
    <h1>ورود</h1>
    <p class="lede">با همان حسابی که در گیت‌وی ساخته‌اید.</p>

    <p v-if="error" class="error">{{ error }}</p>

    <form @submit.prevent="submit">
      <label>
        <span>ایمیل</span>
        <input v-model="email" type="email" required autocomplete="username" class="ltr">
      </label>
      <label>
        <span>گذرواژه</span>
        <input v-model="password" type="password" required autocomplete="current-password">
      </label>
      <button type="submit" :disabled="busy">{{ busy ? 'در حال ورود…' : 'ورود' }}</button>
    </form>

    <p class="muted" style="margin-top: 16px;">
      حساب ندارید؟ <NuxtLink to="/register">ثبت‌نام کنید</NuxtLink>
    </p>
  </div>
</template>
