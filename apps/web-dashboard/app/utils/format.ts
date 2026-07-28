/**
 * Turning API values into what a person reads.
 *
 * Everything here is a pure function of its arguments, which is the point:
 * this is the part of a UI that can be tested without a browser, so it holds
 * the judgements — what "no position" means, when to stop polling, whether a
 * score is good — rather than scattering them through templates.
 *
 * Persian for the prose, English for the SEO terms, matching the reports.
 */

export type Status = 'queued' | 'running' | 'completed' | 'failed' | 'skipped' | 'dispatched' | 'pending'

const STATUS_FA: Record<string, string> = {
  queued: 'در صف',
  pending: 'در انتظار',
  dispatched: 'در حال اجرا',
  running: 'در حال اجرا',
  completed: 'انجام شد',
  failed: 'شکست خورد',
  skipped: 'انجام نشد',
}

const STEP_FA: Record<string, string> = {
  crawl: 'خزش سایت',
  keyword_research: 'تحقیق کلمات کلیدی',
  serp_check: 'بررسی جایگاه',
  link_analysis: 'تحلیل لینک داخلی',
  content_analysis: 'پوشش محتوا',
  optimizer_plan: 'پیشنهاد بازنویسی',
  competitor_crawl: 'خزش سایت رقیب',
  competitor_check: 'مقایسه با رقبا',
}

export function statusLabel(status: string): string {
  return STATUS_FA[status] ?? status
}

export function stepLabel(kind: string): string {
  return STEP_FA[kind] ?? kind
}

/** Whether a workflow has stopped moving — the signal to stop polling. */
export function isTerminal(status: string): boolean {
  return status === 'completed' || status === 'failed'
}

/**
 * `null` means the site was not found in the results that were fetched, which
 * is not the same as position zero and not the same as "not ranked anywhere".
 * Printing a number here would invent data.
 */
export function positionLabel(position: number | null | undefined): string {
  return position == null ? 'در نتایج بررسی‌شده پیدا نشد' : `جایگاه ${position}`
}

export function scoreTone(score: number | null | undefined): 'good' | 'fair' | 'poor' | 'unknown' {
  if (score == null) return 'unknown'
  if (score >= 80) return 'good'
  if (score >= 60) return 'fair'
  return 'poor'
}

