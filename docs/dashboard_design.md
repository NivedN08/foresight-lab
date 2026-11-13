# Dashboard design brief

## What it is for

A live screen that judges read from two metres away. Its one job: show a forecast that was locked in before the fact, next to what then actually happened. Two views share one shell: **Coast** and **Turbulence**.

## Identity: a printed chart, overprinted in magenta

The two subjects already have a visual language. Mariners read nautical charts; pilots read aeronautical charts. Both print time-sensitive information over the base chart in magenta. That is exactly what a sealed forecast is, so:

- The base is a chart: quiet, pale, printed-looking.
- Everything the lab **sealed in advance** is drawn in chart magenta.
- Everything that was **measured** is drawn in ink (near-black navy).
- The **official reference** (tide table, standard index) is drawn in chart blue.

Nothing else gets a strong colour. This is a light theme on purpose: it reads on a projector in a bright room, and it is not the dark control-room look every other team will have.

## Tokens

Colour (define as CSS variables on `:root`):

| Name | Hex | Use |
|---|---|---|
| `--water-deep` | `#F4F9FB` | page background, deep water |
| `--water-shoal` | `#D5E9F1` | shallow-water tint, panel fills |
| `--land` | `#EBDDB0` | land on the maps only |
| `--ink` | `#16283A` | text, measured values, coastlines |
| `--ink-soft` | `#5B6B7A` | secondary text, grid lines at 30% |
| `--tide-blue` | `#2A6A9E` | official reference lines |
| `--seal` | `#B4237F` | everything sealed in advance |
| `--miss` | `#C9461B` | a sealed forecast that missed, warnings |

Type (bundled in `dashboard/fonts/`, no network):

- **IBM Plex Sans Condensed** (400/500/600): all interface text and every number. `font-variant-numeric: tabular-nums`. This is the chart-lettering voice.
- **Newsreader** (400, 400 italic, 500): the live sentence at the top of the side column, and italic for names of water on the map (charts set hydrography in italic; land names stay upright sans).
- Scale: 13 / 15 / 18 / 24 / 34 px. Sentence case everywhere. No all-caps labels, no monospace, no decorative eyebrows above headings.

## Layout

```
+--------------------------------------------------------------+
| foresight-lab      [ Coast | Turbulence ]      chain verified |
+---------------------------------------+----------------------+
|                                       | Live sentence        |
|                                       | (Newsreader, 24-34)  |
|            CHART (map)                +----------------------+
|        ~60% width, full height        | Detail chart         |
|                                       | (strip chart / ROC)  |
|                                       +----------------------+
|                                       | Scoreboard table     |
+---------------------------------------+----------------------+
| Log: latest sealed and graded records, oldest left, newest right |
+--------------------------------------------------------------+
```

Left-aligned throughout. The map is the hero. Panels are separated by 1px `--ink` at 15% opacity, not by cards or shadows. No rounded cards; corner radius 2px at most.

## The signature element: the seal mark

One device, used in both views, is the thing people should remember.

- A sealed forecast is an open magenta diamond.
- When the truth arrives the diamond is "opened": it fills with magenta if the forecast was good, or turns to a `--miss` outline with a short strike through it if it missed. This is the only automatic animation on the page (about 400 ms, once per mark, skipped under `prefers-reduced-motion`).
- A thin magenta tick connects each mark back to the moment it was sealed, so the lead time is visible as a length.

## Coast view

**Map.** West Coast from San Diego to the Strait of Juan de Fuca, land in `--land`, water in `--water-deep` with a `--water-shoal` band along the coast, coastline in ink at 0.75px. Each gauge is a small ink circle with a vertical bar beside it: bar height = how far the water is above (up) or below (down) the tide table right now, drawn in ink; the sealed forecast for the next 6 hours is a magenta diamond at its predicted height on the same bar. Buoys (when present) are small ink triangles sized by wave height. Sea names in Newsreader italic. Selected station gets a magenta ring.

**Live sentence** (generated from state, plain words), for example: "San Francisco is running 58 cm above the tide table. Six hours ago the lab sealed 61 cm; the official table said 0."

**Detail chart** for the selected station (default San Francisco): a tide-gauge strip chart, time on x from -24 h to +24 h with a vertical ink rule at now. Blue line: the tide table. Ink line: observed water level, ending at now. Magenta diamonds: sealed forecasts at their target times, future ones open, past ones opened (filled or struck). A lead selector (1, 3, 6, 12, 24 h) chooses which sealed forecasts are shown. Hovering a diamond shows when it was sealed, the predicted and the measured level, and the error.

**Scoreboard.** Rows are leads; columns are the models (tide table, persistence, damped persistence, the lab's formula); cells are mean error in cm with the count of graded forecasts; the best cell in each row is set in weight 600. A toggle switches between "live, sealed" and "backtest".

**Waves strip** (when buoy data exists): a compact second chart of wave height at the selected buoy with the same three colours.

**Alerts.** If the state carries coastal alerts, one line under the sentence in `--miss`.

## Turbulence view

**Map.** Continental US in an Albers projection, land in `--land` at 60% opacity, state lines in ink at 20%. The sealed risk field for the chosen flight level and valid time is drawn as magenta hatching over the top fifth of risk values, with a lighter magenta wash for the next fifth. Jet stream at 250 hPa as thin blue streamlines or barbs. Pilot reports use the standard aviation turbulence symbol (a small peak; one bar for moderate, two for severe): ink-filled when it landed inside a sealed zone, `--miss` outline when it landed outside; smooth-air reports are small open ink circles.

**Controls.** A flight-level ladder (a vertical tape like an altimeter, FL250 to FL410) and a time scrubber from the last 6 graded hours through the next 6 sealed hours. Scrubbing into the past replays reports landing on the field that had been sealed for that hour.

**Live sentence**, for example: "In the last 3 hours, 41 of 52 moderate-or-worse reports fell inside zones the lab sealed 3 hours earlier."

**Detail chart.** ROC curves for the lab's formula and the reference indices, live sealed data, with the area under each curve stated in words beside it.

**Scoreboard.** Rows are leads (1, 3, 6 h); columns are models; cells are area under the curve with the count of graded reports. Same live / backtest toggle.

## Shared

- **Header.** Product name, the two view tabs, and the chain status in plain words: "1,284 forecasts sealed, 902 graded, chain verified". If verification fails, the header turns `--miss` and says which record broke.
- **Log.** A single horizontal row of the most recent records: seal time, what was predicted, and (if graded) what happened. Newest at the right.
- **Empty and error states** say what to do: "No forecasts sealed yet. Start the live job: `.venv/bin/python -m lab.tasks.coast.live run`".
- Polls `/api/<task>/state` every 30 s; shows the age of the data ("updated 2 min ago") and turns it `--miss` if older than 45 min.
- Works at 1280x720 and 1920x1080; below 900px wide the side column stacks under the map. Keyboard focus visible. Selected view and station are kept in the URL hash.

## Files

`dashboard/index.html`, `dashboard/styles.css`, `dashboard/app.js` (shell, polling), `dashboard/coast.js`, `dashboard/turb.js`, `dashboard/seal.js` (the shared mark). Libraries are already in `dashboard/vendor/` (d3 v7, topojson-client) and map outlines in `dashboard/geo/` (`land-10m.json`, `land-50m.json`, `states-10m.json`). No build step, no CDN.
