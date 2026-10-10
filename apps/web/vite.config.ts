import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";
import { webSourceIdentity } from "./scripts/source_identity.mjs";

export default defineConfig({
  plugins: [react()],
  // This identifies the source snapshot loaded at build/server start; restart Vite after edits when
  // recording an acceptance run so HMR cannot leave the diagnostic fingerprint behind the code.
  define: {
    __FINGUARD_WEB_SOURCE_SHA256__: JSON.stringify(webSourceIdentity()),
  },
  server: {
    // 3000 is the port the client is declared on (vision.md §14), and 127.0.0.1 rather than
    // 0.0.0.0 because a development token reaches this page and rule 13 keeps those on loopback.
    port: 3000,
    host: "127.0.0.1",
    strictPort: true,
  },
});
