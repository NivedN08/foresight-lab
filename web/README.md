# Foresight Lab display page

One page for showing the lab to people: the coast's gauges and buoys and the sky over the US with
the forecasts sealed for them, the ledger as a timeline, the scoreboard, the research loop and a
six-stop presentation. Every number comes from the run records; nothing is typed in.

## Refresh the data

The page reads a snapshot in `web/public/data/`. Take a new one from the main checkout (read only):

    python3 web/build_data.py                  # default root: the main checkout
    python3 web/build_data.py --root /path/to/checkout

It needs only the Python standard library and writes nothing outside `web/public/data/`. The page
shows when the snapshot was taken.

## Run

    cd web
    npm install
    npm run dev        # http://localhost:5174

When the dashboard server is running on port 8765 (`.venv/bin/python -m uvicorn lab.server:app
--port 8765`), the dev server proxies `/api` to it and the page refreshes the record counts and
scoreboards every minute. Without it the page shows the snapshot alone.

## Build

    npm run build      # type-checks, then writes web/dist/
    npm run preview    # serves web/dist/ on port 5174

`dist/` is static and opens from any web server; it always shows the snapshot it was built with.

Keys: right and left arrows step between seals (or between presentation stops), space plays the
ledger, Escape closes the presentation.
