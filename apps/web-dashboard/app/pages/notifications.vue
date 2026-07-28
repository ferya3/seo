<script setup lang="ts">
import { deliveryDetail, deliveryLabel, deliveryTone, eventsLabel, since } from '~/utils/format'

/*
 * Where reports go, and whether they got there.
 *
 * Two panels for two questions people actually ask. The channels list answers
 * "who is subscribed"; the deliveries list answers "did last Monday's report
 * reach anyone", which until now could only be checked by reading the
 * database.
 *
 * The signing secret is the awkward part of this screen. The service returns
 * it exactly once, on creation, and never again — so it is held in a ref and
 * shown until the page is left, with a warning that says so. Fetching it back
 * later is not an option this UI can offer, and pretending otherwise would
 * leave someone with an unusable webhook.
 */

interface Channel {
  id: string
  kind: string
  target: string
  events: string[]
  active: boolean
  secret: string | null
  has_secret: boolean
  created_at: string
}

interface Delivery {
  id: string
  channel_id: string
  kind: string
  target: string
  event_type: string
  status: string
  http_status: number | null
  error: string | null
  attempts: number
  created_at: string
}

interface Project { id: string, name: string, domain: string }

const api = useApi()

const channels = ref<Channel[]>([])
const deliveries = ref<Delivery[]>([])
const projects = ref<Project[]>([])
const error = ref<string | null>(null)
const busy = ref(false)
const loaded = ref(false)

/** Shown once, then gone: the service cannot hand it back. */
const freshSecret = ref<string | null>(null)
const tested = ref<Record<string, string>>({})

const form = reactive({
  kind: 'email',
  target: '',
  events: [] as string[],
  project_id: '',
})

const EVENTS = [
  { value: 'report.rendered', label: 'گزارش آماده شد' },
  { value: 'workflow.completed', label: 'تحلیل تمام شد' },
]

async function load() {
  try {
    const [rows, sent] = await Promise.all([
      api.get<Channel[]>('/v1/notification-channels'),
      api.get<Delivery[]>('/v1/notification-deliveries'),
    ])
    channels.value = rows
    deliveries.value = sent
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
    // Optional, same as everywhere else.
  }
})

async function create() {
  busy.value = true
  error.value = null
  freshSecret.value = null
  try {
    const body: Record<string, unknown> = { kind: form.kind, target: form.target.trim() }
    // An empty list means every subscribable event, which is what someone
    // adding their first channel wants; sending [] says exactly that.
    if (form.events.length) body.events = form.events
    if (form.project_id) body.project_id = form.project_id

    const channel = await api.post<Channel>('/v1/notification-channels', body)
    if (channel.secret) freshSecret.value = channel.secret
    form.target = ''
    form.events = []
    await load()
  } catch (failure) {
    error.value = (failure as Error).message
  } finally {
    busy.value = false
  }
}

async function test(channel: Channel) {
  tested.value = { ...tested.value, [channel.id]: 'در حال ارسال…' }
  try {
    const result = await api.post<{ ok: boolean, status: number | null, error: string | null }>(
      `/v1/notification-channels/${channel.id}/test`, {},
    )
    tested.value = {
      ...tested.value,
      [channel.id]: result.ok
        ? `رسید${result.status ? ` (HTTP ${result.status})` : ''}`
        : `نرسید — ${result.error || `HTTP ${result.status}`}`,
    }
  } catch (failure) {
    tested.value = { ...tested.value, [channel.id]: (failure as Error).message }
  }
}

async function remove(channel: Channel) {
  if (!confirm('این کانال حذف شود؟ گزارش‌های بعدی به آن فرستاده نمی‌شوند.')) return
  try {
    await api.del(`/v1/notification-channels/${channel.id}`)
    await load()
  } catch (failure) {
    error.value = (failure as Error).message
  }
}
</script>

