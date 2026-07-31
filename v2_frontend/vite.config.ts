import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  base: "./",
  plugins: [react()],
  build: {
    outDir: "../oled_v2/static",
    emptyOutDir: true,
    target: "es2020",
    sourcemap: false,
    // The intentional embedded ECharts runtime is served from loopback, not a network CDN.
    chunkSizeWarningLimit: 800
  }
});
