import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: './tests',
  workers: 1,
  timeout: 45000,
  use: { baseURL: 'http://localhost:5173', browserName: 'chromium', trace: 'retain-on-failure', screenshot: 'only-on-failure' },
  reporter: [['list'], ['json', { outputFile: 'test-results/results.json' }]],
})
