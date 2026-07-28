<script setup lang="ts">
import type { Change } from '~/utils/format'
import { changeLabel, changeTone, isTerminal, positionLabel, scoreTone, statusLabel, stepLabel } from '~/utils/format'

interface Workflow {
  workflow_id: string
  goal: string
  status: string
  error: string | null
  created_at: string
  steps: { position: number, kind: string, job_id: string, status: string, error: string | null }[]
  report: Report | null
}

interface Report {
  headline?: Record<string, number | null>
  summary?: {
    source: 'rules' | 'ai'
    text_fa: string
    next_actions: { action: string, why: string, effort: string, impact: string }[]
    watch_outs?: string[]
    note?: string | null
  }
  crawl?: Record<string, unknown>
  keywords?: Record<string, unknown>
  optimizer?: {
    pages_with_fixes?: number
    fixes?: number
    written_by?: 'rules' | 'ai'
    pages?: { url: string, fixes: number }[]
    result_url?: string
  }
  content?: {
    keywords?: number
    covered?: number
    coverage?: number
    gap_count?: number
    cannibalisation_count?: number
    top_gaps?: { keyword: string, demand: number }[]
  }
  links?: {
    pages?: number
    internal_links?: number
    orphan_count?: number
    dead_end_count?: number
    broken_target_count?: number
    max_depth?: number
    average_inlinks?: number
    top_opportunities?: { url: string, inlinks: number, authority: number }[]
  }
  trend?: {
    compared_with?: string
    compared_at?: string
    changes?: Change[]
    better?: string[]
    worse?: string[]
    comparable_sample?: boolean
    note?: string | null
  }
  competitors?: {
    compared_against?: number
    behind_on?: string[]
    ahead_on?: string[]
    missing_theme_count?: number
    top_missing_themes?: string[]
    result_url?: string
  }
  rankings?: {
    target_domain?: string
    best?: { keyword: string, position: number } | null
    top_competitors?: { domain: string, outranks_on: number }[]
    opportunities?: { keyword: string, position: number | null, opportunity: number }[]
  }
}

const route = useRoute()
const api = useApi()

const workflow = ref<Workflow | null>(null)
const error = ref<string | null>(null)
const loading = ref(true)

async function load() {
  try {
    workflow.value = await api.get<Workflow>(`/v1/workflows/${route.params.id}`)
    error.value = null
  } catch (failure) {
    error.value = (failure as Error).message
  } finally {
    loading.value = false
  }
}

/*
 * A three-step audit takes minutes, so this page has to keep up on its own.
 * The interval stops the moment the workflow reaches a terminal state — and
 * once more a few seconds later, because the model-written summary is
 * attached just after the workflow finishes, off the lock.
 */
let timer: ReturnType<typeof setInterval> | undefined
let settledAt: number | null = null

onMounted(async () => {
  await load()
  timer = setInterval(async () => {
    const status = workflow.value?.status ?? 'queued'
    if (isTerminal(status)) {
      if (settledAt === null) settledAt = Date.now()
      if (Date.now() - settledAt > 20000) return clearInterval(timer)
    }
    await load()
  }, 4000)
})

onBeforeUnmount(() => clearInterval(timer))

/*
 * The document is the thing a person actually sends to someone. It is fetched
 * as bytes and handed to the browser as a download, rather than opened in a
 * tab: the gateway needs an Authorization header, and a plain link cannot
 * carry one.
 */
const downloading = ref(false)

async function download(format: 'html' | 'md') {
  downloading.value = true
  error.value = null
  try {
    const started = await api.post<{ report_id: string }>('/v1/reports', {
      workflow_id: route.params.id,
    })
    // The service renders in the background; a short wait beats a spinner
    // that needs its own state machine.
    await new Promise(resolve => setTimeout(resolve, 1500))

    const config = useRuntimeConfig()
    const response = await fetch(
      `${config.public.apiBase}/v1/reports/${started.report_id}/document?format=${format}`,
      { headers: { Authorization: `Bearer ${useAuth().token.value}`, Accept: '*/*' } },
    )
    if (!response.ok) throw new Error('گزارش هنوز آماده نیست. چند لحظه بعد دوباره امتحان کنید.')

    const url = URL.createObjectURL(await response.blob())
    const link = document.createElement('a')
    link.href = url
    link.download = `seo-report.${format}`
    link.click()
    URL.revokeObjectURL(url)
  } catch (failure) {
    error.value = (failure as Error).message
  } finally {
    downloading.value = false
  }
}

