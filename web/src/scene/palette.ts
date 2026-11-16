// Colours, from the summit lab's night palette, with the sea, the land and the forecast marks added.
// Shadows lean cool and highlights warm, as on the summit.

export const C = {
  night: "#161a33",
  night2: "#1d2244",
  night3: "#262d55",
  night4: "#303869",
  line: "#3a4475",
  lineHi: "#4d5893",
  snow: "#eef2fa",
  mist: "#b4bcdb",
  fog: "#8590ba",
  snowShade: "#c6cfe6",
  snowDeep: "#9aa6c8",
  ink: "#1b1f3a",
  gold: "#ffc95c",
  goldHi: "#ffdb8a",
  goldLo: "#c9952f",
  glass: "#8fd3e8",
  glassLo: "#5aa7c2",
  red: "#ef5b5b",
  green: "#53d18b",
  orange: "#ee8a3c",
  // The map.
  sea: "#1a2349",
  seaRow: "#1f2a55",
  seaDeep: "#151c3d",
  land: "#353f6b",
  landHi: "#3e4979",
  landLo: "#2e375f",
  shore: "#9aa6c8",
  shoreLo: "#6f7aa6",
  border: "#5a659c",
  board: "#1d2244",
  boardAlt: "#212851",
};

/** Probability ramp for the sealed turbulence grid, light to heavy. */
export const RAMP = ["#5aa7c2", "#b4bcdb", "#ffc95c", "#ee8a3c", "#ef5b5b"];

export function hexRgb(hex: string): [number, number, number] {
  const n = parseInt(hex.slice(1), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

/** 4x4 Bayer matrix, for dithering densities in pixel art. */
export const BAYER = [0, 8, 2, 10, 12, 4, 14, 6, 3, 11, 1, 9, 15, 7, 13, 5].map((v) => (v + 0.5) / 16);
