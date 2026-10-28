#!/usr/bin/env node
/**
 * Build the deterministic chunk set used by the offline retrieval evaluation.
 * The same chunker is imported by server/index.mjs, so the evaluation runs on
 * exactly the text that the app stores in seekdb.
 */
import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { buildChunks, shortHash } from "../shared/chunk.mjs";

const root = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const corpusDir = path.join(root, "data", "corpus");
const outPath = path.join(root, "data", "eval", "chunks.json");

const names = (await fs.readdir(corpusDir)).filter((name) => /\.(md|txt|markdown)$/i.test(name)).sort();
const docs = [];
let chunkCount = 0;
let charCount = 0;

for (const name of names) {
  const text = await fs.readFile(path.join(corpusDir, name), "utf8");
  const chunks = buildChunks(name, text).map(({ id, docId, docName, index, total, text: chunkText, lang, chars }) => ({
    id,
    docId,
    docName,
    index,
    total,
    text: chunkText,
    lang,
    chars,
  }));
  docs.push({ docName: name, docId: shortHash(`${name}\u0000${text}`), chunks });
  chunkCount += chunks.length;
  charCount += text.length;
  console.log(`${name.padEnd(32)} ${String(chunks.length).padStart(3)} chunks  ${text.length} chars`);
}

const payload = {
  generatedAt: new Date().toISOString(),
  corpus: "data/corpus",
  docs: docs.length,
  documents: docs,
  chunkCount,
  charCount,
  chunks: docs.flatMap((doc) => doc.chunks),
};

await fs.mkdir(path.dirname(outPath), { recursive: true });
await fs.writeFile(outPath, JSON.stringify(payload, null, 2), "utf8");
console.log(`\n${docs.length} docs, ${chunkCount} chunks, ${charCount} chars -> ${path.relative(root, outPath)}`);
