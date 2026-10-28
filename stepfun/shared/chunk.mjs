/**
 * Deterministic, language-agnostic chunker shared by the ingest API and the
 * offline evaluation pipeline. Keeping one implementation means the chunks
 * measured by `scripts/eval_retrieval.py` are exactly the chunks stored in
 * seekdb by the web app.
 */

const SENTENCE_BOUNDARY = /(?<=[。！？!?；;])|\n+/u;

/** Collapse whitespace but keep paragraph information. */
export function normalizeText(text) {
  return String(text ?? "")
    .replace(/\r\n?/g, "\n")
    .replace(/\u00a0/g, " ")
    // Strip the light markdown used by the sample corpus so the stored text and
    // the embeddings are not polluted by formatting characters.
    .replace(/^\s{0,3}#{1,6}\s+/gm, "")
    .replace(/^\s*```[\w-]*\s*$/gm, "")
    .replace(/^\s{0,3}>\s?/gm, "")
    .replace(/\*\*([^*]+)\*\*/g, "$1")
    .replace(/(^|[^\w])__([^_]+)__([^\w]|$)/g, "$1$2$3")
    .replace(/`([^`]+)`/g, "$1")
    .replace(/!?\[([^\]]+)\]\([^)]*\)/g, "$1")
    .replace(/[ \t]+/g, " ")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

function splitSentences(paragraph) {
  return paragraph
    .split(SENTENCE_BOUNDARY)
    .map((s) => s.trim())
    .filter(Boolean);
}

/**
 * Split a document into overlapping chunks.
 *
 * @param {string} text
 * @param {{maxChars?: number, overlap?: number, minChars?: number}} [options]
 * @returns {string[]}
 */
export function chunkText(text, options = {}) {
  const maxChars = options.maxChars ?? 420;
  const overlap = Math.min(options.overlap ?? 80, Math.floor(maxChars / 3));
  const minChars = options.minChars ?? 24;

  const normalized = normalizeText(text);
  if (!normalized) return [];

  const paragraphs = normalized.split(/\n{2,}/).map((p) => p.trim()).filter(Boolean);
  const units = [];
  for (const paragraph of paragraphs) {
    for (const sentence of splitSentences(paragraph)) {
      if (sentence.length <= maxChars) {
        units.push(sentence);
      } else {
        // Hard split very long sentences (tables, code, minified text).
        for (let i = 0; i < sentence.length; i += maxChars) {
          units.push(sentence.slice(i, i + maxChars));
        }
      }
    }
  }

  const chunks = [];
  let buffer = "";
  for (const unit of units) {
    const candidate = buffer ? `${buffer}\n${unit}` : unit;
    if (candidate.length <= maxChars || !buffer) {
      buffer = candidate;
      continue;
    }
    chunks.push(buffer);
    // Sentence-aligned overlap: keep the tail of the previous chunk.
    const tail = buffer.slice(Math.max(0, buffer.length - overlap));
    const cut = tail.search(/[。！？!?；;\n]/u);
    const carry = cut >= 0 ? tail.slice(cut + 1).trim() : tail.trim();
    buffer = (carry ? `${carry}\n${unit}` : unit).slice(-maxChars);
  }
  if (buffer) chunks.push(buffer);

  // Merge tiny trailing chunks into their predecessor.
  const merged = [];
  for (const chunk of chunks) {
    if (merged.length && chunk.length < minChars) {
      merged[merged.length - 1] = `${merged[merged.length - 1]}\n${chunk}`.slice(0, maxChars + minChars);
    } else {
      merged.push(chunk);
    }
  }
  return merged;
}

/** Stable short hash used for document ids (FNV-1a, 32-bit, base36). */
export function shortHash(input) {
  let hash = 0x811c9dc5;
  const text = String(input);
  for (let i = 0; i < text.length; i += 1) {
    hash ^= text.charCodeAt(i);
    hash = Math.imul(hash, 0x01000193);
  }
  return (hash >>> 0).toString(36).padStart(7, "0");
}

/** Build the chunk records that are sent to the browser for embedding. */
export function buildChunks(docName, text, options = {}) {
  const docId = `${shortHash(`${docName}\u0000${text}`)}`;
  const pieces = chunkText(text, options);
  return pieces.map((piece, index) => ({
    id: `${docId}#${index}`,
    docId,
    docName,
    index,
    total: pieces.length,
    text: piece,
    lang: /[\u4e00-\u9fff]/.test(piece) ? "zh" : "en",
    chars: piece.length,
  }));
}

/**
 * Sanitize a user query before handing it to seekdb's full-text query_string.
 * Lucene-ish operators are removed instead of escaped so a stray quote can
 * never turn a search into a syntax error.
 */
export function sanitizeQuery(query) {
  const cleaned = String(query ?? "")
    .replace(/[\\"'`~^*?:()[\]{}!+\-&|/<>]/g, " ")
    .replace(/\s+/g, " ")
    .trim();
  return (cleaned || String(query ?? "").trim()).slice(0, 120);
}
