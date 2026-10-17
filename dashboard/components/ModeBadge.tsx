import { MODES, ModeKey, iconSvg, inkOn } from "@/lib/modes";

/** Round mode icon on the mode's colour. Decorative: the mode name is always written next to it. */
export default function ModeBadge({ mode, color, size = 22 }: { mode: ModeKey; color?: string; size?: number }) {
  const bg = color ?? MODES[mode].color;
  return (
    <span
      aria-hidden
      className="inline-flex shrink-0 items-center justify-center rounded-full ring-2 ring-white"
      style={{ width: size, height: size, background: bg }}
      // static markup from lib/modes, no user input
      dangerouslySetInnerHTML={{ __html: iconSvg(mode, Math.round(size * 0.58), inkOn(bg)) }}
    />
  );
}

/** A short stretch of line drawn the way the map draws this mode (pattern + weight), for legends and timelines. */
export function ModeLine({ mode, color, length = 28, vertical = false }: { mode: ModeKey; color?: string; length?: number; vertical?: boolean }) {
  const st = MODES[mode];
  const c = color ?? st.color;
  const w = Math.max(2, Math.round(st.width * 0.6));
  const dash = st.dash ? st.dash.map((d) => Math.max(1, d * w)).join(" ") : undefined;
  const [x2, y2] = vertical ? [w + 2, length - w] : [length - w, w + 2];
  const [x1, y1] = vertical ? [w + 2, w] : [w, w + 2];
  return (
    <svg aria-hidden width={vertical ? w * 2 + 4 : length} height={vertical ? length : w * 2 + 4} className="shrink-0">
      {mode !== "walk" && <line x1={x1} y1={y1} x2={x2} y2={y2} stroke={st.casing} strokeWidth={w + 3} strokeLinecap="round" />}
      <line x1={x1} y1={y1} x2={x2} y2={y2} stroke={c} strokeWidth={w} strokeLinecap="round" strokeDasharray={dash} />
    </svg>
  );
}
