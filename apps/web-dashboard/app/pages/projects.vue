<script setup lang="ts">
interface Project { id: string, name: string, domain: string, created_at?: string }

const api = useApi()
const projects = ref<Project[]>([])
const form = reactive({ name: '', domain: '' })
const error = ref<string | null>(null)
const busy = ref(false)

async function load() {
  try {
    projects.value = await api.get<Project[]>('/v1/projects')
    error.value = null
  } catch (failure) {
    error.value = (failure as Error).message
  }
}

async function create() {
  busy.value = true
  error.value = null
  try {
    await api.post('/v1/projects', { ...form })
    form.name = ''
    form.domain = ''
    await load()
  } catch (failure) {
    error.value = (failure as Error).message
  } finally {
    busy.value = false
  }
}

async function remove(project: Project) {
  // No modal: a project holds no results of its own — the crawls and reports
  // it groups outlive it — so this is not a destructive action worth a
  // confirmation dialog that people click through anyway.
  try {
    await api.del(`/v1/projects/${project.id}`)
    await load()
  } catch (failure) {
    error.value = (failure as Error).message
  }
}

onMounted(load)
</script>

<template>
  <div>
    <h1>پروژه‌ها</h1>
    <p class="lede">سایت‌هایی که دنبال می‌کنید. هر تحلیل می‌تواند به یکی از آن‌ها وصل شود.</p>

    <p v-if="error" class="error">{{ error }}</p>

    <section class="panel">
      <h2>پروژه‌ی تازه</h2>
      <form @submit.prevent="create">
        <label>
          <span>نام</span>
          <input v-model="form.name" required maxlength="120">
        </label>
        <label>
          <span>دامنه</span>
          <input v-model="form.domain" required maxlength="253" placeholder="example.com" class="ltr">
        </label>
        <button type="submit" :disabled="busy">افزودن</button>
      </form>
    </section>

    <section class="panel">
      <h2>فهرست</h2>
      <p v-if="!projects.length" class="muted">هنوز پروژه‌ای نیست.</p>
      <table v-else>
        <thead>
          <tr><th>نام</th><th>دامنه</th><th /></tr>
        </thead>
        <tbody>
          <tr v-for="project in projects" :key="project.id">
            <td>{{ project.name }}</td>
            <td class="ltr">{{ project.domain }}</td>
            <td><button class="ghost" @click="remove(project)">حذف</button></td>
          </tr>
        </tbody>
      </table>
    </section>
  </div>
</template>
