import { defineConfig } from "@playwright/test";

// End-to-end tests drive the REAL stack: start the backend (with PostgreSQL) and `npm run preview` (or the Docker
// Compose stack) first, then run `npm run e2e`. E2E_BASE_URL selects the frontend URL.
export default defineConfig({
  testDir: "e2e",
  timeout: 120_000,
  retries: 0,
  reporter: [["list"]],
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:4173",
    screenshot: "only-on-failure",
    // Optional: use an already-installed Chromium instead of `npx playwright install chromium`.
    launchOptions: { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE || undefined },
  },
});
