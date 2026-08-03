import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  server: {
    // 3000 is the port the client is declared on (vision.md §14), and 127.0.0.1 rather than
    // 0.0.0.0 because a development token reaches this page and rule 13 keeps those on loopback.
    port: 3000,
    host: "127.0.0.1",
    strictPort: true,
  },
});
