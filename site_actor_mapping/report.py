"""Self-contained HTML report for browsing organisations and their contexts."""

from __future__ import annotations

import html
import json
from collections import defaultdict
from pathlib import Path
from typing import Dict, List

MAX_CONTEXTS_PER_ORG = 300


def write_report(path: Path, orgs: List[dict], mentions: List[dict], themes, theme_pages,
                 n_pages: int, start_urls: List[str], sites: List[str] = ()) -> None:
    ctx: Dict[str, List[dict]] = defaultdict(list)
    org_sites: Dict[str, set] = defaultdict(set)
    org_roles: Dict[str, set] = defaultdict(set)
    for r in mentions:
        org_sites[r["org_key"]].add(r.get("site", ""))
        org_roles[r["org_key"]].add(r.get("site_role", ""))
        if r["repeat_context"] or len(ctx[r["org_key"]]) >= MAX_CONTEXTS_PER_ORG:
            continue
        ctx[r["org_key"]].append({
            "s": r["surface_form"], "c": r["context"], "u": r["url"], "t": r["page_title"],
            "d": r["page_date"], "m": r["methods"], "b": r["boilerplate"], "g": r["region"], "e": r.get("embedded_in", ""),
            "st": r.get("site", ""), "sr": r.get("site_role", ""), "ve": r.get("via_event", ""),
            "cm": r.get("calendar_month", ""),
            "th": r["paragraph_themes"], "tt": r["context_theme_terms"], "co": r["co_mentioned"],
        })
    data = {
        "orgs": [{k: o[k] for k in ("organisation", "org_key", "org_type", "confidence",
                                    "distinct_mentions", "substantive_mentions", "pages",
                                    "boilerplate_only", "methods", "variants", "acronyms",
                                    "themes", "linked_domains")}
                 | {"sites": sorted(org_sites[o["org_key"]]), "roles": sorted(org_roles[o["org_key"]])}
                 for o in orgs],
        "siteList": list(sites),
        "ctx": ctx,
        "themes": [{"name": t, "pages": theme_pages.get(t, 0)} for t in themes.names],
        "nPages": n_pages,
    }
    title = "Actor mapping: " + ", ".join(start_urls)
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    path.write_text(TEMPLATE.replace("{{TITLE}}", html.escape(title))
                    .replace("{{DATA}}", payload), encoding="utf-8")


