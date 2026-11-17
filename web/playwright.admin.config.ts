import { defineConfig } from '@playwright/test'
import base from './playwright.progress.config'

export default defineConfig({ ...base, testMatch: ['admin-theme.spec.ts', 'password-progress.spec.ts'] })
