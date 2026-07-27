<script setup lang="ts">
import type { Account } from '~/composables/useAuth'

const api = useApi()
const auth = useAuth()
const router = useRouter()

const form = reactive({ name: '', email: '', password: '', tenant_name: '' })
const error = ref<string | null>(null)
const busy = ref(false)

async function submit() {
  busy.value = true
  error.value = null
  try {
    const answer = await api.post<{ user: Account, token: string }>('/v1/auth/register', { ...form })
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
  <div class="panel" style="max-width: 460px; margin: 40px auto;">
    <h1>ثبت‌نام</h1>
    <p class="lede">یک حساب و یک تنانت ساخته می‌شود؛ همه‌ی داده‌های شما زیر همان می‌ماند.</p>

    <p v-if="error" class="error">{{ error }}</p>

    <form @submit.prevent="submit">
      <label>
        <span>نام شما</span>
        <input v-model="form.name" required>
      </label>
      <label>
        <span>نام حساب (تنانت)</span>
        <input v-model="form.tenant_name" required>
      </label>
      <label>
        <span>ایمیل</span>
        <input v-model="form.email" type="email" required autocomplete="username" class="ltr">
      </label>
      <label>
        <!-- The gateway requires twelve characters; saying so beforehand beats
             a validation error after the form is filled in. -->
        <span>گذرواژه (دست‌کم ۱۲ نویسه)</span>
        <input v-model="form.password" type="password" minlength="12" required autocomplete="new-password">
      </label>
      <button type="submit" :disabled="busy">{{ busy ? 'در حال ساخت…' : 'ساخت حساب' }}</button>
    </form>

    <p class="muted" style="margin-top: 16px;">
      حساب دارید؟ <NuxtLink to="/login">وارد شوید</NuxtLink>
    </p>
  </div>
</template>