<template>
  <div>
    <h1>اطلاع‌رسانی</h1>
    <p class="lede">
      وقتی گزارشی آماده شد، به این نشانی‌ها خبر می‌رود. وبهوک‌ها با امضای
      <span class="ltr">HMAC-SHA256</span> در هدر <span class="ltr">X-Seo-Signature</span> فرستاده می‌شوند.
    </p>

    <p v-if="error" class="error">{{ error }}</p>

    <section v-if="freshSecret" class="panel secret">
      <h2>کلید امضا</h2>
      <p>
        این کلید فقط همین یک بار نشان داده می‌شود — با ترک این صفحه از بین می‌رود.
        همین حالا جایی امنش کنید.
      </p>
      <code class="ltr">{{ freshSecret }}</code>
    </section>

    <section class="panel">
      <h2>کانال تازه</h2>
      <form @submit.prevent="create">
        <label>
          <span>نوع</span>
          <select v-model="form.kind">
            <option value="email">ایمیل</option>
            <option value="webhook">وبهوک</option>
          </select>
        </label>

        <label>
          <span>{{ form.kind === 'email' ? 'نشانی ایمیل' : 'آدرس وبهوک' }}</span>
          <input
            v-model="form.target" required maxlength="2048" class="ltr"
            :placeholder="form.kind === 'email' ? 'you@example.com' : 'https://example.com/hooks/seo'"
          >
        </label>

        <fieldset>
          <!-- Nothing ticked is not "no events": the service reads an empty
               subscription as every event, and the label says so. -->
          <legend>کدام رویدادها (اگر هیچ‌کدام را نزنید، همه)</legend>
          <label v-for="event in EVENTS" :key="event.value" class="check">
            <input v-model="form.events" type="checkbox" :value="event.value">
            <span>{{ event.label }}</span>
          </label>
        </fieldset>

        <label v-if="projects.length">
          <span>پروژه (اختیاری)</span>
          <select v-model="form.project_id">
            <option value="">بدون پروژه</option>
            <option v-for="project in projects" :key="project.id" :value="project.id">
              {{ project.name }} — {{ project.domain }}
            </option>
          </select>
        </label>

        <button type="submit" :disabled="busy">{{ busy ? 'در حال افزودن…' : 'افزودن کانال' }}</button>
      </form>
    </section>

    <section class="panel">
      <h2>کانال‌ها</h2>
      <p v-if="loaded && !channels.length" class="muted">هنوز کانالی نیست.</p>
      <table v-else-if="channels.length">
        <thead>
          <tr><th>مقصد</th><th>رویدادها</th><th /><th /></tr>
        </thead>
        <tbody>
          <tr v-for="channel in channels" :key="channel.id">
            <td>
              <span class="ltr">{{ channel.target }}</span>
              <div class="muted small">{{ channel.kind === 'email' ? 'ایمیل' : 'وبهوک' }}</div>
            </td>
            <td>{{ eventsLabel(channel.events) }}</td>
            <td>
              <span v-if="tested[channel.id]" class="muted small">{{ tested[channel.id] }}</span>
            </td>
            <td class="row-actions">
              <button class="ghost" @click="test(channel)">آزمایش</button>
              <button class="ghost" @click="remove(channel)">حذف</button>
            </td>
          </tr>
        </tbody>
      </table>
    </section>

    <section class="panel">
      <h2>ارسال‌های اخیر</h2>
      <p v-if="loaded && !deliveries.length" class="muted">هنوز چیزی فرستاده نشده.</p>
      <table v-else-if="deliveries.length">
        <thead>
          <tr><th>کِی</th><th>مقصد</th><th>رویداد</th><th>نتیجه</th></tr>
        </thead>
        <tbody>
          <tr v-for="delivery in deliveries" :key="delivery.id">
            <td>{{ since(delivery.created_at) }}</td>
            <!-- Masked by the service; a listing is the sort of thing that
                 ends up in a screenshot. -->
            <td class="ltr">{{ delivery.target }}</td>
            <td class="ltr">{{ delivery.event_type }}</td>
            <td>
              <span class="pill" :class="deliveryTone(delivery.status)">
                {{ deliveryLabel(delivery.status) }}
              </span>
              <!-- The detail is isolated: "HTTP 503" beside Persian text
                   otherwise reorders into "503 HTTP". -->
              <div class="muted small"><span class="ltr">{{ deliveryDetail(delivery) }}</span></div>
              <div v-if="delivery.attempts > 1" class="muted small">
                {{ delivery.attempts }} تلاش
              </div>
            </td>
          </tr>
        </tbody>
      </table>
    </section>
  </div>
</template>
