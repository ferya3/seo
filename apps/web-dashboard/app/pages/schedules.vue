<script setup lang="ts">
import { cadenceLabel, runAtLabel, since, weekdayLabel } from '~/utils/format'

/*
 * Schedules, from a screen rather than from curl.
 *
 * The API has been there since the scheduler shipped; this is the part that
 * decides what someone sets up. Two things drive the layout: the weekday and
 * day-of-month fields only mean something for one cadence each, so they appear
 * only then rather than sitting there greyed out, and `next_run_at` is shown
 * for every row — a schedule you cannot check is a schedule you do not trust,
 * and it is the one value that proves the cadence was read the way it was
 * meant.
 */

interface Project { id: string, name: string, domain: string }

interface Schedule {
  id: string
  goal: string
  inputs: Record<string, unknown>
  cadence: string
  hour: number
  weekday: number
  day_of_month: number
  timezone: string
  active: boolean
  next_run_at: string | null
  last_run_at: string | null
  runs: number
}

const api = useApi()

const schedules = ref<Schedule[]>([])
const projects = ref<Project[]>([])
const error = ref<string | null>(null)
const busy = ref(false)
const loaded = ref(false)

const form = reactive({
  start_url: '',
  cadence: 'weekly',
  hour: 9,
  weekday: 0,
  day_of_month: 1,
  // The browser knows where the person is; a schedule set up in Berlin should
  // not silently mean nine o'clock in Tehran.
  timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || 'Asia/Tehran',
  max_pages: 30,
  track_keywords: 10,
  project_id: '',
})

async function load() {
  try {
    schedules.value = await api.get<Schedule[]>('/v1/schedules')
    error.value = null
  } catch (failure) {
    error.value = (failure as Error).message
  } finally {
    loaded.value = true
  }
}

onMounted(async () => {
  await load()
  try {
    projects.value = await api.get<Project[]>('/v1/projects')
  } catch {
    // A project is optional here, exactly as it is on a one-off analysis.
  }
})

async function create() {
  busy.value = true
  error.value = null
  try {
    const body: Record<string, unknown> = {
      start_url: form.start_url,
      cadence: form.cadence,
      hour: form.hour,
      timezone: form.timezone,
      max_pages: form.max_pages,
      track_keywords: form.track_keywords,
    }
    // Only the field the chosen cadence uses. Sending weekday: 0 with a
    // monthly schedule is noise the store would have to ignore.
    if (form.cadence === 'weekly') body.weekday = form.weekday
    if (form.cadence === 'monthly') body.day_of_month = form.day_of_month
    if (form.project_id) body.project_id = form.project_id

    await api.post('/v1/schedules', body)
    form.start_url = ''
    await load()
  } catch (failure) {
    error.value = (failure as Error).message
  } finally {
    busy.value = false
  }
}

async function setActive(schedule: Schedule, active: boolean) {
  try {
    await api.post(`/v1/schedules/${schedule.id}/pause`, { active })
    await load()
  } catch (failure) {
    error.value = (failure as Error).message
  }
}

async function remove(schedule: Schedule) {
  // Pausing keeps the settings; deleting does not, so this one asks. The
  // audits it already produced are unaffected — they are workflows of their
  // own and outlive the schedule that started them.
  if (!confirm('این زمان‌بندی حذف شود؟ گزارش‌هایی که تا حالا ساخته باقی می‌مانند.')) return
  try {
    await api.del(`/v1/schedules/${schedule.id}`)
    await load()
  } catch (failure) {
    error.value = (failure as Error).message
  }
}

function startUrlOf(schedule: Schedule): string {
  const url = schedule.inputs?.start_url
  return typeof url === 'string' ? url : '—'
}
</script>

<template>
  <div>
    <h1>زمان‌بندی</h1>
    <p class="lede">
      تحلیل به‌جای اینکه یادتان بیاید، خودش اجرا می‌شود: گزارشش ساخته و از
      کانال‌هایی که تعریف کرده‌اید فرستاده می‌شود.
    </p>

    <p v-if="error" class="error">{{ error }}</p>

    <section class="panel">
      <h2>زمان‌بندی تازه</h2>
      <form @submit.prevent="create">
        <label>
          <span>آدرس شروع</span>
          <input v-model="form.start_url" required placeholder="https://example.com" class="ltr">
        </label>

        <label>
          <span>هر چند وقت</span>
          <select v-model="form.cadence">
            <option value="daily">روزانه</option>
            <option value="weekly">هفتگی</option>
            <option value="monthly">ماهانه</option>
          </select>
        </label>

        <label v-if="form.cadence === 'weekly'">
          <span>کدام روز هفته</span>
          <select v-model.number="form.weekday">
            <!-- The store numbers Monday as zero; the option values follow it
                 rather than being renumbered here. -->
            <option v-for="day in 7" :key="day" :value="day - 1">{{ weekdayLabel(day - 1) }}</option>
          </select>
        </label>

        <label v-if="form.cadence === 'monthly'">
          <span>روز چندم ماه میلادی (۲۹ تا ۳۱ در ماه‌های کوتاه‌تر به آخر ماه می‌چسبد)</span>
          <input v-model.number="form.day_of_month" type="number" min="1" max="31">
        </label>

        <label>
          <span>ساعت</span>
          <input v-model.number="form.hour" type="number" min="0" max="23">
        </label>

        <label>
          <!-- Stored and computed in this zone, so it keeps meaning the same
               hour after a clock change. -->
          <span>منطقه‌ی زمانی</span>
          <input v-model="form.timezone" required class="ltr" placeholder="Asia/Tehran">
        </label>

        <label>
          <span>حداکثر صفحات هر اجرا</span>
          <input v-model.number="form.max_pages" type="number" min="1" max="100000">
        </label>

        <label>
          <span>چند کلمه رتبه‌سنجی شود</span>
          <input v-model.number="form.track_keywords" type="number" min="1" max="50">
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

        <button type="submit" :disabled="busy">{{ busy ? 'در حال ساخت…' : 'ساخت زمان‌بندی' }}</button>
      </form>
    </section>

    <section class="panel">
      <h2>زمان‌بندی‌های فعلی</h2>
      <p v-if="loaded && !schedules.length" class="muted">هنوز زمان‌بندی‌ای نیست.</p>
      <table v-else-if="schedules.length">
        <thead>
          <tr>
            <th>سایت</th><th>هر چند وقت</th><th>اجرای بعدی</th><th>اجراها</th><th />
          </tr>
        </thead>
        <tbody>
          <tr v-for="schedule in schedules" :key="schedule.id">
            <td class="ltr">{{ startUrlOf(schedule) }}</td>
            <td>
              {{ cadenceLabel(schedule) }}
              <div class="muted small ltr">{{ schedule.timezone }}</div>
            </td>
            <td>
              <template v-if="schedule.active">
                {{ runAtLabel(schedule.next_run_at, schedule.timezone) }}
              </template>
              <span v-else class="pill">متوقف</span>
            </td>
            <td>
              {{ schedule.runs }}
              <div v-if="schedule.last_run_at" class="muted small">
                آخری: {{ since(schedule.last_run_at) }}
              </div>
            </td>
            <td class="row-actions">
              <button class="ghost" @click="setActive(schedule, !schedule.active)">
                {{ schedule.active ? 'توقف' : 'ادامه' }}
              </button>
              <button class="ghost" @click="remove(schedule)">حذف</button>
            </td>
          </tr>
        </tbody>
      </table>
    </section>
  </div>
</template>
