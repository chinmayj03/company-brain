import { defineConfig } from '@playwright/test';
export default defineConfig({
  testDir: './e2e',
  timeout: 30000,
  use: {
    baseURL: 'http://localhost:5173',
    screenshot: 'on',
    video: 'off',
    headless: true,
  },
  outputDir: '../.e2e-session/screenshots',
});
