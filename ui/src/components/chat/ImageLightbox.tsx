// Full-screen viewer for chat images (generated or attached): zoom by wheel /
// pinch / buttons / double-click, drag-to-pan when zoomed, and canvas-based
// downloads as PNG, JPEG or WebP. Dependency-free — CSS transforms and
// createPortal only. Escape, the X button or a backdrop click close it.

import {
  useEffect,
  useRef,
  useState,
  type PointerEvent as ReactPointerEvent,
} from "react";
import { createPortal } from "react-dom";
import { useToast } from "../../hooks/toast";
import { IconDownload, IconMinus, IconPlus, IconX } from "../icons";

const MIN_SCALE = 1;
const MAX_SCALE = 8;
const WHEEL_STEP = 1.2;
const BUTTON_STEP = 1.5;

/** Offered export formats; lossy ones encode at quality 0.92 and JPEG gets a
 *  white background fill first (it has no alpha channel). */
const FORMATS = [
  { label: "PNG", mime: "image/png", ext: "png" },
  { label: "JPEG", mime: "image/jpeg", ext: "jpg" },
  { label: "WebP", mime: "image/webp", ext: "webp" },
] as const;

function swapExtension(filename: string, ext: string): string {
  const base = filename.replace(/\.[a-z0-9]+$/i, "");
  return `${base || "image"}.${ext}`;
}

const clamp = (scale: number) =>
  Math.min(MAX_SCALE, Math.max(MIN_SCALE, scale));

interface View {
  scale: number;
  /** Pan offset in px, applied before the scale (transform-origin center). */
  x: number;
  y: number;
}

type Gesture =
  | { type: "pan"; startX: number; startY: number; originX: number; originY: number }
  | { type: "pinch"; startDist: number; startScale: number; originX: number; originY: number };

