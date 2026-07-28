<script setup lang="ts">
// The dashboard has no home of its own. The sites overview is the closest
// thing: it answers "which of my sites needs me", which is the question
// somebody opening a dashboard actually has.
import { HOME } from '~/utils/routes'

const { signedIn } = useAuth()
await navigateTo(signedIn.value ? HOME : '/login', { replace: true })
</script>

<template>
  <p class="muted">…</p>
</template>
