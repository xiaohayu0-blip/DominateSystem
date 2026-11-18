import { defineConfig } from '@playwright/test'
import base from './playwright.progress.config'
export default defineConfig({ ...base, testMatch: ['vocabulary.spec.ts', 'admin-theme.spec.ts', 'password-progress.spec.ts'] })
