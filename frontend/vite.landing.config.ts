// Standalone build of the public landing page (site/) for GitHub Pages. The console keeps using vite.config.ts.
// base "./" makes every asset path relative, so the same build works at https://<owner>.github.io/<repo>/ and on a
// custom domain. public/ (favicon, privacy, terms, about, data deletion) is copied as is.
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  root: "site",
  base: "./",
  envDir: "..",
  publicDir: "../public",
  plugins: [react()],
  build: { outDir: "../dist-landing", emptyOutDir: true },
});
