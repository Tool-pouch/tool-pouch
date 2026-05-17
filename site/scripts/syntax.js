/* Minimal syntax highlighting for Python + Bash. No deps, ~2KB.
 *
 * Auto-detects language from content. Skips the hero terminal (which
 * has its own custom span renderer in terminal.js). Runs once on DOM
 * ready, no MutationObserver — the site is static.
 */
(function () {
  const PY_KEYWORDS = new Set([
    "import", "from", "as", "def", "class", "return", "if", "else",
    "elif", "for", "while", "try", "except", "finally", "with",
    "lambda", "pass", "yield", "raise", "True", "False", "None",
    "and", "or", "not", "in", "is", "await", "async", "global",
    "nonlocal", "break", "continue",
  ]);

  // Built-in types — colored as `tk-type` like Dark Modern. PascalCase
  // identifiers (Anthropic, OpenAI, MyClass) also fall into this bucket
  // via the regex check below.
  const PY_TYPES = new Set([
    "str", "int", "float", "bool", "bytes", "dict", "list", "tuple",
    "set", "frozenset", "type", "object", "complex", "range",
    "Any", "Optional", "Callable", "Iterable", "Iterator", "Union",
    "Tuple", "List", "Dict", "Set", "Mapping", "Sequence", "Type",
  ]);
  const PASCAL_CASE = /^[A-Z][a-zA-Z0-9]*$/;

  function esc(s) {
    return s.replace(/&/g, "&amp;").replace(/</g, "&lt;");
  }

  function span(cls, text) {
    return '<span class="' + cls + '">' + esc(text) + "</span>";
  }

  function highlightPython(code) {
    let i = 0, out = "";
    while (i < code.length) {
      const c = code[i];

      if (c === "#") {
        let j = code.indexOf("\n", i);
        if (j < 0) j = code.length;
        out += span("tk-comment", code.slice(i, j));
        i = j;
        continue;
      }

      if (c === '"' || c === "'") {
        const quote = c;
        let j = i + 1;
        while (j < code.length && code[j] !== quote) {
          if (code[j] === "\\") j += 2; else j++;
        }
        j++;
        out += span("tk-string", code.slice(i, Math.min(j, code.length)));
        i = j;
        continue;
      }

      if (c === "@" && /[a-zA-Z_]/.test(code[i + 1] || "")) {
        let j = i + 1;
        while (j < code.length && /[a-zA-Z0-9_.]/.test(code[j])) j++;
        out += span("tk-decorator", code.slice(i, j));
        i = j;
        continue;
      }

      if (/[0-9]/.test(c)) {
        let j = i;
        while (j < code.length && /[0-9._]/.test(code[j])) j++;
        out += span("tk-number", code.slice(i, j));
        i = j;
        continue;
      }

      if (/[a-zA-Z_]/.test(c)) {
        let j = i;
        while (j < code.length && /[a-zA-Z0-9_]/.test(code[j])) j++;
        const word = code.slice(i, j);
        if (PY_KEYWORDS.has(word)) {
          out += span("tk-keyword", word);
        } else if (PY_TYPES.has(word) || PASCAL_CASE.test(word)) {
          // Dark Modern colors classes/types with their own teal token.
          // Class constructors like `Anthropic()` go here, not into `tk-fn`.
          out += span("tk-type", word);
        } else if (code[j] === "(") {
          out += span("tk-fn", word);
        } else {
          out += esc(word);
        }
        i = j;
        continue;
      }

      out += esc(c);
      i++;
    }
    return out;
  }

  function highlightBashLine(line) {
    let out = "", rest = line;
    const promptMatch = rest.match(/^(\s*\$\s+)/);
    if (promptMatch) {
      out += span("tk-prompt", promptMatch[1]);
      rest = rest.slice(promptMatch[1].length);
    } else if (/^\s*#/.test(rest)) {
      return span("tk-comment", rest);
    }

    let first = true;
    let i = 0;
    while (i < rest.length) {
      const c = rest[i];

      if (/\s/.test(c)) {
        let j = i;
        while (j < rest.length && /\s/.test(rest[j])) j++;
        out += esc(rest.slice(i, j));
        i = j;
        continue;
      }

      if (c === "#") {
        out += span("tk-comment", rest.slice(i));
        return out;
      }

      if (c === '"' || c === "'") {
        const quote = c;
        let j = i + 1;
        while (j < rest.length && rest[j] !== quote) {
          if (rest[j] === "\\") j += 2; else j++;
        }
        j++;
        out += span("tk-string", rest.slice(i, Math.min(j, rest.length)));
        i = j;
        first = false;
        continue;
      }

      let j = i;
      while (j < rest.length && !/\s/.test(rest[j])) j++;
      const token = rest.slice(i, j);
      if (token.startsWith("--") || (token.startsWith("-") && token.length > 1 && !/^-?\d/.test(token))) {
        out += span("tk-flag", token);
      } else if (first) {
        out += span("tk-fn", token);
      } else {
        out += esc(token);
      }
      first = false;
      i = j;
    }
    return out;
  }

  function highlightBash(code) {
    return code.split("\n").map(highlightBashLine).join("\n");
  }

  function detect(code) {
    const t = code.trim();
    if (/^\s*\$\s/m.test(code)) return "bash";
    if (/^(import|from|def|class|@\w)/m.test(t)) return "python";
    if (/\bclient\s*=|\bself\.|->\s*\w|\bawait\s/.test(t)) return "python";
    if (/^pouch\s+\w/.test(t) || /^pip\s+/.test(t)) return "bash";
    return null;
  }

  function go() {
    document.querySelectorAll("pre > code").forEach(function (codeEl) {
      if (codeEl.closest(".terminal-body")) return;
      if (codeEl.dataset.tkDone === "1") return;
      const raw = codeEl.textContent;
      const lang = detect(raw);
      if (lang === "python") codeEl.innerHTML = highlightPython(raw);
      else if (lang === "bash") codeEl.innerHTML = highlightBash(raw);
      else return;
      codeEl.dataset.tkDone = "1";
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", go);
  } else {
    go();
  }
})();
