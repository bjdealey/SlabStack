import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import path from 'node:path'

// The UI talks to a relative /api, and Vite proxies it to the local FastAPI
// process. That keeps the browser on one origin, so there is no CORS
// preflight in development and no API base URL baked into the build.
//
// `--mode demo` loads .env.demo, which switches the app to its in-browser API
// and sets the base path GitHub project Pages need.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')

  return {
    base: env.VITE_BASE || '/',
    plugins: [react(), tailwindcss()],
    resolve: {
      alias: { '@': path.resolve(__dirname, './src') },
    },
    build: {
      // The demo bundles the sample collection and the ported engine; the app
      // bundles Recharts and TanStack. Both sit comfortably under a megabyte,
      // and this is a single-user local app, not a landing page.
      chunkSizeWarningLimit: 1100,
    },
    server: {
      port: 5173,
      strictPort: false,
      proxy: {
        '/api': {
          target: env.SLABSTACK_API_URL || 'http://127.0.0.1:8000',
          changeOrigin: true,
        },
      },
    },
  }
})
