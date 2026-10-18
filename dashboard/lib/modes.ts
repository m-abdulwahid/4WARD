import type { Leg } from "@/lib/api";

// One style per transport mode, shared by the map, the itinerary and the legend.
// Colour is never the only cue: each mode also has its own line pattern / weight and an icon.
export type ModeKey = "walk" | "bike" | "subway" | "streetcar" | "bus" | "go_train" | "car" | "rideshare" | "park_ride";

export type ModeStyle = {
  label: string;
  color: string; // fallback; transit legs use their TTC line colour
  width: number;
  dash?: number[]; // in line widths, as MapLibre expects
  casing: string; // outline under the line so it reads on any basemap
  pattern: string; // words for the legend: what the line looks like
  icon: string; // inner SVG markup, 24x24 viewBox, stroke = currentColor
};

const I = {
  walk: '<circle cx="13" cy="4" r="2"/><path d="M11 21l1.5-6 2.5 2v4"/><path d="M8 12l3-3 3 1 2 3"/><path d="M12.5 9.5l-1 5.5"/>',
  bike: '<circle cx="5.5" cy="17.5" r="3.5"/><circle cx="18.5" cy="17.5" r="3.5"/><circle cx="15" cy="5" r="1"/><path d="M12 17.5V14l-3-3 4-3 2 3h2"/>',
  subway:
    '<path d="M8 3.1V7a4 4 0 0 0 8 0V3.1"/><path d="M9 15l-1-1"/><path d="M15 15l1-1"/><path d="M9 19c-2.8 0-5-2.2-5-5v-4a8 8 0 0 1 16 0v4c0 2.8-2.2 5-5 5Z"/><path d="M8 19l-2 3"/><path d="M16 19l2 3"/>',
  go_train:
    '<rect x="4" y="3" width="16" height="13" rx="3"/><path d="M4 10h16"/><circle cx="8.5" cy="13" r="1"/><circle cx="15.5" cy="13" r="1"/><path d="M9 19l-2 3"/><path d="M15 19l2 3"/><path d="M12 3V1"/>',
  streetcar: '<rect x="5" y="4" width="14" height="14" rx="2"/><path d="M5 11h14"/><path d="M12 4V2"/><path d="M8 18l-2 3"/><path d="M16 18l2 3"/><path d="M9 15h.01"/><path d="M15 15h.01"/>',
  bus: '<rect x="4" y="3" width="16" height="15" rx="2"/><path d="M4 11h16"/><path d="M12 3v8"/><path d="M8 18v3"/><path d="M16 18v3"/><path d="M8 15h.01"/><path d="M16 15h.01"/>',
  car: '<path d="M5 17H3v-5l2-5h14l2 5v5h-2"/><path d="M3 12h18"/><circle cx="7.5" cy="17" r="2"/><circle cx="16.5" cy="17" r="2"/><path d="M9.5 17h5"/>',
  rideshare:
    '<path d="M10 2h4"/><path d="M5 17H3v-5l2-5h14l2 5v5h-2"/><path d="M3 12h18"/><circle cx="7.5" cy="17" r="2"/><circle cx="16.5" cy="17" r="2"/><path d="M9.5 17h5"/>',
  park_ride: '<rect x="3" y="3" width="18" height="18" rx="3"/><path d="M9 17V7h4a3 3 0 0 1 0 6H9"/>',
};

export const MODES: Record<ModeKey, ModeStyle> = {
  walk: { label: "Walk", color: "#52525b", width: 4, dash: [0.6, 1.6], casing: "#ffffff", pattern: "dotted", icon: I.walk },
  bike: { label: "Bike, e-bike or Bike Share", color: "#15803d", width: 4.5, dash: [2, 1.2], casing: "#ffffff", pattern: "dashed", icon: I.bike },
  subway: { label: "Subway", color: "#2563eb", width: 6.5, casing: "#18181b", pattern: "thick, dark edge, TTC line colour", icon: I.subway },
  streetcar: { label: "Streetcar or LRT", color: "#ED1C24", width: 5, casing: "#ffffff", pattern: "solid, TTC line colour", icon: I.streetcar },
  bus: { label: "Bus", color: "#1d4ed8", width: 5, dash: [3, 1], casing: "#ffffff", pattern: "long dashes", icon: I.bus },
  go_train: { label: "GO Train", color: "#09753a", width: 6.5, casing: "#18181b", pattern: "thick, dark edge, GO green", icon: I.go_train },
  car: { label: "Drive", color: "#334155", width: 5.5, casing: "#ffffff", pattern: "solid", icon: I.car },
  rideshare: { label: "Rideshare or taxi", color: "#be185d", width: 5.5, casing: "#ffffff", pattern: "solid", icon: I.rideshare },
  park_ride: { label: "Drive to park & ride lot", color: "#c2410c", width: 5.5, casing: "#ffffff", pattern: "solid", icon: I.park_ride },
};

export const MODE_ORDER: ModeKey[] = ["walk", "bike", "subway", "streetcar", "bus", "go_train", "car", "rideshare", "park_ride"];

/** Which mode a leg is. Car legs are split by the candidate they belong to (rideshare, park & ride). */
export function legMode(leg: Leg, candidateId: string): ModeKey {
  switch (leg.mode) {
    case "walk":
      return "walk";
    case "bike":
    case "ebike":
    case "bikeshare":
      return "bike";
    case "subway":
      return "subway";
    case "lrt_streetcar":
      return "streetcar";
    case "bus":
      return "bus";
    case "go_train":
      return "go_train";
    case "car":
      if (candidateId.startsWith("rideshare") || /rideshare|taxi/i.test(leg.label)) return "rideshare";
      if (candidateId.startsWith("park_ride")) return "park_ride";
      return "car";
    default:
      return "car";
  }
}

/** Line colour for a leg: the TTC line colour for transit when the backend sent one. */
export function legColor(leg: Leg, mode: ModeKey): string {
  if ((mode === "subway" || mode === "streetcar" || mode === "bus" || mode === "go_train") && leg.color) return leg.color;
  return MODES[mode].color;
}

/** Dark or white icon ink, whichever reads better on the given background. */
export function inkOn(hex: string): string {
  const n = parseInt(hex.replace("#", "").padEnd(6, "0").slice(0, 6), 16);
  const [r, g, b] = [(n >> 16) & 255, (n >> 8) & 255, n & 255].map((v) => {
    const c = v / 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  });
  const L = 0.2126 * r + 0.7152 * g + 0.0722 * b;
  return L > 0.179 ? "#18181b" : "#ffffff"; // the luminance where black and white give equal contrast
}

export function iconSvg(mode: ModeKey, size = 14, color = "currentColor"): string {
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="${size}" height="${size}" fill="none" stroke="${color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${MODES[mode].icon}</svg>`;
}