export function ImageLightbox({
  src,
  filename,
  caption,
  onClose,
}: {
  src: string;
  filename: string;
  /** Shown under the image — the generation prompt for generated images. */
  caption?: string;
  onClose: () => void;
}) {
  const { toast } = useToast();
  const [view, setView] = useState<View>({ scale: 1, x: 0, y: 0 });
  const viewRef = useRef(view);
  viewRef.current = view;

  const stageRef = useRef<HTMLDivElement>(null);
  const imgRef = useRef<HTMLImageElement>(null);
  const pointers = useRef(new Map<number, { x: number; y: number }>());
  const gestureRef = useRef<Gesture | null>(null);
  /** True right after a drag ended — swallows the click so panning to the
   *  edge never accidentally closes the viewer. */
  const dragged = useRef(false);
  /** Whether the last pointerdown hit the backdrop itself. Pointer capture
   *  retargets pointerup (and thus the click) to the stage, so the click's
   *  own target can't tell image clicks from backdrop clicks. */
  const downOnBackdrop = useRef(false);

  // Escape closes; the page behind must not scroll while the viewer is open.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = prevOverflow;
    };
  }, [onClose]);

  /** Pointer position relative to the stage center — the zoom anchor space. */
  const anchorFrom = (clientX: number, clientY: number) => {
    const rect = stageRef.current?.getBoundingClientRect();
    if (!rect) return { x: 0, y: 0 };
    return {
      x: clientX - rect.left - rect.width / 2,
      y: clientY - rect.top - rect.height / 2,
    };
  };

  /** Multiply the scale by `factor`, keeping the anchor point fixed. */
  const zoomBy = (factor: number, ax = 0, ay = 0) => {
    setView((v) => {
      const scale = clamp(v.scale * factor);
      if (scale === v.scale) return v;
      if (scale <= MIN_SCALE) return { scale: MIN_SCALE, x: 0, y: 0 };
      const k = scale / v.scale;
      return { scale, x: ax - k * (ax - v.x), y: ay - k * (ay - v.y) };
    });
  };

  // Wheel zoom needs preventDefault, and React registers wheel listeners as
  // passive — attach a native non-passive listener instead.
  useEffect(() => {
    const el = stageRef.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const a = anchorFrom(e.clientX, e.clientY);
      zoomBy(e.deltaY < 0 ? WHEEL_STEP : 1 / WHEEL_STEP, a.x, a.y);
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, []);

  // ── drag-to-pan + two-finger pinch (pointer events cover mouse + touch) ───
  const onPointerDown = (e: ReactPointerEvent<HTMLDivElement>) => {
    e.currentTarget.setPointerCapture(e.pointerId);
    downOnBackdrop.current = e.target === e.currentTarget;
    pointers.current.set(e.pointerId, { x: e.clientX, y: e.clientY });
    const v = viewRef.current;
    if (pointers.current.size === 1) {
      gestureRef.current = {
        type: "pan",
        startX: e.clientX,
        startY: e.clientY,
        originX: v.x,
        originY: v.y,
      };
    } else if (pointers.current.size === 2) {
      const [a, b] = [...pointers.current.values()];
      gestureRef.current = {
        type: "pinch",
        startDist: Math.max(1, Math.hypot(a.x - b.x, a.y - b.y)),
        startScale: v.scale,
        originX: v.x,
        originY: v.y,
      };
    }
  };

  const onPointerMove = (e: ReactPointerEvent<HTMLDivElement>) => {
    if (!pointers.current.has(e.pointerId)) return;
    pointers.current.set(e.pointerId, { x: e.clientX, y: e.clientY });
    const g = gestureRef.current;
    if (!g) return;
    if (g.type === "pan" && pointers.current.size === 1) {
      const dx = e.clientX - g.startX;
      const dy = e.clientY - g.startY;
      if (Math.abs(dx) + Math.abs(dy) > 4) dragged.current = true;
      if (viewRef.current.scale > 1) {
        setView((v) => ({ ...v, x: g.originX + dx, y: g.originY + dy }));
      }
    } else if (g.type === "pinch" && pointers.current.size === 2) {
      dragged.current = true;
      const [a, b] = [...pointers.current.values()];
      const dist = Math.max(1, Math.hypot(a.x - b.x, a.y - b.y));
      const scale = clamp(g.startScale * (dist / g.startDist));
      if (scale <= MIN_SCALE) {
        setView({ scale: MIN_SCALE, x: 0, y: 0 });
        return;
      }
      const mid = anchorFrom((a.x + b.x) / 2, (a.y + b.y) / 2);
      const k = scale / g.startScale;
      setView({
        scale,
        x: mid.x - k * (mid.x - g.originX),
        y: mid.y - k * (mid.y - g.originY),
      });
    }
  };

  const onPointerEnd = (e: ReactPointerEvent<HTMLDivElement>) => {
    pointers.current.delete(e.pointerId);
    if (pointers.current.size === 1) {
      // Pinch dropped to one finger — re-anchor as a pan from where it is.
      const [remaining] = [...pointers.current.values()];
      const v = viewRef.current;
      gestureRef.current = {
        type: "pan",
        startX: remaining.x,
        startY: remaining.y,
        originX: v.x,
        originY: v.y,
      };
    } else if (pointers.current.size === 0) {
      gestureRef.current = null;
    }
  };

  const onStageClick = () => {
    if (dragged.current) {
      dragged.current = false;
      return;
    }
    // Backdrop click closes — clicks on the image itself only zoom/pan.
    if (downOnBackdrop.current) onClose();
  };

  const onDoubleClick = (e: React.MouseEvent<HTMLDivElement>) => {
    const a = anchorFrom(e.clientX, e.clientY);
    if (viewRef.current.scale > 1) setView({ scale: 1, x: 0, y: 0 });
    else zoomBy(2, a.x, a.y);
  };

  // ── downloads: draw onto a canvas, encode, save via an object URL ─────────
  const download = (fmt: (typeof FORMATS)[number]) => {
    const img = imgRef.current;
    if (!img || !img.naturalWidth) return;
    const canvas = document.createElement("canvas");
    canvas.width = img.naturalWidth;
    canvas.height = img.naturalHeight;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    if (fmt.mime === "image/jpeg") {
      // JPEG has no alpha — transparent areas must land on white, not black.
      ctx.fillStyle = "#ffffff";
      ctx.fillRect(0, 0, canvas.width, canvas.height);
    }
    try {
      ctx.drawImage(img, 0, 0);
    } catch {
      toast("error", "Couldn't read the image for download");
      return;
    }
    canvas.toBlob(
      (blob) => {
        if (!blob) {
          toast("error", `This browser can't encode ${fmt.label}`);
          return;
        }
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = swapExtension(filename, fmt.ext);
        document.body.appendChild(a);
        a.click();
        a.remove();
        window.setTimeout(() => URL.revokeObjectURL(url), 10_000);
      },
      fmt.mime,
      fmt.mime === "image/png" ? undefined : 0.92,
    );
  };

  const toolButton =
    "flex h-9 w-9 shrink-0 cursor-pointer items-center justify-center rounded-lg text-white/80 transition-colors duration-150 hover:bg-white/15 hover:text-white disabled:cursor-not-allowed disabled:opacity-40";

  return createPortal(
    <div
      role="dialog"
      aria-modal="true"
      aria-label={`Image viewer — ${filename}`}
      className="fixed inset-0 z-50 flex flex-col bg-black/90 backdrop-blur-sm"
    >
      {/* Top bar: filename + close */}
      <div className="flex items-center gap-3 px-4 py-3 pt-safe">
        <p className="min-w-0 flex-1 truncate text-sm font-medium text-white/85">
          {filename}
        </p>
        <button
          type="button"
          aria-label="Close image viewer"
          onClick={onClose}
          className={toolButton}
        >
          <IconX size={18} />
        </button>
      </div>

      {/* Stage: the zoomable/pannable image */}
      <div
        ref={stageRef}
        onClick={onStageClick}
        onDoubleClick={onDoubleClick}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerEnd}
        onPointerCancel={onPointerEnd}
        className="flex min-h-0 flex-1 touch-none items-center justify-center overflow-hidden px-4"
      >
        <img
          ref={imgRef}
          src={src}
          alt={caption || filename}
          draggable={false}
          style={{
            transform: `translate(${view.x}px, ${view.y}px) scale(${view.scale})`,
          }}
          className={
            "max-h-full max-w-full object-contain transition-transform duration-75 select-none " +
            (view.scale > 1 ? "cursor-grab" : "cursor-zoom-in")
          }
        />
      </div>

      {/* Bottom bar: caption, zoom controls, format downloads */}
      <div className="space-y-2 px-4 pb-4 pb-safe">
        {caption && (
          <p
            className="mx-auto line-clamp-2 max-w-xl text-center text-xs text-white/60"
            title={caption}
          >
            {caption}
          </p>
        )}
        <div className="flex flex-wrap items-center justify-center gap-1.5">
          <button
            type="button"
            aria-label="Zoom out"
            disabled={view.scale <= MIN_SCALE}
            onClick={() => zoomBy(1 / BUTTON_STEP)}
            className={toolButton}
          >
            <IconMinus size={17} />
          </button>
          <span
            aria-live="polite"
            className="w-12 text-center font-mono text-xs text-white/70"
          >
            {Math.round(view.scale * 100)}%
          </span>
          <button
            type="button"
            aria-label="Zoom in"
            disabled={view.scale >= MAX_SCALE}
            onClick={() => zoomBy(BUTTON_STEP)}
            className={toolButton}
          >
            <IconPlus size={17} />
          </button>
          <span aria-hidden className="mx-2 h-5 w-px bg-white/20" />
          {FORMATS.map((fmt) => (
            <button
              key={fmt.mime}
              type="button"
              aria-label={`Download as ${fmt.label}`}
              onClick={() => download(fmt)}
              className="inline-flex min-h-9 cursor-pointer items-center gap-1.5 rounded-lg border border-white/20 px-3 text-xs font-medium text-white/80 transition-colors duration-150 hover:bg-white/15 hover:text-white"
            >
              <IconDownload size={13} />
              {fmt.label}
            </button>
          ))}
        </div>
      </div>
    </div>,
    document.body,
  );
}
