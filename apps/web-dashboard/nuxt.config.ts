export default defineNuxtConfig({
  /*
   * No server rendering, deliberately.
   *
   * The gateway authenticates with a bearer token, not a cookie. Rendering on
   * a Node server would mean that server holding every visitor's token in
   * order to fetch on their behalf — a second place tokens live, and one this
   * project has no reason to have. As a browser-only app the token never
   * leaves the tab it was issued to, and the only backend is the gateway.
   */
  ssr: false,

  compatibilityDate: '2026-07-27',
  devtools: { enabled: false },
  css: ['~/assets/css/main.css'],

  runtimeConfig: {
    public: {
      // Overridable at run time (NUXT_PUBLIC_API_BASE), because the built
      // bundle is the same artifact in development and in production.
      apiBase: 'http://127.0.0.1:8000/api',
    },
  },

  app: {
    head: {
      htmlAttrs: { lang: 'fa', dir: 'rtl' },
      title: 'داشبورد سئو',
      meta: [{ name: 'viewport', content: 'width=device-width, initial-scale=1' }],
    },
  },
})
