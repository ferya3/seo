import { describeError } from '~/utils/format'
import { useAuth } from './useAuth'

/**
 * The gateway, from the browser.
 *
 * One place that attaches the token, one place that turns a failure into a
 * sentence — the same reason the PHP side has ServiceClient and the Python
 * side has shared/llm. A component should never have to know what a 503 means.
 */

export class ApiError extends Error {
  constructor(readonly status: number, message: string) {
    super(message)
  }
}

export function useApi() {
  const config = useRuntimeConfig()
  const auth = useAuth()

  async function request<T>(path: string, options: {
    method?: string
    body?: unknown
    signal?: AbortSignal
  } = {}): Promise<T> {
    const headers: Record<string, string> = {
      // Without this Laravel treats an unauthenticated call as a browser
      // navigation and tries to redirect it to a login page that does not
      // exist. Sent on every request, not just the ones with a body.
      Accept: 'application/json',
    }
    if (options.body !== undefined) headers['Content-Type'] = 'application/json'
    if (auth.token.value) headers.Authorization = `Bearer ${auth.token.value}`

    let response: Response
    try {
      response = await fetch(`${config.public.apiBase}${path}`, {
        method: options.method ?? 'GET',
        headers,
        body: options.body === undefined ? undefined : JSON.stringify(options.body),
        signal: options.signal,
      })
    } catch (cause) {
      // fetch only rejects when the request never happened at all.
      throw new ApiError(0, describeError(0))
    }

    const payload = response.status === 204 ? null : await response.json().catch(() => null)

    if (!response.ok) {
      // A rejected token is not an error to show and move past: whatever the
      // page was doing, the session is over.
      if (response.status === 401) auth.signOut()
      throw new ApiError(response.status, describeError(response.status, payload))
    }
    return payload as T
  }

  return {
    request,
    get: <T>(path: string, signal?: AbortSignal) => request<T>(path, { signal }),
    post: <T>(path: string, body: unknown) => request<T>(path, { method: 'POST', body }),
    del: <T>(path: string) => request<T>(path, { method: 'DELETE' }),
  }
}
