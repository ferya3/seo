<script setup lang="ts">
interface Project { id: string, name: string, domain: string }

const api = useApi()
const router = useRouter()

const form = reactive({
  start_url: '',
  seed: '',
  max_pages: 30,
  track_keywords: 10,
  // One per line. A textarea rather than repeated inputs: people paste these
  // from a list they already have.
  competitors: '',
  project_id: '',
})

/** At most four, because each one is a crawl of somebody else's site. */
const MAX_COMPETITORS = 4

function competitorList(): string[] {
  return form.competitors
    .split(/[\n,]/)
    .map(line => line.trim())
    .filter(Boolean)
    .slice(0, MAX_COMPETITORS)
}

const projects = ref<Project[]>([])
const error = ref<string | null>(null)
const busy = ref(false)

onMounted(async () => {
  try {
    projects.value = await api.get<Project[]>('/v1/projects')
  } catch {
    // A tenant with no projects is normal, and a project is optional here.
  }
})

async function submit() {
  busy.value = true
  error.value = null
  try {
    // Empty strings are dropped rather than sent: the gateway validates
    // `seed` as a string when present, and "" is not a seed.
    const body: Record<string, unknown> = {
      start_url: form.start_url,
      max_pages: form.max_pages,
      track_keywords: form.track_keywords,
    }
    if (form.seed.trim()) body.seed = form.seed.trim()
    const rivals = competitorList()
    if (rivals.length) body.competitors = rivals
    if (form.project_id) body.project_id = form.project_id

    const accepted = await api.post<{ workflow_id: string }>('/v1/workflows', body)
    router.push(`/workflows/${accepted.workflow_id}`)
  } catch (failure) {
    error.value = (failure as Error).message
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="panel" style="max-width: 560px;">
    <h1>تحلیل تازه</h1>
    <p class="lede">
      سایت خزیده می‌شود، کلمات کلیدی استخراج و بعد جایگاهشان بررسی می‌شود.
      اگر رقیبی وارد کنید، هرکدام هم خزیده و با سایت شما مقایسه می‌شود.
    </p>

    <p v-if="error" class="error">{{ error }}</p>

    <form @submit.prevent="submit">
      <label>
        <span>آدرس شروع</span>
        <input v-model="form.start_url" required placeholder="https://example.com" class="ltr">
      </label>

      <label>
        <span>عبارت اولیه (اختیاری — از دامنه حدس زده می‌شود)</span>
        <input v-model="form.seed" placeholder="کفش ورزشی">
      </label>

      <label>
        <span>حداکثر صفحات</span>
        <input v-model.number="form.max_pages" type="number" min="1" max="100000">
      </label>

      <label>
        <!-- Each tracked keyword is a live search request downstream, which is
             why the gateway caps it at fifty. -->
        <span>چند کلمه رتبه‌سنجی شود (هرکدام یک جستجوی واقعی است)</span>
        <input v-model.number="form.track_keywords" type="number" min="1" max="50">
      </label>

      <label>
        <!-- Each competitor is a full crawl of someone else's site, run every
             time this analysis runs. -->
        <span>رقبا (اختیاری، هر خط یک سایت — حداکثر {{ MAX_COMPETITORS }})</span>
        <textarea
          v-model="form.competitors" rows="3" class="ltr"
          placeholder="https://rival-one.example&#10;rival-two.example"
        />
      </label>

      <label v-if="projects.length">
        <span>پروژه (اختیاری)</span>
        <select v-model="form.project_id">
          <option value="">بدون پروژه</option>
          <option v-for="project in projects" :key="project.id" :value="project.id">
            {{ project.name }} — {{ project.domain }}
          </option>
        </select>
      </label>

      <button type="submit" :disabled="busy">{{ busy ? 'در حال شروع…' : 'شروع تحلیل' }}</button>
    </form>
  </div>
</template>
