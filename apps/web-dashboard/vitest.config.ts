import { defineConfig } from 'vitest/config'

/*
 * Plain vitest, no Nuxt environment: what is tested here is the pure module
 * under app/utils. Booting Nuxt to run it would trade seconds of test time for
 * nothing — the components that need a browser are covered by driving the real
 * app instead.
 */
export default defineConfig({
  test: {
    include: ['tests/**/*.spec.ts'],
    environment: 'node',
  },
})
