<script setup lang="ts">
// The dashboard has no home of its own: the list of analyses is the home.
const { signedIn } = useAuth()
await navigateTo(signedIn.value ? '/workflows' : '/login', { replace: true })
</script>

<template>
  <p class="muted">…</p>
</template>
