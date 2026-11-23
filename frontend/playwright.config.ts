import { defineConfig, devices } from "@playwright/test";

const isCI = Boolean(process.env.CI);
// localhost, not 127.0.0.1: the backend allows CORS for FRONTEND_ORIGIN only, which is
// http://localhost:3000 by default.
const baseURL = "http://localhost:3000";

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  forbidOnly: isCI,
  reporter: isCI ? "list" : "html",
  use: {
    baseURL,
    trace: "retain-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
  // The tests run against the real backend and database. Locally, servers that already
  // run are reused; the database needs `make migrate` and `make seed` first.
  webServer: [
    {
      command:
        "cd ../backend && uv run uvicorn app.main:create_app --factory --port 8000 --no-access-log",
      url: "http://localhost:8000/api/v1/health",
      reuseExistingServer: !isCI,
      timeout: 60_000,
    },
    {
      // CI builds first and serves the production build. `pnpm test:e2e` puts
      // node_modules/.bin on the PATH, so no global pnpm is needed.
      command: isCI ? "next start" : "next dev",
      url: baseURL,
      reuseExistingServer: !isCI,
      timeout: 120_000,
    },
  ],
});
