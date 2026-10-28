import { fileURLToPath } from "node:url";
import path from "node:path";

import { defineConfig } from "vite";

const root = fileURLToPath(new URL("./web", import.meta.url));
const outDir = fileURLToPath(new URL("./dist", import.meta.url));

const isolationHeaders = {
  "Cross-Origin-Opener-Policy": "same-origin",
  "Cross-Origin-Embedder-Policy": "require-corp",
  "Cross-Origin-Resource-Policy": "same-origin",
};

export default defineConfig({
  root,
  publicDir: path.join(root, "public"),
  esbuild: { jsx: "automatic" },
  worker: { format: "es" },
  build: {
    outDir,
    emptyOutDir: true,
    target: "es2022",
    chunkSizeWarningLimit: 4096,
  },
  optimizeDeps: {
    exclude: ["@huggingface/transformers"],
  },
  server: {
    port: 5173,
    strictPort: true,
    headers: isolationHeaders,
    proxy: {
      "/api": "http://127.0.0.1:3000",
    },
  },
  preview: {
    port: 4173,
    headers: isolationHeaders,
  },
});
