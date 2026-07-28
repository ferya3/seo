import { describe, expect, it } from 'vitest'

import {
  cadenceLabel,
  changeLabel,
  changeTone,
  deliveryDetail,
  deliveryLabel,
  deliveryTone,
  describeError,
  eventsLabel,
  isTerminal,
  plot,
  positionLabel,
  runAtLabel,
  scoreTone,
  since,
  sparkline,
  statusLabel,
  stepLabel,
  unplotted,
  weekdayLabel,
} from '../app/utils/format'

/*
 * The judgements a UI makes, tested without a browser: what a missing position
 * means, when polling stops, and what each of the gateway's status codes tells
 * the person looking at the screen.
 */

describe('positions', () => {
  it('never prints a number the check did not find', () => {
    // Position null means "not in the results we fetched". Showing 0, or "-",
    // would read as a rank the site does not have.
    expect(positionLabel(null)).toContain('پیدا نشد')
    expect(positionLabel(undefined)).toContain('پیدا نشد')
    expect(positionLabel(3)).toBe('جایگاه 3')
  })

  it('keeps position one as a position, not as falsy', () => {
    expect(positionLabel(1)).toBe('جایگاه 1')
  })
})

describe('polling', () => {
  it('stops on a terminal status and keeps going otherwise', () => {
    expect(isTerminal('completed')).toBe(true)
    expect(isTerminal('failed')).toBe(true)
    expect(isTerminal('running')).toBe(false)
    expect(isTerminal('queued')).toBe(false)
  })
})

describe('labels', () => {
  it('translates the statuses the API actually sends', () => {
    for (const status of ['queued', 'pending', 'dispatched', 'running', 'completed', 'failed', 'skipped']) {
      expect(statusLabel(status)).not.toBe(status)
    }
  })

  it('passes an unknown status through rather than blanking it', () => {
    expect(statusLabel('paused')).toBe('paused')
  })

  it('names each step kind the orchestrator plans', () => {
    expect(stepLabel('crawl')).toBe('خزش سایت')
    expect(stepLabel('keyword_research')).toBe('تحقیق کلمات کلیدی')
    expect(stepLabel('serp_check')).toBe('بررسی جایگاه')
    expect(stepLabel('link_analysis')).toBe('تحلیل لینک داخلی')
    expect(stepLabel('content_analysis')).toBe('پوشش محتوا')
    expect(stepLabel('optimizer_plan')).toBe('پیشنهاد بازنویسی')
    // A workflow can now contain four crawls; only one of them is yours, and
    // the steps table is where a reader tells them apart.
    expect(stepLabel('competitor_crawl')).toBe('خزش سایت رقیب')
    expect(stepLabel('competitor_check')).toBe('مقایسه با رقبا')
  })
})

describe('score tone', () => {
  it('has no colour for a score that does not exist', () => {
    expect(scoreTone(null)).toBe('unknown')
  })

  it('bands on the same boundaries the report uses', () => {
    expect(scoreTone(80)).toBe('good')
    expect(scoreTone(79)).toBe('fair')
    expect(scoreTone(60)).toBe('fair')
    expect(scoreTone(59)).toBe('poor')
    expect(scoreTone(0)).toBe('poor')
  })
})

describe('relative time', () => {
  const now = new Date('2026-07-27T12:00:00Z')

  it('reads in the units the job actually takes', () => {
    expect(since('2026-07-27T11:59:30Z', now)).toBe('همین حالا')
    expect(since('2026-07-27T11:45:00Z', now)).toBe('15 دقیقه پیش')
    expect(since('2026-07-27T09:00:00Z', now)).toBe('3 ساعت پیش')
    expect(since('2026-07-25T12:00:00Z', now)).toBe('2 روز پیش')
  })

  it('does not print Invalid Date at anyone', () => {
    expect(since(null)).toBe('—')
    expect(since('not a date')).toBe('—')
  })
})

