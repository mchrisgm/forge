// Capture README screenshots of the PWA against scripts/mock-server.mjs.
//
//   node scripts/mock-server.mjs 4173 &
//   SETUP_MODE=1 node scripts/mock-server.mjs 4174 &   # first-run wizard shots
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
// Second mock instance running with SETUP_MODE=1 — serves the /setup wizard.
const SETUP_BASE = process.env.MOCK_SETUP_BASE ?? "http://127.0.0.1:4174";
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
  // The Chat section: conversation list + the curated garden conversation
  // with an image attachment, markdown table/list/code, and the composer.
  chat: async (page) => {
    await page.goto(`${BASE}/chats/conv-1`);
    await page.getByText("gravity-fed drip line").first().waitFor();
    await page.getByText("0.08 bar").first().waitFor();
    await page.getByText("Zone A  06:30").first().waitFor(); // code block
    // The attachment thumbnail must actually be decoded, not a broken image.
    const thumb = page.locator('img[alt="raised-beds.png"]').first();
    await thumb.waitFor();
    await thumb.evaluate(
      (el) =>
        el.complete && el.naturalWidth > 0
          ? undefined
          : new Promise((r) => el.addEventListener("load", r, { once: true })),
    );
    await page.evaluate(() => window.scrollTo(0, 0));
    await ready(page);
  },

  memory: async (page) => {
    await page.goto(`${BASE}/memory`);
    await page.getByText("What Forge remembers").first().waitFor();
    await page.getByText("Facts (3)").waitFor();
    await page.getByText("Preferences (3)").waitFor();
    // Two pinned entries → two filled "Unpin" stars.
    await page.getByLabel("Unpin memory").nth(1).waitFor();
    await page.evaluate(() => window.scrollTo(0, 0));
    await ready(page);
  },

  // First-run wizard step 1, against the SETUP_MODE=1 mock (logged out).
  setup: async (page) => {
    await page.goto(`${SETUP_BASE}/setup`);
    await page.getByText("Welcome to Forge").waitFor();
    await page.getByRole("button", { name: "Create admin profile" }).waitFor();
    await ready(page);
  },

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
  // The curated conversation fits the default frame with the composer snug
  // beneath it — a taller frame would just add dead space above the composer.
  { page: "chat", device: "desktop", height: 960 },
  { page: "memory", device: "desktop", height: 1250 },
  // The wizard is one centered card — a narrower frame keeps it readable.
  { page: "setup", device: "desktop", width: 960, height: 840, fresh: true },
  { page: "sessions", device: "desktop" },
  // Taller frame so the whole expanded tool call fits below the header.
  { page: "session-chat", device: "desktop", height: 1250 },
  { page: "models", device: "desktop" },
  { page: "system", device: "desktop" },
  { page: "connectors", device: "desktop" },
  { page: "chat", device: "mobile" },
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

// Log in once through the real multi-user form; reuse the storage state
// everywhere (bearer token + the signed-in profile in localStorage).
const loginCtx = await browser.newContext({
  viewport: DESKTOP.viewport,
  serviceWorkers: "block",
});
{
  const page = await loginCtx.newPage();
  await page.goto(`${BASE}/login`);
  await page.getByLabel("Username").fill("chris");
  await page.getByLabel("Password").fill("forge-demo");
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.waitForURL("**/chats");
  await page.getByText("Plan the garden irrigation").first().waitFor();
  console.log("logged in via the form");
}
const storageState = await loginCtx.storageState();
await loginCtx.close();

async function capture(name, setup, profile, scale, shot) {
  const ctx = await browser.newContext({
    viewport: {
      ...profile.viewport,
      ...(shot.width ? { width: shot.width } : {}),
      ...(shot.height ? { height: shot.height } : {}),
    },
    deviceScaleFactor: scale,
    isMobile: profile.isMobile ?? false,
    hasTouch: profile.hasTouch ?? false,
    serviceWorkers: "block",
    // "fresh" shots (login/setup screens) run logged out.
    ...(shot.fresh ? {} : { storageState }),
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
    const file = await capture(name, pages[shot.page], profile, scale, shot);
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
