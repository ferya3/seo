<script setup lang="ts">
const auth = useAuth()
const router = useRouter()

async function signOut() {
  // Best effort: the token is revoked server-side if the call lands, and the
  // session ends locally either way. A failed logout must not leave someone
  // stuck on a page they wanted to leave.
  try {
    await useApi().post('/v1/auth/logout', {})
  } catch {
    // ignored on purpose — see above
  }
  auth.signOut()
  router.push('/login')
}
</script>

<template>
  <div>
    <header class="bar">
      <div class="shell">
        <NuxtLink to="/" class="brand">داشبورد سئو</NuxtLink>
        <nav v-if="auth.signedIn.value">
          <NuxtLink to="/workflows">تحلیل‌ها</NuxtLink>
          <NuxtLink to="/projects">پروژه‌ها</NuxtLink>
          <a href="#" @click.prevent="signOut">خروج</a>
        </nav>
      </div>
    </header>

    <main class="shell">
      <NuxtPage />
    </main>
  </div>
</template>
