import { describe, expect, it } from 'vitest'

import {
  describeError,
  isTerminal,
  positionLabel,
  scoreTone,
  since,
  statusLabel,
  stepLabel,
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
