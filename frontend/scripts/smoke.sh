#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="${PORT:-5173}"
BASE_URL="${BASE_URL:-http://127.0.0.1:${PORT}}"
export BASE_URL

cd "$ROOT"
npm run dev -- --host 127.0.0.1 > /tmp/company-brain-frontend-smoke.log 2>&1 &
SERVER_PID=$!
trap 'kill "$SERVER_PID" >/dev/null 2>&1 || true' EXIT

for _ in {1..40}; do
  if curl -fsS "$BASE_URL" >/dev/null 2>&1; then
    break
  fi
  sleep 0.25
done

node --input-type=module <<'NODE'
import { chromium } from "playwright";

const baseUrl = process.env.BASE_URL || "http://127.0.0.1:5173";
const routes = [
  ["/repos", "repos-page"],
  ["/browser", "browser-page"],
  ["/query", "query-page"],
  ["/drift", "drift-page"],
];

const browser = await chromium.launch();
const page = await browser.newPage();
const errors = [];
page.on("console", (msg) => {
  if (msg.type() === "error") errors.push(msg.text());
});
page.on("pageerror", (err) => errors.push(err.message));

for (const [route, testId] of routes) {
  await page.goto(`${baseUrl}${route}`, { waitUntil: "networkidle" });
  await page.getByTestId(testId).waitFor({ timeout: 10000 });
}

await browser.close();
if (errors.length) {
  console.error(errors.join("\n"));
  process.exit(1);
}
NODE

echo "Smoke passed for repos, browser, query, drift."
