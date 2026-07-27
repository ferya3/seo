import { computed, ref } from 'vue'

/**
 * Who is signed in, and the token that proves it.
 *
 * The token lives in sessionStorage rather than localStorage: the gateway
 * issues bearer tokens, so there is no HttpOnly option without putting a
 * server in front of it, and between the two browser stores the one that dies
 * with the tab is the smaller window. It survives a reload, which is what a
 * dashboard actually needs.
 */

const KEY = 'seo.token'
const USER = 'seo.user'

export interface Account {
  id: string
  name: string
  email: string
  tenant_id: string
}

const token = ref<string | null>(null)
const account = ref<Account | null>(null)
let restored = false

function restore() {
  if (restored || typeof window === 'undefined') return
  restored = true
  token.value = sessionStorage.getItem(KEY)
  const raw = sessionStorage.getItem(USER)
  account.value = raw ? (JSON.parse(raw) as Account) : null
}

export function useAuth() {
  restore()

  function signIn(newToken: string, user: Account) {
    token.value = newToken
    account.value = user
    sessionStorage.setItem(KEY, newToken)
    sessionStorage.setItem(USER, JSON.stringify(user))
  }

  function signOut() {
    token.value = null
    account.value = null
    sessionStorage.removeItem(KEY)
    sessionStorage.removeItem(USER)
  }

  return {
    token: computed(() => token.value),
    account: computed(() => account.value),
    signedIn: computed(() => token.value !== null),
    signIn,
    signOut,
  }
}
