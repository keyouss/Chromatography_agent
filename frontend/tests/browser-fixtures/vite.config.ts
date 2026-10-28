import { fileURLToPath, URL } from 'node:url'
import { defineConfig, type Plugin } from 'vite'
import vue from '@vitejs/plugin-vue'

function denyNetworkWrites(): Plugin {
  let probeCount = 0
  return {
    name: 'synthetic-browser-fixture-network-guard',
    enforce: 'pre',
    configureServer(server) {
      server.middlewares.use((request, response, next) => {
        const pathname = new URL(request.url ?? '/', 'http://127.0.0.1').pathname
        const method = (request.method ?? 'GET').toUpperCase()
        if (pathname === '/ketcher/index.html' && method === 'GET') {
          response.statusCode = 200
          response.setHeader('Content-Type', 'text/html; charset=utf-8')
          response.setHeader('Cache-Control', 'no-store')
          response.end('<!doctype html><html lang="en"><meta charset="utf-8"><title>SYNTHETIC ONLY</title><body><p>SYNTHETIC ONLY: Ketcher is not tested by this fixture.</p></body></html>')
          return
        }
        if (pathname.startsWith('/api/')) {
          response.statusCode = 403
          response.setHeader('Content-Type', 'application/json; charset=utf-8')
          response.end(JSON.stringify({ error: 'Synthetic fixture blocks all API network fallthrough.' }))
          return
        }
        if (pathname === '/__fixture/remote-probe') {
          if (method !== 'GET') {
            response.statusCode = 405
            response.end('GET only')
            return
          }
          probeCount += 1
          response.statusCode = 204
          response.end()
          return
        }
        if (pathname === '/__fixture/probe-count') {
          if (method !== 'GET') {
            response.statusCode = 405
            response.end('GET only')
            return
          }
          response.statusCode = 200
          response.setHeader('Content-Type', 'application/json; charset=utf-8')
          response.setHeader('Cache-Control', 'no-store')
          response.end(JSON.stringify({ localImageProbeRequests: probeCount }))
          return
        }
        if (method !== 'GET' && method !== 'HEAD') {
          response.statusCode = 405
          response.end('Synthetic fixture is read-only')
          return
        }
        next()
      })
    },
  }
}

export default defineConfig({
  root: fileURLToPath(new URL('.', import.meta.url)),
  envDir: false,
  plugins: [vue(), denyNetworkWrites()],
  resolve: { alias: { '@': fileURLToPath(new URL('../../src', import.meta.url)) } },
  define: { __BUILD_PROFILE__: JSON.stringify('universal-v1') },
  server: {
    host: '127.0.0.1',
    port: 5185,
    strictPort: true,
    proxy: {},
    headers: {
      'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self' ws://127.0.0.1:5185; img-src 'self'; font-src 'self'; object-src 'none'; frame-src 'self'; media-src 'none'; form-action 'none'; base-uri 'self'",
      'X-Content-Type-Options': 'nosniff',
      'Cache-Control': 'no-store',
    },
  },
})
