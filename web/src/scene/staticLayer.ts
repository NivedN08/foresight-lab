// The parts of the scene that never move, drawn once at one canvas pixel per art pixel: the sea,
// the land rasterised from Natural Earth outlines, the shoreline, state borders and the boards.

import type { Geo } from "../data/types";
import { project, type Box, type Layout, type MapFrame } from "./layout";
import { C, hexRgb } from "./palette";

type Ring = [number, number][];

/** A 0/1 mask of which pixels of a map frame are land. */
function landMask(f: MapFrame, rings: Ring[]): Uint8Array {
  const { w, h } = f.box;
  const cv = document.createElement("canvas");
  cv.width = w;
  cv.height = h;
  const ctx = cv.getContext("2d")!;
  ctx.fillStyle = "#fff";
  ctx.beginPath();
  for (const ring of rings) {
    ring.forEach(([lon, lat], k) => {
      const p = project(f, lon, lat);
      const x = p.x - f.box.x;
      const y = p.y - f.box.y;
      if (k === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.closePath();
  }
  ctx.fill("evenodd");
  const img = ctx.getImageData(0, 0, w, h).data;
  const mask = new Uint8Array(w * h);
  for (let i = 0; i < w * h; i++) mask[i] = img[i * 4 + 3] > 110 ? 1 : 0;
  return mask;
}

function lineMask(f: MapFrame, lines: Ring[]): Uint8Array {
  const { w, h } = f.box;
  const cv = document.createElement("canvas");
  cv.width = w;
  cv.height = h;
  const ctx = cv.getContext("2d")!;
  ctx.strokeStyle = "#fff";
  ctx.lineWidth = 1;
  for (const line of lines) {
    ctx.beginPath();
    line.forEach(([lon, lat], k) => {
      const p = project(f, lon, lat);
      if (k === 0) ctx.moveTo(p.x - f.box.x, p.y - f.box.y);
      else ctx.lineTo(p.x - f.box.x, p.y - f.box.y);
    });
    ctx.stroke();
  }
  const img = ctx.getImageData(0, 0, w, h).data;
  const mask = new Uint8Array(w * h);
  for (let i = 0; i < w * h; i++) mask[i] = img[i * 4 + 3] > 90 ? 1 : 0;
  return mask;
}

function paintMap(ctx: CanvasRenderingContext2D, f: MapFrame, land: Uint8Array, borders: Uint8Array) {
  const { x: ox, y: oy, w, h } = f.box;
  const out = ctx.createImageData(w, h);
  const sea = hexRgb(C.sea);
  const seaRow = hexRgb(C.seaRow);
  const seaDeep = hexRgb(C.seaDeep);
  const landC = hexRgb(C.land);
  const landHi = hexRgb(C.landHi);
  const landLo = hexRgb(C.landLo);
  const shore = hexRgb(C.shore);
  const shoreLo = hexRgb(C.shoreLo);
  const border = hexRgb(C.border);
  const at = (x: number, y: number) => (x < 0 || y < 0 || x >= w || y >= h ? 0 : land[y * w + x]);
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      const i = y * w + x;
      let c: [number, number, number];
      if (land[i]) {
        const edge = !at(x - 1, y) || !at(x + 1, y) || !at(x, y - 1) || !at(x, y + 1);
        if (edge) c = !at(x, y - 1) || !at(x - 1, y) ? shore : shoreLo;
        else if (borders[i] && (x + y) % 2 === 0) c = border;
        else {
          // A quiet texture: a lit row every few pixels, a shaded speck now and then.
          const n = (x * 73856093) ^ (y * 19349663);
          c = (n & 31) === 0 ? landLo : y % 6 === 0 && (x + (y >> 1)) % 9 < 4 ? landHi : landC;
        }
      } else {
        // Sea: swell lines, darker away from the shore.
        const near = at(x - 2, y) || at(x + 2, y) || at(x, y - 2) || at(x, y + 2) || at(x - 4, y) || at(x + 4, y);
        const swell = y % 5 === 0 && ((x + y * 3) % 11) < 5;
        c = swell ? seaRow : near ? sea : (x + y) % 2 === 0 ? seaDeep : sea;
      }
      out.data[i * 4] = c[0];
      out.data[i * 4 + 1] = c[1];
      out.data[i * 4 + 2] = c[2];
      out.data[i * 4 + 3] = 255;
    }
  }
  ctx.putImageData(out, ox, oy);
  // A frame, as on a printed chart.
  ctx.fillStyle = C.line;
  ctx.fillRect(ox - 1, oy - 1, w + 2, 1);
  ctx.fillRect(ox - 1, oy + h, w + 2, 1);
  ctx.fillRect(ox - 1, oy, 1, h);
  ctx.fillRect(ox + w, oy, 1, h);
}

function paintBoard(ctx: CanvasRenderingContext2D, b: Box) {
  ctx.fillStyle = C.board;
  ctx.fillRect(b.x, b.y, b.w, b.h);
  ctx.fillStyle = C.line;
  ctx.fillRect(b.x, b.y - 1, b.w, 1);
  ctx.fillRect(b.x, b.y + b.h, b.w, 1);
}

export function buildStatic(geo: Geo, L: Layout): HTMLCanvasElement {
  const cv = document.createElement("canvas");
  cv.width = L.world.w;
  cv.height = L.world.h;
  const ctx = cv.getContext("2d")!;
  ctx.imageSmoothingEnabled = false;
  paintBoard(ctx, L.buoyBoard);
  paintBoard(ctx, L.gaugeBoard);
  paintMap(ctx, L.coast, landMask(L.coast, geo.coast_land), lineMask(L.coast, geo.coast_borders));
  paintMap(ctx, L.sky, landMask(L.sky, geo.sky_land), lineMask(L.sky, geo.sky_borders));
  return cv;
}
