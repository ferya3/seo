<script setup lang="ts">
import { isTerminal, scoreTone, since, statusLabel } from '~/utils/format'

interface WorkflowRow {
  workflow_id: string
  goal: string
  status: string
  error: string | null
  created_at: string
  start_url: string | null
  overall_score: number | null
  trend: { better: number, worse: number } | null
  steps: { position: number, kind: string, status: string }[]
}

/** The site, without the scheme and the trailing slash that add nothing. */
function siteOf(row: WorkflowRow): string {
  if (!row.start_url) return '—'
  return row.start_url.replace(/^https?:\/\//, '').replace(/\/$/, '')
}

const api = useApi()
const rows = ref<WorkflowRow[]>([])
const error = ref<string | null>(null)
const loading = ref(true)

async function load() {
  try {
    rows.value = await api.get<WorkflowRow[]>('/v1/workflows')
    error.value = null
  } catch (failure) {
    error.value = (failure as Error).message
  } finally {
    loading.value = false
  }
}

/*
 * Poll only while something is actually moving, and stop when nothing is.
 * A dashboard left open on a finished list should not keep asking.
 */
let timer: ReturnType<typeof setInterval> | undefined

onMounted(async () => {
  await load()
  timer = setInterval(async () => {
    if (rows.value.every(row => isTerminal(row.status))) return
    await load()
  }, 5000)
})

onBeforeUnmount(() => clearInterval(timer))
</script>

<template>
  <div>
    <div class="rowbar">
      <h1>تحلیل‌ها</h1>
      <div class="spacer" />
      <NuxtLink to="/workflows/new"><button>تحلیل تازه</button></NuxtLink>
    </div>

    <p v-if="error" class="error">{{ error }}</p>

    <div v-if="loading" class="panel muted">در حال بارگذاری…</div>

    <div v-else-if="!rows.length" class="panel">
      <p class="lede">هنوز تحلیلی اجرا نشده.</p>
      <NuxtLink to="/workflows/new">اولین تحلیل را شروع کنید</NuxtLink>
    </div>

    <div v-else class="panel">
      <table>
        <thead>
          <tr>
            <th>سایت</th>
            <th>امتیاز</th>
            <th>نسبت به قبل</th>
            <th>وضعیت</th>
            <th>مراحل</th>
            <th>شروع</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in rows" :key="row.workflow_id">
            <td>
              <NuxtLink :to="`/workflows/${row.workflow_id}`" class="ltr">
                {{ siteOf(row) }}
              </NuxtLink>
            </td>
            <td>
              <span v-if="row.overall_score !== null" class="pill" :class="scoreTone(row.overall_score)">
                {{ row.overall_score }}
              </span>
              <span v-else class="muted">—</span>
            </td>
            <td class="small">
              <!-- Absent on a first run, which is not the same as level. -->
              <template v-if="row.trend">
                <span v-if="row.trend.better" class="pill good">{{ row.trend.better }} بهتر</span>
                <span v-if="row.trend.worse" class="pill poor">{{ row.trend.worse }} بدتر</span>
                <span v-if="!row.trend.better && !row.trend.worse" class="muted">بدون تغییر</span>
              </template>
              <span v-else class="muted">—</span>
            </td>
            <td><span class="pill" :class="row.status">{{ statusLabel(row.status) }}</span></td>
            <td class="muted">
              {{ row.steps.filter(s => s.status === 'completed').length }} از {{ row.steps.length }}
            </td>
            <td class="muted">{{ since(row.created_at) }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
