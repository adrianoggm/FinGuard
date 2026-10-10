import { createHash } from "node:crypto";
import { readFileSync, readdirSync } from "node:fs";
import { fileURLToPath } from "node:url";

const WEB_ROOT = fileURLToPath(new URL("..", import.meta.url)).replace(/[\\/]+$/, "");
const INPUTS = [
  "index.html",
  "package-lock.json",
  "package.json",
  "scripts",
  "src",
  "tsconfig.json",
  "vite.config.ts",
];

function filesUnder(path) {
  const entries = readdirSync(path, { withFileTypes: true });
  return entries.flatMap((entry) => {
    const child = `${path}/${entry.name}`;
    return entry.isDirectory()
      ? filesUnder(child)
      : entry.isFile()
        ? [child]
        : [];
  });
}

/** Fingerprint the web source snapshot Vite reads while loading its config. */
export function webSourceIdentity() {
  const paths = INPUTS.flatMap((input) => {
    const path = `${WEB_ROOT}/${input}`;
    try {
      return readdirSync(path, { withFileTypes: true })
        ? filesUnder(path)
        : [path];
    } catch {
      return [path];
    }
  })
    .map((path) => path.slice(WEB_ROOT.length + 1).replaceAll("\\", "/"))
    .sort();
  const hash = createHash("sha256");
  for (const relative of paths) {
    const name = Buffer.from(relative, "utf8");
    const contents = readFileSync(`${WEB_ROOT}/${relative}`);
    hash.update(name.length.toString(16).padStart(8, "0"));
    hash.update(name);
    hash.update(contents.length.toString(16).padStart(16, "0"));
    hash.update(contents);
  }
  return hash.digest("hex");
}
