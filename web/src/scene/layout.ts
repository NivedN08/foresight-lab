// Where everything sits in the scene, in art pixels. Two maps share one world: the West Coast,
// flanked by a board of buoys on the ocean side and a board of tide gauges on the land side, and
// the sky over the continental US. Both maps are plain equirectangular projections of their boxes.

import type { Geo, Site } from "../data/types";

export type Box = { x: number; y: number; w: number; h: number };

export type MapFrame = {
  box: Box;
  lon0: number;
  lat1: number;
  /** degrees of latitude per art pixel */
  deg: number;
  /** cos(mid latitude), the east-west squeeze */
  kx: number;
};

export type Layout = {
  coast: MapFrame;
  sky: MapFrame;
  buoyBoard: Box;
  gaugeBoard: Box;
  coastGroup: Box;
  world: Box;
  gaugeRows: Map<string, number>;
  buoyRows: Map<string, number>;
  rowH: number;
};

const CAPTION = 30; // room above each group for its caption
const COAST_DEG = 0.07;
const SKY_DEG = 0.13;
const BOARD_W = 140;
const GAP = 10;
const GROUP_GAP = 34;

function frame(box4: [number, number, number, number], deg: number, x: number, y: number): MapFrame {
  const [lon0, lat0, lon1, lat1] = box4;
  const kx = Math.cos((((lat0 + lat1) / 2) * Math.PI) / 180);
  const w = Math.round(((lon1 - lon0) * kx) / deg);
  const h = Math.round((lat1 - lat0) / deg);
  return { box: { x, y, w, h }, lon0, lat1, deg, kx };
}

export function project(f: MapFrame, lon: number, lat: number) {
  return { x: f.box.x + ((lon - f.lon0) * f.kx) / f.deg, y: f.box.y + (f.lat1 - lat) / f.deg };
}

export function unproject(f: MapFrame, x: number, y: number) {
  return { lon: f.lon0 + ((x - f.box.x) * f.deg) / f.kx, lat: f.lat1 - (y - f.box.y) * f.deg };
}

export function makeLayout(geo: Geo, gauges: Site[], buoys: Site[]): Layout {
  const buoyBoard: Box = { x: 0, y: CAPTION, w: BOARD_W, h: 0 };
  const coast = frame(geo.coast_box, COAST_DEG, BOARD_W + GAP, CAPTION);
  buoyBoard.h = coast.box.h;
  const gaugeBoard: Box = { x: coast.box.x + coast.box.w + GAP, y: CAPTION, w: BOARD_W, h: coast.box.h };
  const coastGroup: Box = { x: 0, y: 0, w: gaugeBoard.x + gaugeBoard.w, h: coast.box.h + CAPTION };
  const skyX = coastGroup.w + GROUP_GAP;
  const sky = frame(geo.sky_box, SKY_DEG, skyX, CAPTION);
  // Bottom-align the sky with the coast so the two read as one horizon.
  sky.box.y = CAPTION + Math.max(0, coast.box.h - sky.box.h);
  const world: Box = { x: 0, y: 0, w: sky.box.x + sky.box.w, h: Math.max(coastGroup.h, sky.box.y + sky.box.h) };

  // Board rows follow the sites from north to south.
  const rows = (sites: Site[]) => {
    const order = [...sites].sort((a, b) => b.lat - a.lat);
    const m = new Map<string, number>();
    order.forEach((s, i) => m.set(s.id, i));
    return m;
  };
  const gaugeRows = rows(gauges);
  const buoyRows = rows(buoys);
  const rowH = Math.floor(coast.box.h / Math.max(gauges.length, buoys.length, 1));
  return { coast, sky, buoyBoard, gaugeBoard, coastGroup, world, gaugeRows, buoyRows, rowH };
}

/** Top of a board row. Buoys are fewer, so their rows are centred on the board. */
export function rowTop(L: Layout, board: Box, index: number, count: number): number {
  const used = L.rowH * count;
  return board.y + Math.floor((board.h - used) / 2) + index * L.rowH;
}

/** "SAN FRANCISCO - 18NM West of San Francisco, CA" -> "San Francisco"; "Point Reyes, CA (029)" -> "Point Reyes". */
export function shortName(name: string): string {
  const head = name.split(" - ")[0].split(",")[0].replace(/\s*\(\d+\)\s*$/, "").trim();
  if (head === head.toUpperCase()) return head.toLowerCase().replace(/\b([a-z])/g, (c) => c.toUpperCase()).replace(/\bPt\b/, "Point");
  return head;
}
