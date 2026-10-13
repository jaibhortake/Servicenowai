import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const apiTarget = env.VITE_API_PROXY_TARGET || env.VITE_API_URL || 'http://localhost:8099'
  const server = { port: 5200, host: true }

  if (env.VITE_DEV_HOST) {
    server.host = env.VITE_DEV_HOST
  }

  if (env.VITE_DEV_PORT) {
    server.port = Number(env.VITE_DEV_PORT)
  }

  if (apiTarget) {
    server.proxy = {
      '/api': {
        target: apiTarget,
        changeOrigin: true,
        ws: true,
      },
    }
  }

  return {
    plugins: [react()],
    server,
  }
})
