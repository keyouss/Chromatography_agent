import { fileURLToPath, URL } from 'node:url'
import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, '.', '')
  const port = Number(env.FRONTEND_PORT || 5173)
  const apiPort = Number(env.API_PORT || 9012)
  return {
    plugins: [vue()],
    resolve: { alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) } },
    define: { __BUILD_PROFILE__: JSON.stringify(env.VITE_BUILD_PROFILE || 'universal-v1') },
    server: { port, proxy: { '/api/v1': `http://127.0.0.1:${apiPort}` } },
    build: { modulePreload: { polyfill: false } },
  }
})
