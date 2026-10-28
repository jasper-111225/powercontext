#!/usr/bin/env node
/** Start the API server and the Vite dev server together (Ctrl+C stops both). */
import { spawn } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const children = [];

function run(name, command, args) {
  const child = spawn(command, args, {
    cwd: root,
    env: { ...process.env, FORCE_COLOR: "1" },
    stdio: ["ignore", "pipe", "pipe"],
  });
  child.stdout.on("data", (chunk) => process.stdout.write(`[${name}] ${chunk}`));
  child.stderr.on("data", (chunk) => process.stderr.write(`[${name}] ${chunk}`));
  child.on("exit", (code) => {
    if (code !== 0 && code !== null) console.error(`[${name}] exited with ${code}`);
  });
  children.push(child);
  return child;
}

run("api", process.execPath, ["server/index.mjs"]);
run("web", process.execPath, [path.join(root, "node_modules", "vite", "bin", "vite.js")]);

const shutdown = () => {
  for (const child of children) child.kill("SIGTERM");
  process.exit(0);
};
process.on("SIGINT", shutdown);
process.on("SIGTERM", shutdown);
