import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";

const vendor = fileURLToPath(new URL("../vendor/hanabi-live", import.meta.url));

export default defineConfig({
  root: fileURLToPath(new URL(".", import.meta.url)),
  resolve: {
    alias: {
      // hanab.live source at the pinned commit (review/setup.sh). GPL-3.0.
      "@hanabi-live/game": `${vendor}/packages/game/src/index.ts`,
      "hanabi-live-ui": `${vendor}/packages/client/src/game/ui`,
      "hanabi-live-img": `${vendor}/public/img`,
    },
  },
  // The vendored packages' tsconfig.json files extend monorepo configs we don't check out.
  esbuild: { tsconfigRaw: JSON.stringify({ compilerOptions: { target: "es2022", useDefineForClassFields: true } }) },
  server: {
    fs: { allow: [".."] },
    proxy: { "/api": "http://127.0.0.1:8765" },
  },
  build: { outDir: "dist", emptyOutDir: true, chunkSizeWarningLimit: 2000 },
});
