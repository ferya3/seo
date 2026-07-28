<script setup lang="ts">
import { isTerminal, scoreTone, since, statusLabel } from '~/utils/format'

/*
 * One row per site, not per run.
 *
 * Every other page in this dashboard answers a question about one audit. The
 * question somebody with four sites has on a Monday morning is a different
 * one — which of them needs me — and until now the only way to answer it was
 * to open four reports.
 *
 * Grouped in the browser rather than by a new endpoint: the list of audits
 * already carries the site, the score and the direction, and a "sites"
 * endpoint would be a second place that decides what a site's current state
 * is. Which is exactly the sort of thing that ends up disagreeing.
 */

interface WorkflowRow {
  workflow_id: string
  status: string
  created_at: string
  start_url: string | null
  overall_score: number | null
  trend: { better: number, worse: number } | null
}

interface Site {
  url: string
  label: string
  runs: number
  latest: WorkflowRow            // the newest run, whatever happened to it
  scored: WorkflowRow | null     // the newest one that produced a score
}

const api = useApi()
const rows = ref<WorkflowRow[]>([])
const error = ref<string | null>(null)
const loading = ref(true)

/*
 * Two "latest" per site, deliberately.
 *
 * The newest run is what the status column shows — if last night's audit
 * failed, that is the news. The newest *scored* run is where the number comes
 * from, because showing no score at all when one run failed would throw away
 * everything known about the site. The row says how old that number is rather
 * than letting it pass for current.
 */
const sites = computed<Site[]>(() => {
  const byUrl = new Map<string, WorkflowRow[]>()

  for (const row of rows.value) {
    if (!row.start_url) continue
    const list = byUrl.get(row.start_url) ?? []
    list.push(row)
    byUrl.set(row.start_url, list)
  }

  return [...byUrl.entries()]
    .map(([url, list]) => {
      const ordered = [...list].sort((a, b) => b.created_at.localeCompare(a.created_at))
      return {
        url,
        label: url.replace(/^https?:\/\//, '').replace(/\/$/, ''),
        runs: ordered.length,
        latest: ordered[0]!,
        scored: ordered.find(row => row.overall_score !== null) ?? null,
      }
    })
    // Worst score first: the page exists to say which site needs attention,
    // and alphabetical order says nothing.
    .sort((a, b) => (a.scored?.overall_score ?? 101) - (b.scored?.overall_score ?? 101))
})

async function load() {
  try {
    // A hundred runs is plenty to work out the current state of every site,
    // and this page is a summary rather than a history.
    rows.value = await api.get<WorkflowRow[]>('/v1/workflows?limit=100')
    error.value = null
  } catch (failure) {
    error.value = (failure as Error).message
  } finally {
    loading.value = false
  }
}

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
      <h1>سایت‌ها</h1>
      <div class="spacer" />
      <NuxtLink to="/workflows/new"><button>تحلیل تازه</button></NuxtLink>
    </div>

    <p v-if="error" class="error">{{ error }}</p>
    <div v-if="loading" class="panel muted">در حال بارگذاری…</div>

    <div v-else-if="!sites.length" class="panel">
      <p class="lede">هنوز هیچ سایتی تحلیل نشده.</p>
      <NuxtLink to="/workflows/new">اولین تحلیل را شروع کنید</NuxtLink>
    </div>

    <div v-else class="panel">
      <p class="lede">
        هر سایت یک ردیف، بدترین امتیاز اول — چون سؤال این صفحه این است که کدام
        سایت به شما نیاز دارد.
      </p>
      <table>
        <thead>
          <tr>
            <th>سایت</th>
            <th>امتیاز</th>
            <th>نسبت به قبل</th>
            <th>آخرین اجرا</th>
            <th>اجراها</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="site in sites" :key="site.url">
            <td>
              <!--
                To the run the score came from, not simply the newest one.
                When last night's audit failed, the newest run is an empty
                report; sending someone there from a row headlined 66 shows
                them a page that does not contain the number they clicked.
              -->
              <NuxtLink :to="`/workflows/${(site.scored ?? site.latest).workflow_id}`" class="ltr">
                {{ site.label }}
              </NuxtLink>
            </td>
            <td>
              <span v-if="site.scored" class="pill" :class="scoreTone(site.scored.overall_score)">
                {{ site.scored.overall_score }}
              </span>
              <span v-else class="muted">—</span>
              <!-- Said out loud when the number is not from the latest run,
                   rather than letting an old score pass for current. -->
              <div
                v-if="site.scored && site.scored.workflow_id !== site.latest.workflow_id"
                class="muted small"
              >
                از {{ since(site.scored.created_at) }}
              </div>
            </td>
            <td class="small">
              <template v-if="site.scored?.trend">
                <span v-if="site.scored.trend.better" class="pill good">
                  {{ site.scored.trend.better }} بهتر
                </span>
                <span v-if="site.scored.trend.worse" class="pill poor">
                  {{ site.scored.trend.worse }} بدتر
                </span>
                <span v-if="!site.scored.trend.better && !site.scored.trend.worse" class="muted">
                  بدون تغییر
                </span>
              </template>
              <span v-else class="muted">—</span>
            </td>
            <td>
              <!-- And the status column leads to the run it is describing. -->
              <NuxtLink :to="`/workflows/${site.latest.workflow_id}`">
                <span class="pill" :class="site.latest.status">
                  {{ statusLabel(site.latest.status) }}
                </span>
              </NuxtLink>
              <div class="muted small">{{ since(site.latest.created_at) }}</div>
            </td>
            <td class="muted">{{ site.runs }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
