/* PIE module picker: choose one module, several, or all of them.
   Used by Ask PIE, Feature Catalog, Field Explorer and Relationships.

   The selection is kept in the page address (?modules=ess,inventory), so a copied link
   opens the page with the same modules chosen. An empty selection means all modules.

     const picker = PieModules.create(document.getElementById("modules"), {
       options: [{value: "ess", label: "ESS", note: "verified"}, ...],
       selected: PieModules.fromUrl() || [],
       onChange: selected => { ... },
     });
*/
(function () {
  "use strict";
  const esc = v => String(v == null ? "" : v).replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));

  const CSS = `
.pmp{position:relative;display:inline-block}
.pmp-btn{display:inline-flex;align-items:center;gap:6px;max-width:320px;padding:7px 11px;border:1px solid var(--border);border-radius:8px;background:var(--surface-2);color:var(--text);cursor:pointer;font:inherit;white-space:nowrap}
.pmp-btn:hover{border-color:var(--accent)}
.pmp-btn .pmp-label{overflow:hidden;text-overflow:ellipsis}
.pmp-btn .pmp-n{font-size:11px;font-weight:700;background:var(--accent);color:#fff;border-radius:999px;padding:0 6px}
.pmp-pop{position:absolute;z-index:40;top:calc(100% + 6px);left:0;width:300px;max-width:90vw;background:var(--surface);color:var(--text);border:1px solid var(--border);border-radius:10px;box-shadow:0 10px 30px rgba(0,0,0,.18);padding:10px}
.pmp-pop[hidden]{display:none}
.pmp-q{width:100%;padding:6px 9px;border:1px solid var(--border);border-radius:7px;background:var(--surface-2);color:var(--text);font:inherit;margin-bottom:8px}
.pmp-actions{display:flex;gap:6px;margin-bottom:6px}
.pmp-actions button,.pmp-foot button{font:inherit;font-size:12px;padding:4px 9px;border:1px solid var(--border);border-radius:7px;background:var(--surface);color:var(--text);cursor:pointer}
.pmp-actions button:hover,.pmp-foot button:hover{background:var(--surface-2)}
.pmp-list{max-height:280px;overflow:auto;border-top:1px solid var(--border);border-bottom:1px solid var(--border);padding:4px 0}
.pmp-list label{display:flex;align-items:center;gap:8px;padding:4px 6px;border-radius:6px;cursor:pointer;font-size:13px}
.pmp-list label:hover{background:var(--surface-2)}
.pmp-list small{margin-left:auto;color:var(--muted);font-size:11px}
.pmp-list .pmp-empty{color:var(--muted);font-size:12px;padding:6px}
.pmp-foot{display:flex;align-items:center;gap:6px;margin-top:8px;font-size:12px;color:var(--muted)}
.pmp-foot .pmp-sum{margin-right:auto}
.pmp-foot button.pmp-done{background:var(--accent);border-color:var(--accent);color:#fff}
`;

  function injectCss() {
    if (document.getElementById("pmp-css")) return;
    const s = document.createElement("style");
    s.id = "pmp-css";
    s.textContent = CSS;
    document.head.appendChild(s);
  }

  /** Modules named in the address (?modules=a,b or the older ?module=a); null when absent. */
  function fromUrl() {
    const p = new URLSearchParams(location.search);
    const v = p.has("modules") ? p.get("modules") : p.get("module");
    return v == null ? null : [...new Set(v.split(",").map(s => s.trim().toLowerCase()).filter(Boolean))];
  }

  function query(selected, keep) {
    const p = new URLSearchParams(keep || "");
    p.delete("module");
    p.delete("modules");
    let q = p.toString();
    if (selected.length) q = (q ? q + "&" : "") + "modules=" + selected.map(encodeURIComponent).join(",");
    return q ? "?" + q : "";
  }

  /** Put the selection in the address bar (other parameters are kept). */
  function toUrl(selected) {
    history.replaceState(history.state, "", location.pathname + query(selected, location.search) + location.hash);
  }

  /** A link to `page` (default: this page) that opens with these modules chosen. */
  function link(selected, page) {
    const u = new URL(page || location.pathname, location.href);
    return u.origin + u.pathname + query(selected);
  }

  function labelFor(selected, options, allLabel) {
    if (!selected.length) return allLabel;
    const name = v => (options.find(o => o.value === v) || {label: v}).label;
    return selected.length <= 2 ? selected.map(name).join(" + ") : `${name(selected[0])} + ${selected.length - 1} more`;
  }

  async function copyText(text) {
    try { await navigator.clipboard.writeText(text); return true; }
    catch (e) {
      const ta = document.createElement("textarea");
      ta.value = text; document.body.appendChild(ta); ta.select();
      let ok = false; try { ok = document.execCommand("copy"); } catch (_) {}
      ta.remove(); return ok;
    }
  }

  function create(host, opts) {
    injectCss();
    const o = Object.assign({options: [], selected: [], allLabel: "All modules", title: "Modules", syncUrl: true,
                             shareable: true, onChange: () => {}}, opts || {});
    let options = o.options.slice();
    let selected = [...new Set((o.selected || []).map(String))];
    host.classList.add("pmp");
    host.innerHTML = `<button type="button" class="pmp-btn" aria-haspopup="dialog" aria-expanded="false" title="${esc(o.title)}: choose one, several or all">
        <span class="pmp-label"></span><span class="pmp-n" hidden></span><span aria-hidden="true">▾</span></button>
      <div class="pmp-pop" role="dialog" aria-label="${esc(o.title)}" hidden>
        <input type="search" class="pmp-q" placeholder="Find a module…" aria-label="Find a module">
        <div class="pmp-actions"><button type="button" data-a="all">${esc(o.allLabel)}</button><button type="button" data-a="none">Clear</button></div>
        <div class="pmp-list"></div>
        <div class="pmp-foot"><span class="pmp-sum"></span>${o.shareable ? '<button type="button" data-a="copy" title="Copy a link that opens this page with these modules">Copy link</button>' : ""}<button type="button" class="pmp-done" data-a="done">Done</button></div>
      </div>`;
    const btn = host.querySelector(".pmp-btn"), pop = host.querySelector(".pmp-pop"), list = host.querySelector(".pmp-list"),
          q = host.querySelector(".pmp-q"), sum = host.querySelector(".pmp-sum");

    function paint() {
      host.querySelector(".pmp-label").textContent = labelFor(selected, options, o.allLabel);
      const n = host.querySelector(".pmp-n");
      n.hidden = selected.length < 2; n.textContent = selected.length;
      sum.textContent = selected.length ? `${selected.length} of ${options.length} selected` : `all ${options.length} modules`;
      const term = q.value.trim().toLowerCase();
      const shown = options.filter(x => !term || x.label.toLowerCase().includes(term) || x.value.includes(term));
      list.innerHTML = shown.map(x => `<label><input type="checkbox" value="${esc(x.value)}"${selected.includes(x.value) ? " checked" : ""}>
        <span>${esc(x.label)}</span>${x.note ? `<small>${esc(x.note)}</small>` : ""}</label>`).join("") ||
        '<div class="pmp-empty">No module matches.</div>';
      q.hidden = options.length <= 8;
    }
    function changed() {
      paint();
      if (o.syncUrl) toUrl(selected);
      o.onChange(selected.slice());
    }
    function open(show) {
      pop.hidden = !show;
      btn.setAttribute("aria-expanded", String(show));
      if (show) { q.value = ""; paint(); (q.hidden ? list.querySelector("input") || btn : q).focus(); }
    }

    btn.addEventListener("click", () => open(pop.hidden));
    q.addEventListener("input", paint);
    list.addEventListener("change", e => {
      const v = e.target.value;
      selected = e.target.checked ? [...selected, v] : selected.filter(x => x !== v);
      // Keep the order of the option list, so links and labels are stable.
      selected = options.map(x => x.value).filter(x => selected.includes(x)).concat(selected.filter(x => !options.some(y => y.value === x)));
      changed();
    });
    pop.addEventListener("click", async e => {
      const a = e.target.closest("[data-a]"); if (!a) return;
      if (a.dataset.a === "all" || a.dataset.a === "none") { selected = []; changed(); }
      else if (a.dataset.a === "done") { open(false); btn.focus(); }
      else if (a.dataset.a === "copy") {
        const ok = await copyText(link(selected));
        const was = a.textContent; a.textContent = ok ? "Link copied" : "Copy failed";
        setTimeout(() => { a.textContent = was; }, 1500);
      }
    });
    document.addEventListener("click", e => { if (!pop.hidden && !host.contains(e.target)) open(false); });
    host.addEventListener("keydown", e => { if (e.key === "Escape" && !pop.hidden) { open(false); btn.focus(); } });

    paint();
    if (o.syncUrl) toUrl(selected);
    return {
      get: () => selected.slice(),
      set(values, silent) { selected = [...new Set((values || []).map(String))]; silent ? paint() : changed(); },
      setOptions(list2) {
        options = list2.slice();
        // Drop choices that no longer exist, once the real module list is known.
        const known = new Set(options.map(x => x.value));
        const kept = selected.filter(v => known.has(v));
        if (kept.length !== selected.length) { selected = kept; if (o.syncUrl) toUrl(selected); }
        paint();
      },
    };
  }

  window.PieModules = {create, fromUrl, toUrl, link};
})();
