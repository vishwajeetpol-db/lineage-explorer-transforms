import { useCallback, useEffect, useRef, useState } from "react";
import { X } from "lucide-react";

/** A floating, draggable panel — grabbed by its title bar. Matches the POC's
 *  detail popups: opens near the top-right, can be dragged anywhere, closes via
 *  the X. Body scrolls; the panel stays within the viewport. */

interface Props {
  title: React.ReactNode;
  subtitle?: string;
  accent?: string; // tailwind text color for the title icon/dot
  initial?: { x: number; y: number };
  width?: number;
  onClose: () => void;
  children: React.ReactNode;
  /** Stacking helpers so the focused panel comes forward. */
  z?: number;
  onFocus?: () => void;
}

export default function DraggablePanel({
  title, subtitle, initial, width = 380, onClose, children, z = 40, onFocus,
}: Props) {
  const [pos, setPos] = useState(initial ?? { x: window.innerWidth - width - 32, y: 96 });
  const drag = useRef<{ dx: number; dy: number } | null>(null);

  const onPointerDown = useCallback((e: React.PointerEvent) => {
    onFocus?.();
    drag.current = { dx: e.clientX - pos.x, dy: e.clientY - pos.y };
    (e.target as HTMLElement).setPointerCapture(e.pointerId);
  }, [pos, onFocus]);

  const onPointerMove = useCallback((e: React.PointerEvent) => {
    if (!drag.current) return;
    const maxX = window.innerWidth - 60;
    const maxY = window.innerHeight - 40;
    setPos({
      x: Math.min(Math.max(0, e.clientX - drag.current.dx), maxX),
      y: Math.min(Math.max(0, e.clientY - drag.current.dy), maxY),
    });
  }, []);

  const onPointerUp = useCallback((e: React.PointerEvent) => {
    drag.current = null;
    try { (e.target as HTMLElement).releasePointerCapture(e.pointerId); } catch { /* noop */ }
  }, []);

  // Keep the panel on-screen if the window resizes smaller.
  useEffect(() => {
    const onResize = () => setPos((p) => ({
      x: Math.min(p.x, window.innerWidth - 60),
      y: Math.min(p.y, window.innerHeight - 40),
    }));
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  return (
    <div
      className="fixed rounded-2xl border border-white/[0.1] bg-[#12121E]/95 backdrop-blur-md shadow-[0_16px_50px_rgba(0,0,0,0.6)] flex flex-col max-h-[80vh]"
      style={{ left: pos.x, top: pos.y, width, zIndex: z }}
      onMouseDown={onFocus}
    >
      {/* Title bar (drag handle) */}
      <div
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        className="flex items-center gap-2 px-3.5 py-2.5 border-b border-white/[0.06] cursor-move select-none rounded-t-2xl"
      >
        <div className="flex items-center gap-2 flex-1 min-w-0">
          <span className="text-[13px] font-semibold text-slate-100 truncate">{title}</span>
        </div>
        <button
          onClick={onClose}
          onPointerDown={(e) => e.stopPropagation()}
          className="text-slate-500 hover:text-slate-200 transition-colors shrink-0"
        >
          <X size={15} />
        </button>
      </div>
      {subtitle && (
        <div className="px-3.5 pt-2 font-mono text-[10px] text-slate-500 truncate">{subtitle}</div>
      )}
      {/* Body */}
      <div className="flex-1 overflow-y-auto p-3.5">{children}</div>
    </div>
  );
}
