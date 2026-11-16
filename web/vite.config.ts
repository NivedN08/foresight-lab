import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The page works from the snapshot in public/data alone. In development, /api is proxied to the
// existing dashboard server (python -m uvicorn lab.server:app --port 8765) so the page can refresh
// the live numbers when that server happens to be running.
export default defineConfig({
  plugins: [react()],
  base: "./",
  server: {
    port: 5174,
    strictPort: true,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8765",
        changeOrigin: false,
        // When the dashboard server is not running, answer "no content" instead of a 502, so the
        // page quietly stays on its snapshot.
        configure: (proxy) => {
          proxy.on("error", (_err, _req, res) => {
            const out = res as unknown as { headersSent?: boolean; writeHead?: (code: number) => void; end: () => void };
            if (out.writeHead && !out.headersSent) out.writeHead(204);
            out.end();
          });
        },
      },
    },
  },
  preview: { port: 5174 },
});
