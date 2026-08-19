// Verify README screenshots: every file in docs/screenshots is a valid PNG,
// every <img src> in README.md resolves to an existing file, and every
// captured screenshot is actually referenced by the README.
//
//   node scripts/verify-screens.mjs

import { readFileSync, readdirSync, statSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..");
const SHOTS = join(ROOT, "docs", "screenshots");
const README = join(ROOT, "README.md");

const PNG_MAGIC = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);

// The complete suite shoot-screens.mjs captures — a missing one fails.
const REQUIRED = [
  "chat-desktop",
  "memory-desktop",
  "setup-desktop",
  "sessions-desktop",
  "session-chat-desktop",
  "models-desktop",
  "system-desktop",
  "connectors-desktop",
  "chat-mobile",
  "sessions-mobile",
  "session-chat-mobile",
  "models-mobile",
  "system-mobile",
];

let failures = 0;
const fail = (msg) => {
  failures += 1;
  console.error(`FAIL ${msg}`);
};

// 1) Every file in docs/screenshots is a real PNG, and the suite is complete.
const files = readdirSync(SHOTS).filter((f) => f.endsWith(".png")).sort();
if (files.length === 0) fail("no PNG files in docs/screenshots");
for (const name of REQUIRED) {
  if (!files.includes(`${name}.png`)) fail(`missing screenshot: ${name}.png`);
}
for (const f of files) {
  const full = join(SHOTS, f);
  const head = readFileSync(full).subarray(0, 8);
  if (!head.equals(PNG_MAGIC)) fail(`${f}: not a valid PNG (bad magic bytes)`);
  const kb = statSync(full).size / 1024;
  if (kb > 400) fail(`${f}: ${kb.toFixed(0)} KB exceeds the 400 KB cap`);
  console.log(`ok  ${f}  ${kb.toFixed(0)} KB`);
}

// 2) Every README img src (and markdown image) resolves.
const readme = readFileSync(README, "utf8");
const refs = new Set();
for (const m of readme.matchAll(/<img\s[^>]*src="([^"]+)"/g)) refs.add(m[1]);
for (const m of readme.matchAll(/!\[[^\]]*\]\(([^)\s]+)\)/g)) refs.add(m[1]);
if (refs.size === 0) fail("README references no images");
for (const ref of refs) {
  if (/^https?:/.test(ref)) continue;
  try {
    statSync(join(ROOT, ref));
    console.log(`ok  README -> ${ref}`);
  } catch {
    fail(`README img src does not resolve: ${ref}`);
  }
}

// 3) Every img tag has non-empty alt text.
for (const m of readme.matchAll(/<img\s[^>]*>/g)) {
  const tag = m[0];
  const alt = tag.match(/alt="([^"]*)"/);
  if (!alt || !alt[1].trim()) fail(`img tag missing alt text: ${tag.slice(0, 80)}…`);
}

// 4) Every captured screenshot is referenced from the README.
for (const f of files) {
  if (!refs.has(`docs/screenshots/${f}`)) {
    fail(`screenshot not referenced by README: ${f}`);
  }
}

if (failures) {
  console.error(`\n${failures} check(s) failed`);
  process.exit(1);
}
console.log(`\nall checks passed (${files.length} screenshots, ${refs.size} README refs)`);