/** Short relative time. Seconds are noise on jobs that take minutes. */
export function since(iso: string | null | undefined, now: Date = new Date()): string {
  if (!iso) return '—'
  const then = new Date(iso)
  if (Number.isNaN(then.getTime())) return '—'

  const minutes = Math.floor((now.getTime() - then.getTime()) / 60000)
  if (minutes < 1) return 'همین حالا'
  if (minutes < 60) return `${minutes} دقیقه پیش`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours} ساعت پیش`
  return `${Math.floor(hours / 24)} روز پیش`
}

// --------------------------------------------------------------- schedules

// Persian month and day names, Gregorian calendar, Latin digits: the calendar
// because the scheduler counts Gregorian months, the digits because every
// other number on these pages is Latin and one page with two numeral systems
// reads as a bug.
const FA_GREGORIAN = 'fa-IR-u-ca-gregory-nu-latn'

/**
 * Monday is 0, matching Python's `weekday()`, which is what the store uses.
 *
 * The names are asked of Intl rather than typed out, so the cadence sentence
 * spells the day exactly as the date beside it does — a hand-written
 * "پنج‌شنبه" next to Intl's "پنجشنبه" reads as two different days to anyone
 * scanning the row. 2024-01-01 was a Monday.
 */
const WEEKDAY_FA = Array.from({ length: 7 }, (_, day) =>
  new Intl.DateTimeFormat(FA_GREGORIAN, { weekday: 'long', timeZone: 'UTC' })
    .format(new Date(Date.UTC(2024, 0, 1 + day))))

export function weekdayLabel(weekday: number): string {
  return WEEKDAY_FA[weekday] ?? String(weekday)
}

export interface Cadence {
  cadence: string
  hour: number
  weekday: number
  day_of_month: number
}

/**
 * "هر دوشنبه ساعت ۰۹:۰۰" — the sentence someone checks against what they meant.
 *
 * Two things are said out loud rather than left to be discovered:
 * the month is the Gregorian one (the scheduler does no Jalali arithmetic and
 * pretending otherwise here would be a lie the UI tells), and a day past the
 * 28th lands on the last day of the short months instead of skipping them.
 */
export function cadenceLabel(schedule: Cadence): string {
  const at = `ساعت ${String(schedule.hour).padStart(2, '0')}:00`

  switch (schedule.cadence) {
    case 'daily':
      return `هر روز ${at}`
    case 'weekly':
      return `هر ${weekdayLabel(schedule.weekday)} ${at}`
    case 'monthly': {
      const clamped = schedule.day_of_month > 28 ? '، در ماه‌های کوتاه‌تر آخرین روز ماه' : ''
      return `روز ${schedule.day_of_month} هر ماه میلادی ${at}${clamped}`
    }
    default:
      return schedule.cadence
  }
}

/**
 * An absolute time, printed in the schedule's own timezone.
 *
 * `next_run_at` arrives in UTC and the browser is wherever the person is
 * sitting; rendering it locally would show 05:30 to someone who asked for
 * nine in Tehran and leave them convinced the scheduler is broken. An
 * unusable timezone falls back to UTC and says so rather than throwing —
 * a page that renders nothing is worse than one that names its own zone.
 */
export function runAtLabel(iso: string | null | undefined, tz = 'Asia/Tehran'): string {
  if (!iso) return '—'
  const at = new Date(iso)
  if (Number.isNaN(at.getTime())) return '—'

  const options: Intl.DateTimeFormatOptions = {
    weekday: 'long', day: 'numeric', month: 'long',
    hour: '2-digit', minute: '2-digit', hour12: false,
  }
  try {
    // Gregorian is forced: fa-IR would otherwise print a Jalali date next to
    // a cadence that counts Gregorian months.
    return new Intl.DateTimeFormat(FA_GREGORIAN, { ...options, timeZone: tz }).format(at)
  } catch {
    const utc = new Intl.DateTimeFormat(FA_GREGORIAN, { ...options, timeZone: 'UTC' })
    return `${utc.format(at)} (UTC)`
  }
}

// ------------------------------------------------------------------ trends

export interface Change {
  metric: string
  label_fa?: string
  before: number
  after: number
  change: number
  direction: 'better' | 'worse' | 'level'
}

/**
 * The change, with which way is better already decided.
 *
 * "+9" on issues is worse and "+9" on the score is better. An arrow or a
 * colour on its own leaves the reader to remember which metrics are which,
 * and the entire point of a trend section is that they should not have to.
 */
export function changeLabel(change: Change): string {
  const sign = change.change > 0 ? `+${change.change}` : String(change.change)
  const verdict = change.direction === 'better'
    ? 'بهتر'
    : change.direction === 'worse' ? 'بدتر' : 'بدون تغییر معنادار'
  return `${sign} (${verdict})`
}

export function changeTone(change: Change): 'good' | 'poor' | 'unknown' {
  if (change.direction === 'better') return 'good'
  if (change.direction === 'worse') return 'poor'
  return 'unknown'
}

/**
 * A score series as SVG polyline points.
 *
 * Three decisions live here rather than in the template, because each one is a
 * way to draw a chart that lies:
 *
 *   * **The scale is fixed to 0–100, not to the data.** Auto-scaling a run of
 *     44, 45, 44 fills the box with a mountain range and makes noise look like
 *     a collapse. A score out of a hundred is drawn against a hundred.
 *   * **A run that never measured the metric is skipped, not plotted as zero.**
 *     Same rule as the trend table: a line dropping to the floor because a
 *     step was skipped is a claim about the site that nobody made.
 *   * **Oldest on the left.** The API answers newest-first, and drawing in
 *     that order runs time backwards. The page is right-to-left; the time axis
 *     is not, which is why both ends are labelled with their dates.
 *
 * Fewer than two usable points is not a chart, and the caller gets an empty
 * string rather than a dot floating in a box.
 */
export interface Point { x: number, y: number, value: number }

export function plot(
  values: (number | null | undefined)[], width = 320, height = 120, max = 100,
): Point[] {
  const usable = values.filter((v): v is number => typeof v === 'number')
  if (usable.length < 2) return []

  const step = width / (usable.length - 1)
  return usable.map((value, index) => ({
    x: Math.round(index * step),
    y: Math.round(height - (Math.max(0, Math.min(value, max)) / max) * height),
    value,
  }))
}

/** The same points as a polyline attribute. */
export function sparkline(
  values: (number | null | undefined)[], width = 320, height = 120, max = 100,
): string {
  return plot(values, width, height, max).map(p => `${p.x},${p.y}`).join(' ')
}

/** How many runs in the series had no number for this metric. */
export function unplotted(values: (number | null | undefined)[]): number {
  return values.filter(v => typeof v !== 'number').length
}

// ----------------------------------------------------------- notifications

const EVENT_FA: Record<string, string> = {
  'report.rendered': 'گزارش آماده شد',
  'workflow.completed': 'تحلیل تمام شد',
}

export function eventLabel(event: string): string {
  return EVENT_FA[event] ?? event
}

/** An empty subscription list means every event, not none. */
export function eventsLabel(events: string[] | null | undefined): string {
  if (!events || !events.length) return 'همه‌ی رویدادها'
  return events.map(eventLabel).join('، ')
}

export function deliveryTone(status: string): 'good' | 'poor' | 'unknown' {
  if (status === 'sent') return 'good'
  if (status === 'failed') return 'poor'
  return 'unknown'   // sending — claimed, not yet answered
}

const DELIVERY_FA: Record<string, string> = {
  sent: 'رسید',
  failed: 'نرسید',
  sending: 'در حال ارسال',
}

export function deliveryLabel(status: string): string {
  return DELIVERY_FA[status] ?? status
}

/**
 * What a delivery attempt actually says, HTTP status included.
 *
 * `failed` on its own is not actionable: 404 is a wrong url someone must fix,
 * 503 is the far side having a bad minute and a retry already queued.
 */
export function deliveryDetail(row: { status: string, http_status?: number | null, error?: string | null }): string {
  if (row.status === 'sent') return row.http_status ? `HTTP ${row.http_status}` : 'انجام شد'
  if (row.status === 'sending') return 'هنوز جوابی نیامده'

  if (row.http_status) {
    const code = `HTTP ${row.http_status}`
    // The sender records `error` as "HTTP 503" when that is all it knows, so
    // printing both gives "HTTP 503 — HTTP 503".
    return row.error && row.error !== code ? `${code} — ${row.error}` : code
  }
  return row.error || 'دلیلش ثبت نشده'
}

/**
 * What a failed request means, in one place.
 *
 * The gateway's status codes each mean something specific — 503 is "the
 * service is down, try again", 422 is "you sent something wrong" — and
 * collapsing them into "خطایی رخ داد" would throw that away.
 */
export function describeError(status: number, body?: unknown): string {
  const detail = messageIn(body)

  switch (status) {
    case 0: return 'به سرور دسترسی نیست. آیا گیت‌وی بالا است؟'
    case 401: return 'نشست شما منقضی شده. دوباره وارد شوید.'
    case 403: return 'اجازه‌ی این کار را ندارید.'
    case 404: return 'پیدا نشد.'
    case 422: return detail ?? 'ورودی پذیرفته نشد.'
    case 429: return 'تعداد درخواست‌ها زیاد شد. کمی صبر کنید.'
    case 503: return detail ?? 'سرویس پایین‌دستی در دسترس نیست. کمی بعد دوباره تلاش کنید.'
    default:
      return detail ?? `خطای غیرمنتظره (${status}).`
  }
}

/** Laravel answers with `message` or `error`; FastAPI with `detail`. */
function messageIn(body: unknown): string | undefined {
  if (!body || typeof body !== 'object') return undefined
  const record = body as Record<string, unknown>

  for (const key of ['error', 'message', 'detail']) {
    const value = record[key]
    if (typeof value === 'string' && value.trim()) return value
  }

  // Laravel validation: {"errors": {"start_url": ["..."]}}
  const errors = record.errors
  if (errors && typeof errors === 'object') {
    const first = Object.values(errors as Record<string, unknown>)[0]
    if (Array.isArray(first) && typeof first[0] === 'string') return first[0]
  }
  return undefined
}
