/* Lightweight terminal animation for the hero. No deps. ~50 lines.
 *
 * Cycles through three frames showing the canonical Tool Pouch flow:
 *   1. wrap_anthropic capture
 *   2. pouch traces
 *   3. pouch replay --repeat 100
 *
 * Pause on hover. Reduced motion respected. Total payload < 1KB gzipped.
 */
(function () {
  const el = document.getElementById("terminal-body");
  if (!el) return;

  const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  const frames = [
    [
      { c: "prompt", t: "$ " },
      { c: "",       t: "python app.py\n" },
      { c: "out",    t: "[pouch] capturing every messages.create → ~/.tool_pouch/tool_pouch.db\n" },
      { c: "out",    t: "Listening on :8000\n" },
    ],
    [
      { c: "prompt", t: "$ " },
      { c: "",       t: "pouch traces --since 1h --failed\n" },
      { c: "out",    t: "TRACE      WHEN     AGENT          OUTCOME    REQUEST_ID\n" },
      { c: "bad",    t: "f3a91c7d   2m ago   support_bot    crashed    req-7c2a9\n" },
      { c: "bad",    t: "9b41208e   14m ago  support_bot    looped     req-44a01\n" },
      { c: "out",    t: "ok   8472f0c1   28m ago  support_bot    completed  req-22cd7\n" },
    ],
    [
      { c: "prompt", t: "$ " },
      { c: "",       t: "pouch replay f3a91c7d --repeat 100\n" },
      { c: "out",    t: "Replaying f3a91c7d in chaos mode × 100...\n" },
      { c: "out",    t: "\nFailure rates across replays:\n\n" },
      { c: "bad",    t: "  search   × timeout         87% crashed, 13% handled\n" },
      { c: "bad",    t: "  search   × malformed_json  62% looped, 38% handled\n" },
      { c: "ok",     t: "  search   × empty_result    100% handled\n" },
    ],
  ];

  let frameIndex = 0;
  let charIndex = 0;
  let segIndex = 0;
  let paused = false;

  function render() {
    const frame = frames[frameIndex];
    let html = "";
    for (let i = 0; i <= segIndex && i < frame.length; i++) {
      const seg = frame[i];
      const limit = i === segIndex ? charIndex : seg.t.length;
      const text = seg.t.slice(0, limit)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;");
      if (seg.c) {
        html += `<span class="${seg.c}">${text}</span>`;
      } else {
        html += text;
      }
    }
    html += '<span class="cursor">▎</span>';
    el.innerHTML = html;
  }

  function tick() {
    if (paused) return setTimeout(tick, 200);
    const frame = frames[frameIndex];
    if (segIndex >= frame.length) {
      // Hold the finished frame, then advance.
      setTimeout(() => {
        frameIndex = (frameIndex + 1) % frames.length;
        charIndex = 0;
        segIndex = 0;
        render();
        tick();
      }, 1800);
      return;
    }
    const seg = frame[segIndex];
    if (charIndex >= seg.t.length) {
      segIndex += 1;
      charIndex = 0;
    } else {
      charIndex += 1;
    }
    render();
    setTimeout(tick, reduce ? 0 : 18);
  }

  el.addEventListener("mouseenter", () => { paused = true; });
  el.addEventListener("mouseleave", () => { paused = false; });

  if (reduce) {
    // Show the final frame statically.
    frameIndex = frames.length - 1;
    segIndex = frames[frameIndex].length;
    charIndex = 0;
    render();
  } else {
    tick();
  }
})();
