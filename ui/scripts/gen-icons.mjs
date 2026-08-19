// Generates the PWA icon PNGs (192/512 + maskable variants) with zero
// dependencies: a minimal PNG encoder (zlib deflate + hand-rolled chunks)
// rasterizing the Forge mark — an ember diamond over a dark rounded tile.
// Run: node scripts/gen-icons.mjs   (writes into ui/public/)

import { deflateSync } from "node:zlib";
import { mkdirSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const OUT_DIR = join(dirname(fileURLToPath(import.meta.url)), "..", "public");

// ── PNG encoding ────────────────────────────────────────────────────────────

const CRC_TABLE = (() => {
  const table = new Int32Array(256);
  for (let n = 0; n < 256; n++) {
    let c = n;
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
    table[n] = c;
  }
  return table;
})();

function crc32(buf) {
  let c = 0xffffffff;
  for (let i = 0; i < buf.length; i++) {
    c = CRC_TABLE[(c ^ buf[i]) & 0xff] ^ (c >>> 8);
  }
  return (c ^ 0xffffffff) >>> 0;
}

function chunk(type, data) {
  const out = Buffer.alloc(8 + data.length + 4);
  out.writeUInt32BE(data.length, 0);
  out.write(type, 4, "ascii");
  data.copy(out, 8);
  out.writeUInt32BE(crc32(out.subarray(4, 8 + data.length)), 8 + data.length);
  return out;
}

function encodePng(width, height, rgba) {
  const signature = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(width, 0);
  ihdr.writeUInt32BE(height, 4);
  ihdr[8] = 8; // bit depth
  ihdr[9] = 6; // color type RGBA
  // raw scanlines with filter byte 0
  const raw = Buffer.alloc(height * (1 + width * 4));
  for (let y = 0; y < height; y++) {
    const rowStart = y * (1 + width * 4);
    raw[rowStart] = 0;
    rgba.copy(raw, rowStart + 1, y * width * 4, (y + 1) * width * 4);
  }
  return Buffer.concat([
    signature,
    chunk("IHDR", ihdr),
    chunk("IDAT", deflateSync(raw, { level: 9 })),
    chunk("IEND", Buffer.alloc(0)),
  ]);
}

// ── Forge mark rasterizer ───────────────────────────────────────────────────

const lerp = (a, b, t) => a + (b - a) * t;

/**
 * Per-sample color in normalized coords (u, v in 0..1, y down).
 * Returns [r, g, b, a] 0..255.
 */
function sample(u, v, maskable) {
  // Background tile: subtle vertical gradient of the app background color.
  const bgTop = [0x14, 0x18, 0x21];
  const bgBottom = [0x0b, 0x0d, 0x12];
  // Rounded-rect clip for the non-maskable icon; maskable icons bleed fully.
  if (!maskable) {
    const r = 0.21; // corner radius fraction
    const cx = Math.min(Math.max(u, r), 1 - r);
    const cy = Math.min(Math.max(v, r), 1 - r);
    const d = Math.hypot(u - cx, v - cy);
    if (d > r) return [0, 0, 0, 0];
  }
  const bg = [
    lerp(bgTop[0], bgBottom[0], v),
    lerp(bgTop[1], bgBottom[1], v),
    lerp(bgTop[2], bgBottom[2], v),
  ];

  // The mark: an ember diamond (rotated square) with a notch cut from the
  // bottom, reading as a flame/anvil spark. Maskable variants shrink the
  // glyph into the 80% safe zone.
  const scale = maskable ? 0.72 : 1.0;
  const gx = (u - 0.5) / scale;
  const gy = (v - 0.47) / scale;

  const outer = Math.abs(gx) / 0.30 + Math.abs(gy) / 0.34; // <=1 inside
  const notch =
    Math.abs(gx) / 0.115 + Math.abs(gy - 0.21) / 0.15; // cutout diamond

  if (outer <= 1 && notch > 1) {
    // Ember gradient: hot amber at the top to deep orange at the base.
    const t = Math.min(Math.max((gy + 0.34) / 0.68, 0), 1);
    const top = [0xfc, 0xd3, 0x4d]; // amber-300
    const bottom = [0xea, 0x66, 0x0c]; // orange-600
    return [
      Math.round(lerp(top[0], bottom[0], t)),
      Math.round(lerp(top[1], bottom[1], t)),
      Math.round(lerp(top[2], bottom[2], t)),
      255,
    ];
  }
  return [Math.round(bg[0]), Math.round(bg[1]), Math.round(bg[2]), 255];
}

function render(size, maskable) {
  const rgba = Buffer.alloc(size * size * 4);
  const SS = 3; // supersampling grid
  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      let r = 0;
      let g = 0;
      let b = 0;
      let a = 0;
      for (let sy = 0; sy < SS; sy++) {
        for (let sx = 0; sx < SS; sx++) {
          const u = (x + (sx + 0.5) / SS) / size;
          const v = (y + (sy + 0.5) / SS) / size;
          const [cr, cg, cb, ca] = sample(u, v, maskable);
          r += cr * ca;
          g += cg * ca;
          b += cb * ca;
          a += ca;
        }
      }
      const n = SS * SS;
      const idx = (y * size + x) * 4;
      rgba[idx] = a ? Math.round(r / a) : 0;
      rgba[idx + 1] = a ? Math.round(g / a) : 0;
      rgba[idx + 2] = a ? Math.round(b / a) : 0;
      rgba[idx + 3] = Math.round(a / n);
    }
  }
  return encodePng(size, size, rgba);
}

mkdirSync(OUT_DIR, { recursive: true });
const jobs = [
  ["icon-192.png", 192, false],
  ["icon-512.png", 512, false],
  ["icon-maskable-192.png", 192, true],
  ["icon-maskable-512.png", 512, true],
];
for (const [name, size, maskable] of jobs) {
  const png = render(size, maskable);
  writeFileSync(join(OUT_DIR, name), png);
  console.log(`wrote ${name} (${png.length} bytes)`);
}
