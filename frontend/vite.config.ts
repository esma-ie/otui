import { svelte } from "@sveltejs/vite-plugin-svelte";
import { existsSync, readFileSync, readdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { defineConfig, type Plugin } from "vite";

// THIRD-PARTY-NOTICES.txt beside the build: every npm package whose code or files end up in it
// (d3, Svelte's runtime, the DM Sans font under the SIL OFL 1.1, ...), with its licence text.
// Found from the bundle's own module graph, so the list cannot drift from what is shipped.
function thirdPartyNotices(): Plugin {
  const packageRoot = (id: string) => {
    if (!id.includes("node_modules")) return null;
    let dir = dirname(id.replace(/^\0/, "").split("?")[0]);
    while (dir !== dirname(dir)) {
      const pj = join(dir, "package.json");
      if (existsSync(pj) && JSON.parse(readFileSync(pj, "utf8")).name) return dir;
      dir = dirname(dir);
    }
    return null;
  };
  return {
    name: "otui-third-party-notices",
    apply: "build",
    generateBundle() {
      const roots = new Set<string>();
      for (const id of this.getModuleIds()) {
        const root = packageRoot(id);
        if (root) roots.add(root);
      }
      const entries = [...roots]
        .map((root) => {
          const pj = JSON.parse(readFileSync(join(root, "package.json"), "utf8"));
          const file = readdirSync(root).find((f) => /^(licen[cs]e|copying)(\.|$)/i.test(f));
          const text = file ? readFileSync(join(root, file), "utf8").trim() : `(no licence file; package.json says ${pj.license})`;
          return { name: `${pj.name} ${pj.version}`, license: pj.license ?? "see text", text };
        })
        .sort((a, b) => a.name.localeCompare(b.name));
      const head =
        "otui's browser app is AGPL-3.0-or-later (see LICENSES/ in otui's source). It includes the\n" +
        "following third-party packages, under their own licences:\n\n" +
        entries.map((e) => `  ${e.name}  (${e.license})`).join("\n");
      const body = entries.map((e) => `${"=".repeat(78)}\n${e.name}  (${e.license})\n${"=".repeat(78)}\n\n${e.text}\n`);
      this.emitFile({ type: "asset", fileName: "THIRD-PARTY-NOTICES.txt", source: [head, ...body].join("\n\n") });
    },
  };
}

// The build goes into the Python package, so `otui serve` serves it and users never need Node.
// `npm run dev` proxies the API to a running `otui serve` (port 8642).
export default defineConfig({
  plugins: [svelte(), thirdPartyNotices()],
  base: "./",
  build: {
    outDir: fileURLToPath(new URL("../src/otui/web", import.meta.url)),
    emptyOutDir: true,
  },
  server: {
    proxy: { "/api": "http://127.0.0.1:8642" },
  },
});
