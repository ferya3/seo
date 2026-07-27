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
