// @clep/sdk — vanilla browser SDK (no framework required).
// One import instruments the whole app; data-clep attributes mark features.
//
//   <script src="clep.js" data-clep></script>
//   <!-- or --> import "@clep/sdk";
//
//   <button data-clep="ai-research" data-clep-action="primary">
//     Start Research
//   </button>
//   <div data-clep="ai-research" data-clep-state="result"> ... </div>
//
// What it captures per feature:
//   name -> DOM subtree rect (normalized) -> interactions -> state changes
// Export with window.__CLEP__.export() or auto-POST to registryUrl.
//
// No deps. <2kb gz. Safe to leave in production (observer is passive).

(function () {
  if (typeof window === "undefined") return;
  if (window.__CLEP__) return;

  var REGISTRY = {}; // name -> feature record
  var OPTS = {
    registryUrl: null, // e.g. "http://localhost:8787/features" — POST on change (debounced)
    captureStates: true,
    captureInteractions: true,
    debug: false,
  };

  try {
    var s = document.currentScript;
    if (s) {
      if (s.getAttribute("data-registry")) OPTS.registryUrl = s.getAttribute("data-registry");
      if (s.hasAttribute("data-debug")) OPTS.debug = true;
    }
  } catch (e) {}

  function log() {
    if (OPTS.debug && console && console.log) console.log.apply(console, ["[clep]"].concat([].slice.call(arguments)));
  }

  function normRect(el) {
    var r = el.getBoundingClientRect();
    var vw = Math.max(window.innerWidth || 1, 1);
    var vh = Math.max(window.innerHeight || 1, 1);
    return {
      x: +(r.left / vw).toFixed(4),
      y: +(r.top / vh).toFixed(4),
      w: +(r.width / vw).toFixed(4),
      h: +(r.height / vh).toFixed(4),
    };
  }

  function componentPath(el) {
    // Cheap component tree: walk up 4 levels collecting tag + data-clep/state/action.
    var path = [];
    var cur = el;
    for (var i = 0; i < 5 && cur && cur !== document.body; i++) {
      var seg = (cur.tagName || "?").toLowerCase();
      if (cur.getAttribute) {
        if (cur.getAttribute("data-clep-state")) seg += "[state=" + cur.getAttribute("data-clep-state") + "]";
        if (cur.getAttribute("data-clep-action")) seg += "[action=" + cur.getAttribute("data-clep-action") + "]";
        var cls = (cur.className && cur.className.baseVal !== undefined ? "" : cur.className) || "";
        if (typeof cls === "string" && cls) seg += "." + cls.trim().split(/\s+/).slice(0, 2).join(".");
      }
      path.unshift(seg);
      cur = cur.parentElement;
    }
    return path;
  }

  function ensureFeature(name) {
    if (!REGISTRY[name]) {
      REGISTRY[name] = {
        name: name,
        title: name.replace(/[-_]+/g, " ").replace(/\b\w/g, function (c) { return c.toUpperCase(); }),
        url: location.href,
        viewport: { w: window.innerWidth, h: window.innerHeight },
        element: null,
        component: [],
        interactions: [],
        states: [],
        _els: [],
      };
    }
    return REGISTRY[name];
  }

  function observeElement(el) {
    var name = el.getAttribute("data-clep");
    if (!name) return;
    var feat = ensureFeature(name);
    if (feat._els.indexOf(el) === -1) feat._els.push(el);
    // Union rect of all nodes sharing the name (feature may span multiple nodes).
    var rects = feat._els.map(function (e) {
      try { return e.getBoundingClientRect(); } catch (err) { return null; }
    }).filter(Boolean);
    if (rects.length) {
      var l = Math.min.apply(null, rects.map(function (r) { return r.left; }));
      var t = Math.min.apply(null, rects.map(function (r) { return r.top; }));
      var r2 = Math.max.apply(null, rects.map(function (r) { return r.right; }));
      var b = Math.max.apply(null, rects.map(function (r) { return r.bottom; }));
      var vw = Math.max(window.innerWidth || 1, 1), vh = Math.max(window.innerHeight || 1, 1);
      feat.element = {
        x: +Math.max(l, 0).toFixed(1) && +(Math.max(l, 0) / vw).toFixed(4),
        y: +((Math.max(t, 0) / vh).toFixed(4)),
        w: +((Math.min(r2, vw) - Math.max(l, 0)) / vw).toFixed(4),
        h: +((Math.min(b, vh) - Math.max(t, 0)) / vh).toFixed(4),
      };
      feat.viewport = { w: window.innerWidth, h: window.innerHeight };
      feat.component = componentPath(el);
    }
    var st = el.getAttribute("data-clep-state");
    if (st && OPTS.captureStates && feat.states.indexOf(st) === -1) {
      feat.states.push(st);
      pushInteraction(name, { type: "state", state: st, at: Date.now() });
    }
    scheduleSync(name);
  }

  function pushInteraction(name, ev) {
    var feat = ensureFeature(name);
    if (!OPTS.captureInteractions && ev.type !== "state") return;
    // Cap at 50 to stay small.
    if (feat.interactions.length < 50) feat.interactions.push(ev);
    scheduleSync(name);
  }

  var syncTimer = null;
  function scheduleSync(name) {
    if (!OPTS.registryUrl) return;
    if (syncTimer) clearTimeout(syncTimer);
    syncTimer = setTimeout(function () { syncFeature(name); }, 800);
  }

  function syncFeature(name) {
    if (!OPTS.registryUrl) return;
    try {
      var payload = exportFeature(name);
      fetch(OPTS.registryUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      }).catch(function () {});
    } catch (e) {}
  }

  function exportFeature(name) {
    var f = REGISTRY[name];
    if (!f) return null;
    var out = {};
    for (var k in f) if (k.charAt(0) !== "_") out[k] = f[k];
    return out;
  }

  function exportAll() {
    var all = {};
    Object.keys(REGISTRY).forEach(function (k) { all[k] = exportFeature(k); });
    return all;
  }

  function scan() {
    var nodes = document.querySelectorAll("[data-clep]");
    for (var i = 0; i < nodes.length; i++) observeElement(nodes[i]);
    log("scan: " + nodes.length + " node(s), " + Object.keys(REGISTRY).length + " feature(s)");
  }

  // Watch for SPA renders / dynamic DOM.
  var mo = null;
  if (typeof MutationObserver !== "undefined") {
    mo = new MutationObserver(function (muts) {
      var dirty = false;
      muts.forEach(function (m) {
        if (m.type === "attributes" && m.attributeName && m.attributeName.indexOf("data-clep") === 0) dirty = true;
        (m.addedNodes || []).forEach(function (n) {
          if (n.nodeType === 1 && (n.hasAttribute("data-clep") || (n.querySelector && n.querySelector("[data-clep]")))) dirty = true;
        });
      });
      if (dirty) scan();
    });
  }

  // Interaction capture: click / input / submit inside a [data-clep] subtree.
  document.addEventListener("click", function (e) {
    var t = e.target && e.target.closest ? e.target.closest("[data-clep]") : null;
    if (!t) return;
    var name = t.getAttribute("data-clep");
    var actionEl = e.target && e.target.closest ? e.target.closest("[data-clep-action]") : null;
    pushInteraction(name, {
      type: "click",
      target: actionEl ? actionEl.getAttribute("data-clep-action") : (e.target.tagName || "").toLowerCase(),
      text: (e.target.innerText || "").trim().slice(0, 60),
      at: Date.now(),
    });
  }, true);

  document.addEventListener("input", function (e) {
    var t = e.target && e.target.closest ? e.target.closest("[data-clep]") : null;
    if (!t) return;
    pushInteraction(t.getAttribute("data-clep"), {
      type: "input",
      target: (e.target.name || e.target.placeholder || e.target.tagName || "").toLowerCase().slice(0, 40),
      at: Date.now(),
    });
  }, true);

  document.addEventListener("submit", function (e) {
    var t = e.target && e.target.closest ? e.target.closest("[data-clep]") : null;
    if (!t) return;
    pushInteraction(t.getAttribute("data-clep"), { type: "submit", at: Date.now() });
  }, true);

  // Network / loading heuristic: XHR+fetch inside clip window marks loading states.
  // (Coarse — the renderer only needs click → input → submit → loading → result order.)
  var origFetch = window.fetch;
  if (origFetch) {
    window.fetch = function () {
      var stack = document.querySelector("[data-clep]:hover");
      var args = arguments;
      var p = origFetch.apply(this, args);
      if (stack) {
        var name = stack.getAttribute("data-clep");
        pushInteraction(name, { type: "loading", at: Date.now() });
        p.then(function () { pushInteraction(name, { type: "result", at: Date.now() }); }, function () {});
      }
      return p;
    };
  }

  window.__CLEP__ = {
    scan: scan,
    features: function () { return Object.keys(REGISTRY); },
    export: exportFeature,
    exportAll: exportAll,
    configure: function (o) { for (var k in o) OPTS[k] = o[k]; },
    download: function (name) {
      var data = name ? exportFeature(name) : exportAll();
      var blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
      var a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = (name || "clep-features") + ".feature.json";
      a.click();
    },
  };

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", scan);
  else scan();
  if (mo) mo.observe(document.documentElement, { childList: true, subtree: true, attributes: true, attributeFilter: ["data-clep", "data-clep-state", "data-clep-action"] });
  log("instrumented. mark features with data-clep=\"name\".");
})();
