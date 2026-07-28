/**
 * Where a signed-in person belongs.
 *
 * Four places decided this independently — the login form, the register form,
 * the route guard and the index page — and after the overview page was added
 * they disagreed: the index sent you to the sites overview and everything else
 * still sent you to the list of runs. Signing in landed on the wrong page,
 * which is exactly the sort of thing nobody notices in a diff.
 *
 * So the answer lives here, once.
 */
export const HOME = '/sites'
