/**
 * Signed out means the login page, signed in means not the login page.
 *
 * This is a convenience, not a control: every page's data comes from the
 * gateway, which checks the token itself. A route guard in a bundle the user
 * controls protects nothing — it just avoids showing an empty page that is
 * about to 401.
 */
import { HOME } from '~/utils/routes'

export default defineNuxtRouteMiddleware((to) => {
  const { signedIn } = useAuth()
  const open = ['/login', '/register']

  if (!signedIn.value && !open.includes(to.path)) return navigateTo('/login')
  if (signedIn.value && open.includes(to.path)) return navigateTo(HOME)
})