describe('errors', () => {
  it('tells the four failures apart instead of saying "something went wrong"', () => {
    expect(describeError(0)).toContain('دسترسی نیست')
    expect(describeError(401)).toContain('منقضی')
    expect(describeError(429)).toContain('زیاد')
    expect(describeError(503)).toContain('در دسترس نیست')
  })

  it("prefers the service's own explanation when there is one", () => {
    expect(describeError(422, { error: 'target 10.0.0.1 is not allowed' }))
      .toBe('target 10.0.0.1 is not allowed')
    expect(describeError(503, { error: 'orchestrator unavailable' }))
      .toBe('orchestrator unavailable')
  })

  it('digs the first message out of a Laravel validation body', () => {
    expect(describeError(422, { message: 'x', errors: { start_url: ['The start url field is required.'] } }))
      .toBe('x')
    expect(describeError(422, { errors: { start_url: ['The start url field is required.'] } }))
      .toBe('The start url field is required.')
  })

  it('survives a body that is not an object', () => {
    expect(describeError(500, 'plain text')).toContain('500')
    expect(describeError(500, null)).toContain('500')
  })
})

describe('cadence', () => {
  it('names the day the way the store numbers it — Monday is zero', () => {
    // Off by one here and every weekly schedule reads wrong on screen while
    // firing correctly, which is the hardest kind of bug to be told about.
    expect(weekdayLabel(0)).toBe('دوشنبه')
    expect(weekdayLabel(6)).toBe('یکشنبه')
  })

  it('spells the day exactly as the date beside it does', () => {
    // Both come from Intl for this reason: "پنج‌شنبه" in the cadence next to
    // "پنجشنبه" in the next-run column reads as two different days.
    const thursday = runAtLabel('2026-07-30T04:30:00+00:00', 'UTC')
    expect(thursday.startsWith(weekdayLabel(3))).toBe(true)
  })

  it('pads the hour so 9 does not read as 90', () => {
    expect(cadenceLabel({ cadence: 'daily', hour: 9, weekday: 0, day_of_month: 1 }))
      .toBe('هر روز ساعت 09:00')
  })

  it('says the monthly cadence counts Gregorian months', () => {
    // The scheduler does no Jalali arithmetic. A Persian UI that leaves that
    // unsaid is read as "the first of every Persian month".
    expect(cadenceLabel({ cadence: 'monthly', hour: 8, weekday: 0, day_of_month: 1 }))
      .toContain('میلادی')
  })

  it('warns that a late day clamps instead of skipping the short months', () => {
    const late = cadenceLabel({ cadence: 'monthly', hour: 9, weekday: 0, day_of_month: 31 })
    expect(late).toContain('آخرین روز ماه')
    // …and does not clutter the common case with it.
    expect(cadenceLabel({ cadence: 'monthly', hour: 9, weekday: 0, day_of_month: 5 }))
      .not.toContain('آخرین روز ماه')
  })
})

describe('next run', () => {
  const nine = '2026-07-28T05:30:00+00:00'   // 09:00 in Tehran

  it("prints the time in the schedule's timezone, not the browser's", () => {
    // The whole point: someone who asked for nine in Tehran must read nine,
    // wherever the laptop is. This is UTC+03:30 in July.
    expect(runAtLabel(nine, 'Asia/Tehran')).toContain('09:00')
    expect(runAtLabel(nine, 'UTC')).toContain('05:30')
  })

  it('falls back to UTC and says so rather than throwing', () => {
    const label = runAtLabel(nine, 'Mars/Olympus')
    expect(label).toContain('UTC')
    expect(label).toContain('05:30')
  })

  it('has an answer for a schedule that has no next run', () => {
    expect(runAtLabel(null)).toBe('—')
    expect(runAtLabel('not a date')).toBe('—')
  })
})