TEMPLATE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{TITLE}}</title>
<style>
:root{--bg:#fafaf8;--fg:#1d1d1b;--muted:#6b6b66;--line:#e2e1dc;--card:#fff;--accent:#2f5d8a;
--mark:#ffe58a;--theme:#cfe6d4;--chip:#eeede8}
@media (prefers-color-scheme:dark){:root{--bg:#18181a;--fg:#e8e8e4;--muted:#9a9a94;--line:#333336;
--card:#212124;--accent:#8db7e0;--mark:#6b5a12;--theme:#24513a;--chip:#2c2c30}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);
font:14px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
header{padding:20px 24px 8px}h1{font-size:18px;margin:0 0 4px}.sub{color:var(--muted)}
.wrap{display:grid;grid-template-columns:minmax(320px,440px) 1fr;gap:16px;padding:12px 24px 24px}
@media (max-width:900px){.wrap{grid-template-columns:1fr;padding:12px 16px}header{padding:16px 16px 4px}}
.panel{background:var(--card);border:1px solid var(--line);border-radius:8px}
.filters{display:flex;flex-wrap:wrap;gap:8px;padding:12px;border-bottom:1px solid var(--line)}
.filters input[type=search]{flex:1 1 100%;padding:7px 9px;border:1px solid var(--line);border-radius:6px;
background:var(--bg);color:var(--fg)}
select,input[type=number]{padding:5px;border:1px solid var(--line);border-radius:6px;background:var(--bg);color:var(--fg)}
label{color:var(--muted);font-size:12px;display:flex;align-items:center;gap:4px}
#list{max-height:calc(100vh - 170px);overflow:auto}
.org{padding:8px 12px;border-bottom:1px solid var(--line);cursor:pointer}
.org:hover{background:var(--chip)}.org.sel{background:var(--chip);box-shadow:inset 3px 0 var(--accent)}
.org .n{float:right;color:var(--muted);font-variant-numeric:tabular-nums}
.meta{color:var(--muted);font-size:12px}
.chip{display:inline-block;background:var(--chip);border-radius:10px;padding:0 7px;margin:2px 4px 0 0;font-size:12px}
.conf-high{color:#2e7d4f}.conf-medium{color:#b07a12}.conf-low{color:#a33}
#detail{padding:16px 20px;max-height:calc(100vh - 110px);overflow:auto}
#detail h2{margin:0 0 6px;font-size:17px}
.ctx{border-top:1px solid var(--line);padding:10px 0}
.ctx p{margin:4px 0}.ctx a{color:var(--accent);word-break:break-all}
mark{background:var(--mark);color:inherit;padding:0 1px}
mark.t{background:var(--theme)}
table{border-collapse:collapse;font-size:13px}td,th{padding:3px 10px 3px 0;text-align:left}
.empty{color:var(--muted);padding:20px}
</style></head><body>
<header><h1>{{TITLE}}</h1><div class="sub" id="stats"></div>
<div class="sub">This report lists every organisation found. The theme filter (inclusion criterion) applies only to master_network, so it may hold fewer.</div></header>
<div class="wrap">
 <div class="panel">
  <div class="filters">
   <input type="search" id="q" placeholder="Filter by name, variant or domain">
   <label>Theme <select id="theme"><option value="">any</option></select></label>
   <label>Site <select id="site"><option value="">all</option><option value="@main">main site</option>
     <option value="@linked">linked sites</option></select></label>
   <label>Type <select id="type"><option value="">any</option></select></label>
   <label>Confidence <select id="conf"><option value="">any</option><option>high</option>
     <option>medium</option><option>low</option></select></label>
   <label>Min mentions <input type="number" id="min" value="1" min="1" style="width:56px"></label>
   <label><input type="checkbox" id="nobp" checked> hide boilerplate-only</label>
   <label>Sort <select id="sort"><option value="m">mentions</option><option value="p">pages</option>
     <option value="a">A to Z</option><option value="th">themed mentions</option></select></label>
  </div>
  <div id="list"></div>
 </div>
 <div class="panel" id="detail"><div class="empty">Select an organisation to read its mentions in context.
  <div id="themes" style="margin-top:16px"></div></div></div>
</div>
<script>
const D = {{DATA}};
const $ = s => document.querySelector(s);
const esc = s => (s||"").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const reEsc = s => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
$("#stats").textContent = `${D.orgs.length} organisations across ${D.nPages} pages`;
for (const t of D.themes) $("#theme").insertAdjacentHTML("beforeend", `<option>${esc(t.name)}</option>`);
for (const t of [...new Set(D.orgs.map(o => o.org_type).filter(Boolean))].sort())
  $("#type").insertAdjacentHTML("beforeend", `<option>${esc(t)}</option>`);
if (D.siteList.length > 1) for (const s of D.siteList) $("#site").insertAdjacentHTML("beforeend", `<option>${esc(s)}</option>`);
else $("#site").closest("label").style.display = "none";
const siteOk = (o, s) => !s || (s === "@main" ? o.roles.includes("main") : s === "@linked" ? o.roles.includes("linked") : o.sites.includes(s));
const ctxSiteOk = (c, s) => !s || (s === "@main" ? c.sr === "main" : s === "@linked" ? c.sr === "linked" : c.st === s);
$("#themes").innerHTML = D.themes.length ? "<b>Themes</b><table><tr><th>theme</th><th>pages</th><th>share</th></tr>" +
  D.themes.map(t => `<tr><td>${esc(t.name)}</td><td>${t.pages}</td><td>${(100*t.pages/D.nPages).toFixed(1)}%</td></tr>`).join("") + "</table>" : "";
const themeCount = (o, t) => { const m = (o.themes||"").match(new RegExp("(?:^|; )" + reEsc(t) + " \\((\\d+)\\)")); return m ? +m[1] : 0; };
const themedTotal = o => (o.themes||"").split("; ").filter(Boolean).reduce((a, x) => a + +(x.match(/\((\d+)\)$/)||[0,0])[1], 0);
let selected = null;
function render() {
  const q = $("#q").value.toLowerCase(), th = $("#theme").value, ty = $("#type").value,
        cf = $("#conf").value, si = $("#site").value, mn = +$("#min").value || 1, nobp = $("#nobp").checked, so = $("#sort").value;
  let rows = D.orgs.filter(o => (!q || (o.organisation + " " + o.variants + " " + o.linked_domains + " " + o.acronyms).toLowerCase().includes(q))
    && siteOk(o, si) && (!th || themeCount(o, th) > 0) && (!ty || o.org_type === ty) && (!cf || o.confidence === cf)
    && o.distinct_mentions >= mn && !(nobp && o.boilerplate_only));
  const key = {m: o => -o.substantive_mentions - o.distinct_mentions/1e4, p: o => -o.pages,
               th: o => th ? -themeCount(o, th) : -themedTotal(o), a: o => o.organisation.toLowerCase()}[so];
  rows.sort((a, b) => { const x = key(a), y = key(b); return x < y ? -1 : x > y ? 1 : 0; });
  $("#list").innerHTML = rows.map(o => `<div class="org${o.org_key===selected?" sel":""}" data-k="${esc(o.org_key)}">
    <span class="n">${o.substantive_mentions}/${o.distinct_mentions}</span><b>${esc(o.organisation)}</b>
    <div class="meta">${esc(o.org_type||"type unknown")} · <span class="conf-${o.confidence}">${o.confidence}</span>
    · ${o.pages} page${o.pages==1?"":"s"}${o.sites.length>1?" · "+o.sites.length+" sites":""}${o.acronyms?" · "+esc(o.acronyms):""}</div></div>`).join("")
    || '<div class="empty">No organisations match these filters.</div>';
}
function show(k) {
  selected = k; render();
  const o = D.orgs.find(x => x.org_key === k), cs = D.ctx[k] || [], th = $("#theme").value;
  const si = $("#site").value;
  const list = cs.filter(c => (!th || c.th.includes(th)) && ctxSiteOk(c, si));
  const hl = c => {
    // Name first; theme terms are then marked only in text outside the name's mark.
    let s = esc(c.c);
    if (c.s) s = s.replace(new RegExp(reEsc(esc(c.s)), "g"), "<mark>$&</mark>");
    const terms = (c.tt||"").split("; ").filter(Boolean).sort((a,b) => b.length - a.length);
    if (terms.length) {
      const re = new RegExp("(" + terms.map(t => reEsc(esc(t))).join("|") + ")", "gi");
      s = s.split(/(<mark>.*?<\/mark>)/).map(part => part.startsWith("<mark>") ? part
            : part.replace(re, '<mark class="t">$1</mark>')).join("");
    }
    return s;
  };
  $("#detail").innerHTML = `<h2>${esc(o.organisation)}</h2>
   <div class="meta">${esc(o.org_type||"type unknown")} · confidence <span class="conf-${o.confidence}">${o.confidence}</span>
   · ${o.substantive_mentions} substantive / ${o.distinct_mentions} distinct mentions on ${o.pages} page${o.pages==1?"":"s"}</div>
   <p class="meta">Found by: ${esc(o.methods)}<br>Variants: ${esc(o.variants)}${o.linked_domains?"<br>Links to: "+esc(o.linked_domains):""}</p>
   <p>${(o.themes||"").split("; ").filter(Boolean).map(t => `<span class="chip">${esc(t)}</span>`).join("")||'<span class="meta">No theme terms in its paragraphs</span>'}</p>
   ${list.map(c => `<div class="ctx"><p>${hl(c)}</p><div class="meta">
     <a href="${esc(c.u)}" target="_blank" rel="noopener">${esc(c.t || c.u)}</a>${c.d?" · "+esc(c.d):""}
     · ${esc(c.m)}${c.g!=="main"?" · "+esc(c.g):""}${c.b?" · boilerplate":""}
     ${D.siteList.length>1?`<br>Site: ${esc(c.st)}${c.sr==="linked"?" (linked)":""}`:""}${c.ve?" · via event: "+esc(c.ve):""}${c.cm?" · calendar "+esc(c.cm):""}
     ${c.e?`<br>PDF found on <a href="${esc(c.e)}" target="_blank" rel="noopener">${esc(c.e)}</a>`:""}
     ${c.th?"<br>Paragraph themes: "+esc(c.th):""}${c.co?"<br>Also mentioned: "+esc(c.co):""}</div></div>`).join("")
     || '<div class="empty">No contexts for this theme filter.</div>'}`;
  $("#detail").scrollTop = 0;
}
$("#list").addEventListener("click", e => { const el = e.target.closest(".org"); if (el) show(el.dataset.k); });
for (const id of ["#q","#theme","#site","#type","#conf","#min","#nobp","#sort"]) $(id).addEventListener("input", () => selected ? show(selected) : render());
render();
</script></body></html>
"""
