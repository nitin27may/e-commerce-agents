// Serve web/out the way GitHub Pages does: files under a sub-path, 404.html for a miss. No dependencies.
//
//   node scripts/serve-preview.mjs [port] [basePath]      # defaults: 4173 /e-commerce-agents/preview
import { createServer } from "node:http";
import { existsSync, readFileSync, statSync } from "node:fs";
import { dirname, extname, join, normalize } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..", "out");
const port = Number(process.argv[2] ?? 4173);
const base = (process.argv[3] ?? "/e-commerce-agents/preview").replace(/\/$/, "");
const types = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript",
  ".css": "text/css",
  ".json": "application/json",
  ".png": "image/png",
  ".ico": "image/x-icon",
  ".svg": "image/svg+xml",
  ".txt": "text/plain",
  ".woff2": "font/woff2",
};

createServer((req, res) => {
  const url = new URL(req.url ?? "/", "http://localhost");
  if (url.pathname === "/") {
    res.writeHead(302, { Location: `${base}/` }).end();
    return;
  }
  let file = null;
  if (url.pathname === base || url.pathname.startsWith(`${base}/`)) {
    const rel = normalize(decodeURIComponent(url.pathname.slice(base.length))).replace(/^(\.\.[/\\])+/, "");
    let candidate = join(root, rel);
    if (existsSync(candidate) && statSync(candidate).isDirectory()) candidate = join(candidate, "index.html");
    if (existsSync(candidate) && candidate.startsWith(root)) file = candidate;
  }
  const status = file ? 200 : 404;
  const target = file ?? join(root, "404.html");
  if (!existsSync(target)) {
    res.writeHead(404).end("not found");
    return;
  }
  res.writeHead(status, { "Content-Type": types[extname(target)] ?? "application/octet-stream" });
  res.end(readFileSync(target));
}).listen(port, "127.0.0.1", () => console.log(`preview on http://127.0.0.1:${port}${base}/`));