const report = computed(() => workflow.value?.report ?? null)
const summary = computed(() => report.value?.summary ?? null)

const tiles = computed(() => {
  const headline = report.value?.headline ?? {}
  return [
    { name: 'امتیاز کلی', value: headline.overall_score, tone: scoreTone(headline.overall_score) },
    { name: 'تعداد ایرادها', value: headline.total_issues },
    { name: 'کلمات کلیدی', value: headline.keywords_found },
    { name: 'رتبه‌گرفته', value: headline.keywords_ranked },
    { name: 'میانگین جایگاه', value: headline.average_position },
    { name: 'صفحه‌ی یتیم', value: headline.orphan_pages },
    { name: 'پوشش کلمات (٪)', value: headline.keyword_coverage },
    { name: 'صفحه برای بازنویسی', value: headline.pages_to_rewrite },
  ]
})
</script>

<template>
  <div>
    <div class="rowbar">
      <h1>گزارش تحلیل</h1>
      <span v-if="workflow" class="pill" :class="workflow.status">
        {{ statusLabel(workflow.status) }}
      </span>
      <div class="spacer" />
      <button
        v-if="workflow && isTerminal(workflow.status)"
        :disabled="downloading"
        @click="download('html')"
      >
        {{ downloading ? 'در حال ساخت…' : 'دانلود گزارش' }}
      </button>
      <button
        v-if="workflow && isTerminal(workflow.status)"
        class="ghost"
        :disabled="downloading"
        @click="download('md')"
      >
        Markdown
      </button>
      <NuxtLink to="/workflows"><button class="ghost">بازگشت</button></NuxtLink>
    </div>

    <p v-if="error" class="error">{{ error }}</p>
    <div v-if="loading" class="panel muted">در حال بارگذاری…</div>

    <template v-else-if="workflow">
      <p v-if="workflow.error" class="error">{{ workflow.error }}</p>

      <!-- The summary comes first because it is the answer; everything below
           it is the evidence. -->
      <section v-if="summary" class="panel">
        <h2>
          خلاصه
          <span class="pill">{{ summary.source === 'ai' ? 'نوشته‌ی مدل' : 'قانون‌محور' }}</span>
        </h2>
        <p class="summary">{{ summary.text_fa }}</p>

        <template v-if="summary.next_actions?.length">
          <h3>قدم‌های بعدی</h3>
          <ol class="actions">
            <li v-for="(action, index) in summary.next_actions" :key="index">
              <strong>{{ action.action }}</strong>
              <br>
              <span class="muted">
                {{ action.why }} — تلاش: {{ action.effort }}، اثر: {{ action.impact }}
              </span>
            </li>
          </ol>
        </template>

        <template v-if="summary.watch_outs?.length">
          <h3>چیزهایی که این گزارش نمی‌داند</h3>
          <ul>
            <li v-for="(item, index) in summary.watch_outs" :key="index" class="muted">{{ item }}</li>
          </ul>
        </template>

        <p v-if="summary.note" class="muted" style="margin-bottom: 0;">{{ summary.note }}</p>
      </section>

      <!-- Right under the summary: by the second run this is the first thing
           anyone looks for. -->
      <section v-if="report?.trend?.changes?.length" class="panel">
        <h2>نسبت به اجرای قبلی</h2>
        <p v-if="report.trend.note" class="lede">{{ report.trend.note }}</p>
        <table>
          <thead>
            <tr><th>سنجه</th><th>قبل</th><th>حالا</th><th>تغییر</th></tr>
          </thead>
          <tbody>
            <tr v-for="row in report.trend.changes" :key="row.metric">
              <td>{{ row.label_fa ?? row.metric }}</td>
              <td class="muted">{{ row.before }}</td>
              <td>{{ row.after }}</td>
              <td><span class="pill" :class="changeTone(row)">{{ changeLabel(row) }}</span></td>
            </tr>
          </tbody>
        </table>
      </section>

      <section v-if="report?.headline" class="panel">
        <h2>اعداد اصلی</h2>
        <div class="tiles">
          <div v-for="tile in tiles" :key="tile.name" class="tile">
            <div class="value" :class="tile.tone">{{ tile.value ?? '—' }}</div>
            <div class="name">{{ tile.name }}</div>
          </div>
        </div>
      </section>

      <section class="panel">
        <h2>مراحل</h2>
        <table>
          <tbody>
            <tr v-for="step in workflow.steps" :key="step.position">
              <td>{{ step.position }}</td>
              <td>{{ stepLabel(step.kind) }}</td>
              <td><span class="pill" :class="step.status">{{ statusLabel(step.status) }}</span></td>
              <td class="muted">{{ step.error ?? '' }}</td>
            </tr>
          </tbody>
        </table>
      </section>

      <section v-if="report?.rankings?.opportunities?.length" class="panel">
        <h2>فرصت‌ها</h2>
        <p class="lede">
          به ترتیب چیزی که بیشترین جا برای رشد دارد. عدد فرصت اولویت نسبی است،
          نه پیش‌بینی ترافیک.
        </p>
        <table>
          <thead>
            <tr><th>کلمه کلیدی</th><th>جایگاه فعلی</th><th>فرصت</th></tr>
          </thead>
          <tbody>
            <tr v-for="item in report.rankings.opportunities.slice(0, 15)" :key="item.keyword">
              <td>{{ item.keyword }}</td>
              <td class="muted">{{ positionLabel(item.position) }}</td>
              <td>{{ item.opportunity }}</td>
            </tr>
          </tbody>
        </table>
      </section>

      <section v-if="report?.optimizer?.pages_with_fixes" class="panel">
        <h2>
          پیشنهاد بازنویسی
          <span class="pill">
            {{ report.optimizer.written_by === 'ai' ? 'متن آماده' : 'دستورالعمل' }}
          </span>
        </h2>
        <p class="lede">
          {{ report.optimizer.fixes }} اصلاح مشخص روی
          {{ report.optimizer.pages_with_fixes }} صفحه. متن کامل هر پیشنهاد در
          خود سرویس بهینه‌سازی است.
        </p>
        <table>
          <thead>
            <tr><th>صفحه</th><th>تعداد اصلاح</th></tr>
          </thead>
          <tbody>
            <tr v-for="row in report.optimizer.pages" :key="row.url">
              <td class="ltr">{{ row.url }}</td>
              <td>{{ row.fixes }}</td>
            </tr>
          </tbody>
        </table>
      </section>

      <section v-if="report?.content?.keywords" class="panel">
        <h2>پوشش محتوا</h2>
        <p class="lede">
          کدام کلمه‌های کلیدیِ تحقیق‌شده صفحه‌ای دارند و کدام ندارند. تطبیق بر
          اساس عنوان، توضیح متا و تیترهاست — یعنی چیزی که صفحه درباره‌ی خودش
          اعلام می‌کند، نه متن بدنه.
        </p>
        <div class="tiles">
          <div class="tile">
            <div class="value">{{ report.content.coverage ?? '—' }}٪</div>
            <div class="name">پوشش</div>
          </div>
          <div class="tile">
            <div class="value" :class="report.content.gap_count ? 'fair' : 'good'">
              {{ report.content.gap_count ?? '—' }}
            </div>
            <div class="name">کلمه‌ی بدون صفحه</div>
          </div>
          <div class="tile">
            <div class="value" :class="report.content.cannibalisation_count ? 'poor' : 'good'">
              {{ report.content.cannibalisation_count ?? '—' }}
            </div>
            <div class="name">رقابت داخلی</div>
          </div>
        </div>

        <template v-if="report.content.top_gaps?.length">
          <h3>صفحه‌هایی که هنوز نوشته نشده‌اند</h3>
          <table>
            <thead>
              <tr><th>کلمه کلیدی</th><th>تقاضا</th></tr>
            </thead>
            <tbody>
              <tr v-for="gap in report.content.top_gaps" :key="gap.keyword">
                <td>{{ gap.keyword }}</td>
                <td class="muted">{{ gap.demand }}</td>
              </tr>
            </tbody>
          </table>
        </template>
      </section>

      <section v-if="report?.competitors?.compared_against" class="panel">
        <h2>مقایسه با رقبا</h2>
        <p class="lede">
          مقایسه روی نرخ‌ها و میانه‌هاست، نه تعداد صفحه‌ها — هر خزش سقف دارد و
          شمردن صفحه‌ها اندازه‌ی سقف را می‌سنجد نه اندازه‌ی سایت.
        </p>
        <div class="tiles">
          <div class="tile">
            <div class="value">{{ report.competitors.compared_against }}</div>
            <div class="name">رقیب مقایسه‌شده</div>
          </div>
          <div class="tile">
            <div class="value" :class="report.competitors.behind_on?.length ? 'poor' : 'good'">
              {{ report.competitors.behind_on?.length ?? 0 }}
            </div>
            <div class="name">سنجه‌ای که عقب‌اید</div>
          </div>
          <div class="tile">
            <div class="value" :class="report.competitors.ahead_on?.length ? 'good' : ''">
              {{ report.competitors.ahead_on?.length ?? 0 }}
            </div>
            <div class="name">سنجه‌ای که جلوترید</div>
          </div>
        </div>

        <template v-if="report.competitors.top_missing_themes?.length">
          <h3>موضوع‌هایی که رقبا پوشش می‌دهند و شما نه</h3>
          <p class="muted small">
            عبارتی اینجا می‌آید که دست‌کم دو رقیب رویش نوشته باشند و در سایت شما نباشد.
          </p>
          <p>
            <span v-for="term in report.competitors.top_missing_themes" :key="term" class="pill">
              {{ term }}
            </span>
          </p>
        </template>
      </section>

      <section v-if="report?.links?.pages" class="panel">
        <h2>لینک‌های داخلی</h2>
        <div class="tiles">
          <div class="tile">
            <div class="value">{{ report.links.internal_links ?? '—' }}</div>
            <div class="name">لینک داخلی</div>
          </div>
          <div class="tile">
            <div class="value" :class="report.links.orphan_count ? 'poor' : 'good'">
              {{ report.links.orphan_count ?? '—' }}
            </div>
            <div class="name">صفحه‌ی یتیم</div>
          </div>
          <div class="tile">
            <div class="value" :class="report.links.broken_target_count ? 'poor' : 'good'">
              {{ report.links.broken_target_count ?? '—' }}
            </div>
            <div class="name">لینک به صفحه‌ی خراب</div>
          </div>
          <div class="tile">
            <div class="value">{{ report.links.average_inlinks ?? '—' }}</div>
            <div class="name">میانگین لینک ورودی</div>
          </div>
        </div>

        <template v-if="report.links.top_opportunities?.length">
          <h3>صفحاتی که لینک داخلی کم دارند</h3>
          <p class="lede">
            محتوا دارند ولی سایت خودش به آن‌ها کم اشاره می‌کند. یک لینک از یک
            صفحه‌ی قوی، ارزان‌ترین کاری است که می‌شود برایشان کرد.
          </p>
          <table>
            <thead>
              <tr><th>صفحه</th><th>لینک ورودی</th><th>اعتبار داخلی</th></tr>
            </thead>
            <tbody>
              <tr v-for="row in report.links.top_opportunities" :key="row.url">
                <td class="ltr">{{ row.url }}</td>
                <td>{{ row.inlinks }}</td>
                <td class="muted">{{ row.authority }}</td>
              </tr>
            </tbody>
          </table>
        </template>
      </section>

      <section v-if="report?.rankings?.top_competitors?.length" class="panel">
        <h2>رقبا</h2>
        <table>
          <thead>
            <tr><th>دامنه</th><th>روی چند عبارت بالاتر است</th></tr>
          </thead>
          <tbody>
            <tr v-for="rival in report.rankings.top_competitors" :key="rival.domain">
              <td class="ltr">{{ rival.domain }}</td>
              <td>{{ rival.outranks_on }}</td>
            </tr>
          </tbody>
        </table>
      </section>
    </template>
  </div>
</template>