describe('deliveries', () => {
  it('reads an empty subscription as every event, which is what it means', () => {
    expect(eventsLabel([])).toBe('همه‌ی رویدادها')
    expect(eventsLabel(undefined)).toBe('همه‌ی رویدادها')
    expect(eventsLabel(['report.rendered'])).toBe('گزارش آماده شد')
  })

  it('keeps "still sending" apart from "failed"', () => {
    expect(deliveryTone('sent')).toBe('good')
    expect(deliveryTone('failed')).toBe('poor')
    expect(deliveryTone('sending')).toBe('unknown')
    expect(deliveryLabel('sending')).toBe('در حال ارسال')
  })

  it('shows why a delivery failed, because 404 and 503 need different actions', () => {
    expect(deliveryDetail({ status: 'failed', http_status: 404 })).toContain('404')
    // What the webhook sender actually writes when the status is all it knows.
    expect(deliveryDetail({ status: 'failed', http_status: 503, error: 'HTTP 503' }))
      .toBe('HTTP 503')
    expect(deliveryDetail({ status: 'failed', http_status: 500, error: 'ReadTimeout: took too long' }))
      .toBe('HTTP 500 — ReadTimeout: took too long')
    expect(deliveryDetail({ status: 'failed', http_status: null, error: 'connection refused' }))
      .toContain('connection refused')
    expect(deliveryDetail({ status: 'failed', http_status: null, error: null }))
      .toBe('دلیلش ثبت نشده')
    expect(deliveryDetail({ status: 'sent', http_status: 200 })).toBe('HTTP 200')
  })
})


describe('trend', () => {
  const better = { metric: 'overall_score', before: 45, after: 58, change: 13, direction: 'better' as const }
  const worse = { metric: 'total_issues', before: 9, after: 18, change: 9, direction: 'worse' as const }
  const level = { metric: 'orphan_pages', before: 2, after: 2, change: 0, direction: 'level' as const }

  it('decides which way is better instead of leaving it to the reader', () => {
    // Both moved up by a positive number; only one of them is good news.
    expect(changeLabel(better)).toBe('+13 (بهتر)')
    expect(changeLabel(worse)).toBe('+9 (بدتر)')
    expect(changeTone(better)).toBe('good')
    expect(changeTone(worse)).toBe('poor')
  })

  it('keeps a negative sign readable', () => {
    expect(changeLabel({ ...worse, change: -4, direction: 'better' })).toBe('-4 (بهتر)')
  })

  it('says a flat metric is flat rather than colouring it', () => {
    expect(changeLabel(level)).toBe('0 (بدون تغییر معنادار)')
    expect(changeTone(level)).toBe('unknown')
  })
})


describe('sparkline', () => {
  it('is not drawn at all from a single run', () => {
    // One point is a dot floating in a box, not a trend.
    expect(sparkline([70])).toBe('')
    expect(sparkline([])).toBe('')
    expect(sparkline([null, 70])).toBe('')
  })

  it('draws against a fixed hundred, not against the data', () => {
    // Auto-scaling 44/45/44 would fill the box and make noise look like a
    // collapse. Half marks stay at half height.
    expect(sparkline([50, 50], 100, 60)).toBe('0,30 100,30')
    expect(sparkline([0, 100], 100, 60)).toBe('0,60 100,0')
  })

  it('skips a run that never measured the metric instead of plotting zero', () => {
    // A line dropping to the floor because a step was skipped is a claim
    // about the site that nobody made.
    expect(sparkline([50, null, 50], 100, 60)).toBe('0,30 100,30')
    expect(unplotted([50, null, undefined, 50])).toBe(2)
  })

  it('clamps a value outside the scale rather than drawing off the canvas', () => {
    expect(sparkline([-20, 140], 100, 60)).toBe('0,60 100,0')
  })

  it('spaces the points evenly across the width', () => {
    expect(sparkline([0, 0, 0], 100, 60)).toBe('0,60 50,60 100,60')
  })
})


describe('plot', () => {
  it('gives the drawing code a dot per run, value included', () => {
    // The line alone reads as flat on a fixed 0-100 axis when a site moves by
    // ten points; the dots are what make three runs legible as three runs.
    expect(plot([40, 60], 100, 100)).toEqual([
      { x: 0, y: 60, value: 40 },
      { x: 100, y: 40, value: 60 },
    ])
  })

  it('is empty for anything that is not a series', () => {
    expect(plot([70])).toEqual([])
    expect(plot([])).toEqual([])
  })
})
