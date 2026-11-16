import type { IncomingMessage, ServerResponse } from 'node:http'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

function port(name: string, fallback: number) {
  const value = process.env[name] ?? String(fallback)
  if (!/^[1-9][0-9]{0,4}$/.test(value) || Number(value) > 65535) throw new Error('Invalid local port')
  return Number(value)
}

const apiPort = port('WORKFLOW_API_PORT', 8000)
const uiPort = port('WORKFLOW_UI_PORT', 5173)
const host = `127.0.0.1:${uiPort}`
const origin = `http://${host}`
const allowedRoute = /^\/v1\/(catalog|workflows(?:\/[1-9][0-9]*(?:\/(?:advance|quality-findings|recommendations|batches\/(?:left|right)(?:\/files)?))?)?)$/

function boundary(req: IncomingMessage, res: ServerResponse, next: () => void) {
  const path = (req.url ?? '').split('?')[0]
  const wrongOrigin = req.headers.origin !== undefined && req.headers.origin !== origin
  const crossOrigin = req.headers['sec-fetch-site'] === 'cross-site' || req.headers['sec-fetch-site'] === 'same-site'
  if (req.headers.host !== host || wrongOrigin || crossOrigin) {
    res.writeHead(403).end()
    return
  }
  if (path.startsWith('/v1') && (!allowedRoute.test(path) ||
      (req.method !== 'GET' && !(req.method === 'POST' && (path === '/v1/workflows' || path.endsWith('/advance')))))) {
    res.writeHead(404).end()
    return
  }
  if (path.startsWith('/v1/')) res.setHeader('Cache-Control', 'no-store')
  res.setHeader('Referrer-Policy', 'no-referrer')
  next()
}

const local = {
  host: '127.0.0.1', port: uiPort, strictPort: true, cors: false,
  proxy: { '/v1/': { target: `http://127.0.0.1:${apiPort}`, changeOrigin: true } },
} as const

export default defineConfig({
  plugins: [{
    name: 'workflow-local-boundary',
    configureServer(server) { server.middlewares.use(boundary) },
    configurePreviewServer(server) { server.middlewares.use(boundary) },
  }, react()],
  server: local, preview: local,
  test: { environment: 'jsdom', environmentOptions: { jsdom: { url: origin } }, include: ['tests/**/*.test.{ts,tsx}'] },
})
