import { defineConfig } from '@playwright/test'

const port = process.env.WORKFLOW_UI_PORT ?? '5173'
const origin = `http://127.0.0.1:${port}`

export default defineConfig({
  testDir: './e2e',
  workers: 1,
  timeout: 60_000,
  use: {
    baseURL: origin, viewport: { width: 1440, height: 1000 },
    trace: 'off', video: 'off', screenshot: 'off',
  },
  webServer: {
    command: 'env -u WORKFLOW_TOKEN -u POSTGRES_PASSWORD -u INGEST_PASSWORD -u WORKFLOW_EXEC_PASSWORD -u WORKFLOW_REPORT_PASSWORD npm run preview',
    url: origin, reuseExistingServer: false,
  },
})
