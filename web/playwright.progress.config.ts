import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: './tests', testMatch: 'password-progress.spec.ts', workers: 1, timeout: 30000,
  use: { baseURL: 'http://127.0.0.1:5179', channel: 'msedge', headless: true, screenshot: 'only-on-failure' },
  webServer: { command: 'npm.cmd run dev -- --port 5179 --strictPort', url: 'http://127.0.0.1:5179', reuseExistingServer: false },
  reporter: [['list']],
})
