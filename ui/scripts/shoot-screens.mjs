// Capture README screenshots of the PWA against scripts/mock-server.mjs.
//
//   node scripts/mock-server.mjs 4173 &
//   node scripts/shoot-screens.mjs
//
// Uses the globally installed playwright package and the system chromium in
// /opt/pw-browsers (no downloads). Writes docs/screenshots/{name}.png and
// re-captures at a lower deviceScaleFactor if a file exceeds the size cap.

import { createRequire } from "node:module";
import { mkdirSync, statSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
let chromium;
try {
  ({ chromium } = require("playwright"));
} catch {
  ({ chromium } = require("/opt/node22/lib/node_modules/playwright"));
}

const __dirname = dirname(fileURLToPath(import.meta.url));
const OUT = resolve(__dirname, "..", "..", "docs", "screenshots");
const BASE = process.env.MOCK_BASE ?? "http://127.0.0.1:4173";
const EXECUTABLE = "/opt/pw-browsers/chromium-1194/chrome-linux/chrome";
const SIZE_CAP = 400 * 1024;

mkdirSync(OUT, { recursive: true });

const settle = (ms) => new Promise((r) => setTimeout(r, ms));

async function ready(page) {
  await page.evaluate(() => document.fonts.ready);
  await settle(350);
}

// ── Per-page setups: navigate, wait for real content, compose the frame ─────

const pages = {
  sessions: async (page) => {
    await page.goto(`${BASE}/sessions`);
    for (const t of ["fix flaky auth tests", "add dark mode", "profile slow ingest job"]) {
      await page.getByText(t).first().waitFor();
    }
    await page.getByText("Qwen3 Coder 30B A3B").first().waitFor();
    await ready(page);
  },

  "session-chat": async (page) => {
    await page.goto(`${BASE}/sessions/sess-1`);
    await page.getByText("boundary condition").first().waitFor();
    // Expand the file-edit tool call so Arguments/Result are visible.
    const editRow = page
      .getByRole("button")
      .filter({ hasText: "services/auth/token.py" })
      .first();
    await editRow.waitFor();
    await editRow.click();
    await page.getByText("Arguments").first().waitFor();
    // Top-anchor: the sticky page header is translucent, so any content
    // scrolled beneath it would show through in the capture.
    await page.evaluate(() => window.scrollTo(0, 0));
    await ready(page);
  },

  models: async (page) => {
    await page.goto(`${BASE}/models`);
    await page.getByText("Qwen3 Coder 30B A3B").first().waitFor();
    await page.getByText("Score breakdown").first().waitFor();
    await page.getByText("GPU 1").first().waitFor(); // multi-GPU lease banner
    await page.getByText("62%").first().waitFor(); // SSE download progress
    // Center the frame between suggestions and catalog; the lease banner is
    // sticky so it stays in view. Nudge past the clipped page header.
    await page
      .getByText("Seed-Coder-8B-Instruct")
      .first()
      .evaluate((el) => el.scrollIntoView({ block: "center", behavior: "instant" }));
    await page.evaluate(() => window.scrollBy(0, 64));
    await ready(page);
  },

  // Mobile-only: the multi-GPU load sheet (vLLM tensor parallel picker).
  "models-load": async (page) => {
    await page.goto(`${BASE}/models`);
    await page.getByText("Qwen2.5 Coder 32B AWQ").first().waitFor();
    await page.getByText("62%").first().waitFor();
    const card = page
      .locator("li")
      .filter({ hasText: "Qwen2.5 Coder 32B AWQ" })
      .last();
    await card.getByRole("button", { name: "Load", exact: true }).click();
    await page.getByText("Span multiple GPUs").waitFor();
    await page.getByText("Span multiple GPUs").click();
    await page.getByText("vLLM tensor parallel across 2 GPUs").waitFor();
    await ready(page);
  },

  system: async (page) => {
    await page.goto(`${BASE}/system`);
    await page.getByText("GPU 0 VRAM").waitFor();
    await page.getByText("GPU 1 VRAM").waitFor();
    await page.getByText("Session containers").waitFor();
    await page.getByText("forge-session-a1b2c3").waitFor();
    await ready(page);
  },

  connectors: async (page) => {
    await page.goto(`${BASE}/connectors`);
    await page.getByText("GitHub").first().waitFor();
    await page.getByText("Web search").first().waitFor();
    await page.getByText("Notion").first().waitFor();
    await ready(page);
  },
};

const DESKTOP = { viewport: { width: 1440, height: 900 }, scales: [2, 1.5, 1.25] };
const MOBILE = {
  viewport: { width: 390, height: 844 },
  scales: [3, 2, 1.5],
  isMobile: true,
  hasTouch: true,
};

const SHOTS = [
  { page: "sessions", device: "desktop" },
  // Taller frame so the whole expanded tool call fits below the header.
  { page: "session-chat", device: "desktop", height: 1250 },
  { page: "models", device: "desktop" },
  { page: "system", device: "desktop" },
  { page: "connectors", device: "desktop" },
  { page: "sessions", device: "mobile" },
  { page: "session-chat", device: "mobile" },
  { page: "models-load", device: "mobile", name: "models-mobile" },
  { page: "system", device: "mobile" },
];

const browser = await chromium.launch({
  headless: true,
  executablePath: EXECUTABLE,
  args: ["--no-sandbox", "--font-render-hinting=none"],
});

// Log in once through the real form; reuse the storage state everywhere.
const loginCtx = await browser.newContext({
  viewport: DESKTOP.viewport,
  serviceWorkers: "block",
});
{
  const page = await loginCtx.newPage();
  await page.goto(`${BASE}/login`);
  await page.fill("#password", "forge-demo");
  await page.getByRole("button", { name: "Unlock" }).click();
  await page.waitForURL("**/sessions");
  await page.getByText("fix flaky auth tests").first().waitFor();
  console.log("logged in via the form");
}
const storageState = await loginCtx.storageState();
await loginCtx.close();

async function capture(name, setup, profile, scale, height) {
  const ctx = await browser.newContext({
    viewport: { ...profile.viewport, ...(height ? { height } : {}) },
    deviceScaleFactor: scale,
    isMobile: profile.isMobile ?? false,
    hasTouch: profile.hasTouch ?? false,
    serviceWorkers: "block",
    storageState,
  });
  const page = await ctx.newPage();
  try {
    await setup(page);
    const file = join(OUT, `${name}.png`);
    await page.screenshot({ path: file, fullPage: false });
    return file;
  } finally {
    await ctx.close();
  }
}

for (const shot of SHOTS) {
  const profile = shot.device === "desktop" ? DESKTOP : MOBILE;
  const name = shot.name ?? `${shot.page}-${shot.device}`;
  let done = false;
  for (const scale of profile.scales) {
    const file = await capture(name, pages[shot.page], profile, scale, shot.height);
    const size = statSync(file).size;
    console.log(`${name}.png @${scale}x -> ${(size / 1024).toFixed(0)} KB`);
    if (size <= SIZE_CAP) {
      done = true;
      break;
    }
  }
  if (!done) console.warn(`  ${name}.png still over the cap at min scale`);
}

await browser.close();
console.log(`screenshots in ${OUT}`);
