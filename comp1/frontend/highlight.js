// Syntax colouring for the code inspector (Python, JSON, live trace) and the console.
// Loaded as a plain <script> before app.js; also require()-able from node for tests,
// which is why every browser global is guarded — the same arrangement as blocks.js.
//
// Two rules hold everywhere in here:
//
// * Tokenizers are lossless. Joining the text of every token gives back the input
//   exactly, so Copy (which reads textContent) copies what it always did, and a
//   tokenizer that misreads something can only colour it wrongly, never drop it.
// * Rendering never goes through innerHTML. Console lines carry text the server
//   sent, and a message containing "<img onerror=…>" must print as those
//   characters, so every token becomes a text node, wrapped in a span by class.
//
// Wrapped in a function: as a classic script, a top-level `json` or `render` here
// would become a window global every other script shares.

(function () {
  // Classes are short and prefixed (`hl-kw`, `hl-str`, …); style.css colours them.
  const PY_KEYWORDS = new Set([
    "and", "as", "break", "class", "continue", "def", "elif", "else", "except",
    "for", "from", "global", "if", "import", "in", "is", "lambda", "nonlocal",
    "not", "or", "pass", "raise", "return", "try", "while", "with", "yield",
  ]);
  const PY_CONSTANTS = new Set(["True", "False", "None"]);
  const PY_BUILTINS = new Set([
    "abs", "float", "getattr", "int", "len", "max", "min", "print", "range", "round",
  ]);

  // One pass, one regex: each alternative is a capture group, and whichever group
  // matched names the token. Anything none of them match (spaces, operators,
  // brackets) falls through as plain text between matches.
  const PY_RE = new RegExp([
    /(#[^\n]*)/.source,                                         // 1 comment
    /("(?:[^"\\\n]|\\.)*"?|'(?:[^'\\\n]|\\.)*'?)/.source,       // 2 string (tolerates unterminated)
    /(\b\d+(?:\.\d+)?(?:[eE][+-]?\d+)?\b)/.source,             // 3 number
    /([A-Za-z_]\w*)/.source,                                    // 4 identifier
  ].join("|"), "g");

  function pushPlain(out, text) {
    if (!text) return;
    const last = out[out.length - 1];
    // Merge runs of uncoloured text so the DOM gets one node, not one per space.
    if (last && last[0] === null) last[1] += text;
    else out.push([null, text]);
  }

  function python(src) {
    const out = [];
    let at = 0;
    let afterDef = false;
    for (const m of String(src).matchAll(PY_RE)) {
      pushPlain(out, src.slice(at, m.index));
      at = m.index + m[0].length;
      const [text, comment, string, number, ident] = m;
      if (comment) out.push(["hl-com", text]);
      else if (string) out.push(["hl-str", text]);
      else if (number) out.push(["hl-num", text]);
      else if (ident) {
        let cls = null;
        if (afterDef) cls = "hl-def";
        else if (PY_KEYWORDS.has(ident)) cls = "hl-kw";
        else if (PY_CONSTANTS.has(ident)) cls = "hl-const";
        else if (PY_BUILTINS.has(ident)) cls = "hl-builtin";
        else if (src[at] === "(") cls = "hl-fn";
        if (cls) out.push([cls, text]);
        else pushPlain(out, text);
        afterDef = ident === "def";
        continue;
      }
      afterDef = false;
    }
    pushPlain(out, String(src).slice(at));
    return out;
  }

  const JSON_RE = new RegExp([
    /("(?:[^"\\\n]|\\.)*"?)(\s*:)?/.source,                     // 1 string, 2 trailing colon → key
    /(-?\b\d+(?:\.\d+)?(?:[eE][+-]?\d+)?\b)/.source,           // 3 number
    /\b(true|false|null)\b/.source,                             // 4 literal
    /([{}[\],:])/.source,                                       // 5 punctuation
  ].join("|"), "g");

  function json(src) {
    const out = [];
    let at = 0;
    src = String(src);
    for (const m of src.matchAll(JSON_RE)) {
      pushPlain(out, src.slice(at, m.index));
      at = m.index + m[0].length;
      const [, string, colon, number, literal, punct] = m;
      if (string !== undefined) {
        out.push([colon ? "hl-key" : "hl-str", string]);
        if (colon) out.push(["hl-punct", colon]);
      } else if (number !== undefined) out.push(["hl-num", number]);
      else if (literal !== undefined) out.push(["hl-const", literal]);
      else out.push(["hl-punct", punct]);
    }
    pushPlain(out, src.slice(at));
    return out;
  }

  // One line of the live execution trace, as app.js writes it:
  //   "001  block rotate  [abc]"
  //   "002    ↳ SimDrone.rotate(\"cw\", 30) → null"
  // The arguments and result are JSON, so they reuse the JSON colouring.
  const TRACE_BLOCK_RE = /^(\d+)(\s+)(block)(\s+)(\S+)(\s+)(\[[^\]]*\])(.*)$/s;
  const TRACE_CALL_RE = /^(\d+)(\s+↳\s+)([\w$]+)(\.)([\w$]+)(\()(.*)(\))(?:(\s*→\s*)(.*))?$/s;
  //   "003  finished: done"   (or "finished: error (the drone did not answer …)")
  const TRACE_END_RE = /^(\d+)(\s+)(finished)(:\s*)(\w+)(.*)$/s;
  const END_TONES = { done: "tone-ok", stopped: "tone-warn", error: "tone-error" };

  function trace(line) {
    line = String(line);
    let m = line.match(TRACE_BLOCK_RE);
    if (m) {
      const [, seq, s1, word, s2, op, s3, id, rest] = m;
      const out = [["hl-ln", seq], [null, s1], ["hl-kw", word], [null, s2],
        ["hl-fn", op], [null, s3], ["hl-com", id]];
      pushPlain(out, rest);
      return out;
    }
    m = line.match(TRACE_CALL_RE);
    if (m) {
      const [, seq, arrow, adapter, dot, method, open, args, close, resArrow, result] = m;
      const out = [["hl-ln", seq], ["hl-punct", arrow], ["hl-obj", adapter],
        ["hl-punct", dot], ["hl-fn", method], ["hl-punct", open], ...json(args),
        ["hl-punct", close]];
      if (resArrow !== undefined) out.push(["hl-punct", resArrow], ...json(result || ""));
      return out;
    }
    m = line.match(TRACE_END_RE);
    if (m) {
      const [, seq, s1, word, colon, reason, rest] = m;
      const out = [["hl-ln", seq], [null, s1], ["hl-kw", word], ["hl-punct", colon],
        [END_TONES[reason] || null, reason]];
      pushPlain(out, rest);
      return out;
    }
    return [[null, line]];
  }

  // The console speaks to students, not programmers, so it is coloured by what a
  // line *means* rather than by syntax: the leading symbol every message already
  // starts with picks the tone of the whole line, and only two things inside it
  // are picked out — block names in “curly quotes”, and measured numbers.
  const CONSOLE_TONES = [
    [/^\s*(⛔|💥|✖)/u, "tone-error"],
    [/^\s*⚠/u, "tone-warn"],
    [/^\s*💡/u, "tone-hint"],
    [/^\s*(🏆|🎯|✅|🎉)/u, "tone-ok"],
    [/^\s*(▶|↺|🔗|ℹ)/u, "tone-info"],
    // "mission <reason>": the interpreter's three ways a run ends.
    [/^mission done\b/, "tone-ok"],
    [/^mission error\b/, "tone-error"],
    [/^mission stopped\b/, "tone-warn"],
  ];

  function consoleTone(line) {
    for (const [re, tone] of CONSOLE_TONES) if (re.test(line)) return tone;
    return null;
  }

  const CONSOLE_RE = new RegExp([
    /(“[^”\n]*”)/.source,                                      // 1 a block's name, as the app quotes it
    // 2 a number, with its unit. Fenced on both sides by anything word-ish or a
    // dot, so neither a version (0.3.1) nor a port in a name (udp11111) lights up.
    /((?<![\w.])-?\d+(?:\.\d+)?(?:\s?(?:cm|m|s|%|°))?(?![\w.]))/.source,
  ].join("|"), "gu");

  function consoleLine(line) {
    const out = [];
    let at = 0;
    line = String(line);
    for (const m of line.matchAll(CONSOLE_RE)) {
      pushPlain(out, line.slice(at, m.index));
      at = m.index + m[0].length;
      out.push([m[1] ? "hl-quote" : "hl-num", m[0]]);
    }
    pushPlain(out, line.slice(at));
    return out;
  }

  // --- DOM ---------------------------------------------------------------------

  function fragment(doc, tokens) {
    const frag = doc.createDocumentFragment();
    for (const [cls, text] of tokens) {
      if (!cls) {
        frag.append(doc.createTextNode(text));
        continue;
      }
      const span = doc.createElement("span");
      span.className = cls;
      span.textContent = text;
      frag.append(span);
    }
    return frag;
  }

  /** Replace `el`'s contents with `tokens`, coloured. */
  function render(el, tokens) {
    el.replaceChildren(fragment(el.ownerDocument, tokens));
  }

  /**
   * Append one coloured line (plus its newline) to `el`, a <pre>.
   *
   * Appending rather than re-rendering keeps a long session's console and trace
   * cheap: earlier lines are never touched again.
   */
  function appendLine(el, tokens, lineClass = null) {
    const doc = el.ownerDocument;
    const line = doc.createElement("span");
    line.className = lineClass ? `hl-line ${lineClass}` : "hl-line";
    line.append(fragment(doc, tokens), doc.createTextNode("\n"));
    el.append(line);
    return line;
  }

  const COMP1_HIGHLIGHT = { python, json, trace, consoleLine, consoleTone, render, appendLine };

  if (typeof window !== "undefined") window.COMP1_HIGHLIGHT = COMP1_HIGHLIGHT;
  if (typeof module !== "undefined") module.exports = COMP1_HIGHLIGHT;
})();
