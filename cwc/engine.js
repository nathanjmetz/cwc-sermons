// In-browser search for the static (GitHub Pages) build. It answers the page's
// /api/search and /api/stats calls from the JSON files in data/, mirroring
// cwc/search.py. Stemming must match stem() in cwc/export_static.py.
(() => {
  const BASE = new URL("data/", document.baseURI).href;
  // Jev double-checks the closest passages; the key lives behind this small function (jev-proxy/)
  const JEV = "__JEV_URL__", JEV_POOL = 30, JEV_PASSAGES = 60;
  const LEAD_IN = 4, BUCKET = 150; // deciseconds: words in 15s buckets; a "window" is two buckets (30s)
  const cache = {};
  const get = path => cache[path] ??= fetch(BASE + path).then(r => r.ok ? r.json() : null).catch(() => null);
  let meta;
  const loadMeta = async () => meta ??= await get("meta.json");

  const stem = w => {
    w = w.toLowerCase().replace(/[^a-z0-9']/g, "").replace(/'/g, "");
    if (w.length > 4) w = w.replace(/(ing|ed|es|s|ness|ful|ly)$/, "");
    return w.slice(0, 7);
  };
  const video = i => { const [id, title, date, duration, thumbnail, , speakers = [], span = null] = meta.videos[i];
    return { id, title, date, duration, thumbnail, speakers, span, url: `https://www.youtube.com/watch?v=${id}` }; };
  let only = null;   // ids a speaker filter allows, for the search in progress
  const dateOk = (v, rng) => (!only || only.has(v.id)) && (!rng || (v.date && rng[0] <= v.date && v.date <= rng[1]));
  // with a speaker chosen, a full service only counts while the sermon runs: someone else led the worship
  const timeOk = (v, t) => !only || !v.span || (t >= v.span[0] - 30 && t <= v.span[1] + 30);
  const pad = n => String(n).padStart(2, "0");
  const iso = d => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  const addDays = (d, n) => new Date(d.getFullYear(), d.getMonth(), d.getDate() + n);

  // ---------- query parsing ----------
  const MONTHS = ["january","february","march","april","may","june","july","august","september","october","november","december"];
  function extractDates(q) {
    let m;
    const around = d => [iso(addDays(d, -3)), iso(addDays(d, 3))];
    const strip = re => q.replace(re, " ").replace(/\s+/g, " ").trim();
    if ((m = q.match(/\b(20\d\d)-(\d{1,2})-(\d{1,2})\b/)))
      return [strip(m[0]), around(new Date(+m[1], m[2] - 1, +m[3]))];
    if ((m = q.match(/\b(\d{1,2})\/(\d{1,2})\/(\d{2,4})\b/))) {
      const y = +m[3] < 100 ? 2000 + +m[3] : +m[3];
      return [strip(m[0]), around(new Date(y, m[1] - 1, +m[2]))];
    }
    const monthRe = new RegExp(`\\b(${MONTHS.join("|")}|${MONTHS.map(x => x.slice(0, 3)).join("|")})\\.?,?\\s+(20\\d\\d)\\b`, "i");
    if ((m = q.match(monthRe))) {
      const mo = MONTHS.findIndex(x => x.startsWith(m[1].toLowerCase().slice(0, 3))), y = +m[2];
      return [strip(m[0]), [iso(new Date(y, mo, 1)), iso(new Date(y, mo + 1, 0))]];
    }
    if ((m = q.match(/(?<![:\d])\b(20[0-3]\d)\b(?![:\d])/)))
      return [strip(m[0]), [`${m[1]}-01-01`, `${m[1]}-12-31`]];
    return [q, null];
  }

  let refRe, bookOnlyRe;
  function buildRefRe() {
    const ord = { 1: "(?:1|i|1st|first)", 2: "(?:2|ii|2nd|second)", 3: "(?:3|iii|3rd|third)" };
    const esc = s => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&").replace(/ /g, "\\s+");
    const alias = a => { const m = a.match(/^([123]) (.+)$/); return (m ? ord[m[1]] + "\\s*" + esc(m[2]) : esc(a)) + "\\.?"; };
    const book = "(?:" + Object.keys(meta.books).sort((a, b) => b.length - a.length).map(alias).join("|") + ")";
    refRe = new RegExp(`\\b(${book})\\s*(?:,?\\s*chapter\\s+)?(\\d{1,3})\\b(?:(?:\\s*:\\s*|\\.(?=\\d)|,?\\s+verses?\\s+|,?\\s+vs?\\.?\\s+)(\\d{1,3})\\b(?:\\s*(?:-|–|—|through|thru|to)\\s*(\\d{1,3})\\b)?)?`, "i");
    bookOnlyRe = new RegExp(`^\\s*(${book})\\s*$`, "i");
  }
  function normBook(raw) {
    let s = raw.toLowerCase().replace(/\.$/, "").replace(/\s+/g, " ").trim();
    if (!(s in meta.books)) {
      const m = s.match(/^(1|i|1st|first|2|ii|2nd|second|3|iii|3rd|third)\s*(.+)$/);
      if (m) s = ({ i: 1, "1st": 1, first: 1, ii: 2, "2nd": 2, second: 2, iii: 3, "3rd": 3, third: 3 }[m[1]] || m[1]) + " " + m[2];
    }
    return meta.books[s] || null;
  }
  function parseRef(text) {
    let m = text.match(refRe);
    if (m) {
      const book = normBook(m[1]);
      if (book) return { book, chapter: +m[2], vs: m[3] ? +m[3] : null, ve: m[4] ? +m[4] : (m[3] ? +m[3] : null), match: m[0] };
    }
    m = text.match(bookOnlyRe);
    if (m && normBook(m[1])) return { book: normBook(m[1]), chapter: null, vs: null, ve: null, match: m[0] };
    return null;
  }
  const label = r => r.book + (r.chapter ? ` ${r.chapter}` : "") + (r.vs ? `:${r.vs}` : "") + (r.ve && r.ve !== r.vs ? `-${r.ve}` : "");

  // ---------- helpers ----------
  const moment = (t, lab, snippet, score) => ({ t: Math.max(0, Math.floor(t - LEAD_IN)), label: lab, snippet, score });
  function finish(results, limit) {
    const out = [...results.values()].sort((a, b) => b.score - a.score || (b.date || "").localeCompare(a.date || "")).slice(0, limit);
    for (const r of out) {
      r.moments.sort((a, b) => b.score - a.score);
      const best = r.moments[0];
      r.start = best ? best.t : 0;
      r.moments = best ? [best, ...r.moments.slice(1, 6).sort((a, b) => a.t - b.t)] : [];
    }
    return out;
  }

  // ---------- scripture ----------
  function refScore(q, row) {
    const [, ch, vs, ve] = row;
    if (q.chapter == null) return 1.0;
    if (ch !== q.chapter) return ch == null ? 0.25 : 0;
    if (q.vs == null) return vs == null ? 3.0 : 2.6;
    if (vs == null) return 1.8;
    const qa = q.vs, qb = q.ve || q.vs, ra = vs, rb = ve || vs;
    if (ra <= qb && qa <= rb) return ra === qa && rb === qb ? 3.0 : 2.7;
    return Math.min(Math.abs(ra - qb), Math.abs(qa - rb)) <= 3 ? 1.4 : 0.9;
  }
  async function searchScripture(q, rng, limit) {
    const rows = (await get(`refs/${meta.slugs[q.book]}.json`)) || [];
    const results = new Map();
    for (const row of rows) {
      const s = refScore(q, row);
      if (s <= 0) continue;
      const v = video(row[0]);
      if (!dateOk(v, rng) || (row[4] != null && !timeOk(v, row[4]))) continue;
      const r = results.get(v.id) || { ...v, score: 0, moments: [], cited_in: new Set(), hits: 0, desc: 0, kind: "scripture" };
      results.set(v.id, r);
      const lab = label({ book: q.book, chapter: row[1], vs: row[2], ve: row[3] });
      if (row[4] == null) { r.cited_in.add(`${row[5]}: ${lab}`); r.score = Math.max(r.score, s * 0.9); r.desc = Math.max(r.desc, s); continue; }
      r.hits++;
      r.moments.push(moment(row[4], lab, row[6], s - row[4] / 1e6));
      r.score = Math.max(r.score, s);
    }
    for (const r of results.values()) {
      r.score += 0.25 * Math.log1p(r.hits) + (r.desc >= 2.6 ? 0.5 : 0);
      r.cited_in = [...r.cited_in].sort(); delete r.hits; delete r.desc;
    }
    return finish(results, limit);
  }

  // ---------- words ----------
  function terms(text) {
    const phrases = [...text.matchAll(/"([^"]+)"/g)].map(m => m[1].toLowerCase());
    const rest = text.replace(/"[^"]*"/g, " ");
    const stop = new Set(meta.stop);
    const words = [...phrases.flatMap(p => p.split(/\s+/)), ...(rest.toLowerCase().match(/[\w']+/g) || [])]
      .filter(w => w.length > 1 && !stop.has(w));
    return { phrases, stems: [...new Set(words.map(stem).filter(s => s.length > 1 && !stop.has(s)))] };
  }
  async function postingsFor(s) {
    const shard = (await get(`terms/${s.slice(0, 2)}.json`)) || {};
    const out = new Map();
    for (const [i, ...ts] of shard[s] || []) out.set(i, ts);
    return out;
  }
  async function snippetAt(id, tds, stems, phrases) {
    const tx = await get(`tx/${id}.json`);
    if (!tx) return { snippet: "", ok: true };
    const toks = tx.w.split(" ");
    let k = tx.t.findIndex(x => x >= tds); if (k < 0) k = toks.length - 1;
    const lo = Math.max(0, k - 18), around = toks.slice(lo, lo + 55).join(" ");
    const ok = !phrases.length || phrases.every(p => around.toLowerCase().replace(/[^\w' ]/g, "").includes(p.replace(/[^\w' ]/g, "")));
    return { snippet: (lo ? "… " : "") + around.slice(0, 260) + " …", ok };
  }
  async function searchText(text, rng, limit) {
    const { phrases, stems } = terms(text);
    if (!stems.length) return [];
    const N = meta.videos.length;
    const plist = await Promise.all(stems.map(postingsFor));
    const idf = plist.map(p => Math.log(1 + N / (1 + p.size)));
    for (const mode of ["and", "or"]) {
      const results = new Map();
      const vids = new Set(plist.flatMap(p => [...p.keys()]));
      for (const i of vids) {
        const v = video(i);
        if (!dateOk(v, rng)) continue;
        // count each term per 15s bucket, then score 30s windows (bucket b + b+1)
        const buckets = new Map();
        plist.forEach((p, j) => (p.get(i) || []).forEach(t => {
          if (!timeOk(v, t / 10)) return;
          const b = Math.floor(t / BUCKET);
          if (!buckets.has(b)) buckets.set(b, { n: stems.map(() => 0), first: t });
          const e = buckets.get(b); e.n[j]++; e.first = Math.min(e.first, t);
        }));
        const wins = [];
        for (const [b, e] of buckets) {
          const nx = buckets.get(b + 1);
          const n = e.n.map((c, j) => c + (nx ? nx.n[j] : 0));
          const have = n.filter(c => c > 0).length;
          if (mode === "and" && have < stems.length) continue;
          wins.push({ b, first: e.first, score: n.reduce((s, c, j) => s + idf[j] * Math.log1p(c), 0) * (have / stems.length) });
        }
        if (!wins.length) continue;
        wins.sort((a, b) => b.score - a.score);
        const picked = [];
        for (const w of wins) if (picked.every(p => Math.abs(p.b - w.b) > 1)) picked.push(w);
        const title = meta.videos[i][5].split(" ");
        const titleHit = stems.some(s => title.includes(s));
        results.set(v.id, { ...v, kind: "text", matched: mode === "and" ? "all words" : "some words",
          score: picked[0].score + 0.6 * Math.log1p(picked.length) + (titleHit ? 1.5 : 0),
          moments: picked.slice(0, 6).map(w => ({ ...moment(w.first / 10, null, "", w.score), _tds: w.first })) });
      }
      let out = finish(results, limit * (phrases.length ? 3 : 1));
      await Promise.all(out.map(async r => {
        await Promise.all(r.moments.map(async m => { const s = await snippetAt(r.id, m._tds, stems, phrases); m.snippet = s.snippet; m.ok = s.ok; delete m._tds; }));
        if (phrases.length) { r.moments = r.moments.filter(m => m.ok); if (r.moments.length) r.start = r.moments[0].t; }
      }));
      if (phrases.length) out = out.filter(r => r.moments.length);
      out = out.slice(0, limit);
      if (out.length >= 3 || mode === "or") return out;
    }
    return [];
  }


  // ---------- ideas (meaning, not words) ----------
  // Same model as cwc/embed.py, run in the browser. Downloaded once (~35 MB), then cached.
  const QUERY_PREFIX = "Represent this sentence for searching relevant passages: ";
  let extractor, vectors;
  const progress = text => window.dispatchEvent(new CustomEvent("cwc-progress", { detail: text }));
  async function loadIdeas() {
    if (!extractor) {
      progress("Loading the idea-search model (one-time download, about 35 MB)…");
      const { pipeline } = await import("https://cdn.jsdelivr.net/npm/@huggingface/transformers@3.0.2");
      extractor = await pipeline("feature-extraction", "Xenova/bge-small-en-v1.5", { dtype: "q8" });
    }
    if (!vectors) {
      progress("Loading sermon fingerprints…");
      const [p, buf] = await Promise.all([get("passages.json"), fetch(BASE + "vectors.i8").then(r => r.arrayBuffer())]);
      vectors = { meta: p.passages, dim: p.dim, v: new Int8Array(buf) };
    }
    progress("");
  }
  // the model can only run one batch at a time, so calls wait their turn
  let queue = Promise.resolve();
  const embed = texts => (queue = queue.catch(() => {}).then(() => embedNow(texts)));
  async function embedNow(texts) {
    const out = await extractor(texts, { pooling: "cls", normalize: true });
    const d = out.dims[1];
    return texts.map((_, i) => out.data.slice(i * d, (i + 1) * d));
  }
  async function jevRerank(q, results) {
    if (!JEV.startsWith("https://")) return;
    const picks = [];
    for (const r of [...results.values()].sort((a, b) => b.score - a.score))
      for (const m of [...r.moments].sort((a, b) => b.score - a.score).slice(0, 2)) picks.push([r, m]);
    picks.splice(JEV_PASSAGES);
    const txs = Object.fromEntries(await Promise.all([...new Set(picks.map(([r]) => r.id))].map(async id => [id, await get(`tx/${id}.json`)])));
    const texts = picks.map(([r, m]) => { const tx = txs[r.id]; if (!tx) return "";
      const toks = tx.w.split(" "); return toks.filter((_, j) => tx.t[j] >= m._s && tx.t[j] <= m._e).join(" ").slice(0, 2400); });
    let probs;
    try {
      window.dispatchEvent(new CustomEvent("cwc-progress", { detail: "Double-checking the closest matches…" }));
      const res = await fetch(JEV, { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query: q, texts: texts.map(t => t || "(no words)") }), signal: AbortSignal.timeout(12000) });
      if (!res.ok) return;
      probs = (await res.json()).p;
    } catch (e) { return; }   // offline, slow, or out of quota: keep the meaning-only order
    if (!Array.isArray(probs) || probs.length !== picks.length) return;
    const judged = new Set();
    picks.forEach(([, m], i) => { m.jev = Math.round(probs[i] * 1000) / 1000; m.score = 0.75 * probs[i] + 0.25 * m.score; judged.add(m); });
    for (const r of results.values()) {
      // unjudged moments rank below judged ones; near-misses go when better ones remain
      for (const m of r.moments) if (!judged.has(m)) m.score *= 0.25;
      const keep = r.moments.filter(m => (m.jev ?? 1) >= 0.05);
      r.moments = keep.length ? keep : r.moments.slice(0, 1);
      r.score = r.moments.length ? Math.max(...r.moments.map(m => m.score)) : 0;
      r.jev = Math.max(0, ...r.moments.map(m => m.jev ?? 0));
    }
  }

  async function searchIdeas(q, rng, limit) {
    const final = limit;
    if (JEV.startsWith("https://")) limit = Math.max(limit, JEV_POOL);
    await loadIdeas();
    const [qv] = await embed([QUERY_PREFIX + q]);
    const { meta: pm, dim, v } = vectors, n = pm.length;
    const sims = new Float32Array(n);
    for (let k = 0; k < n; k++) {
      let s = 0; const o = k * dim;
      for (let j = 0; j < dim; j++) s += v[o + j] * qv[j];
      sims[k] = s / 127;
    }
    const order = Array.from(sims.keys()).sort((a, b) => sims[b] - sims[a]).slice(0, 5000);
    const results = new Map();
    for (const k of order) {
      const [vi, s, e] = pm[k], vid = video(vi);
      if (!dateOk(vid, rng) || !timeOk(vid, s / 10)) continue;
      const sim = sims[k];
      if (!results.has(vid.id)) {
        if (results.size >= limit) continue;
        results.set(vid.id, { ...vid, score: sim, moments: [], kind: "idea", _n: 0 });
      }
      const r = results.get(vid.id);
      if (sim >= r.score - 0.03) r._n++;
      if (sim >= r.score - 0.06 && r.moments.length < 6 && r.moments.every(m => Math.abs(m._s - s) >= 450))
        r.moments.push({ ...moment(s / 10, null, "", sim), _s: s, _e: e });
    }
    for (const r of results.values()) { r.score += 0.01 * Math.min(r._n - 1, 5); delete r._n; }
    await jevRerank(q, results);
    const out = finish(results, final);
    await Promise.all(out.map(async (r, i) => {
      const tx = await get(`tx/${r.id}.json`);
      const toks = tx ? tx.w.split(" ") : [];
      const slice = (a, b) => toks.filter((_, j) => tx.t[j] >= a && tx.t[j] < b).join(" ");
      for (const [j, m] of r.moments.entries()) {
        let start = m._s;
        if (i < 8 && j === 0 && tx) {
          // best 25s stretch inside the minute, so playback starts where the thought begins
          const subs = [];
          for (let a = m._s; a < Math.max(m._s + 1, m._e - 200); a += 100) { const t = slice(a, a + 250); if (t.split(" ").length >= 8) subs.push([a, t]); }
          if (subs.length > 1) {
            const ev = await embed(subs.map(x => x[1]));
            let best = 0, bs = -9;
            ev.forEach((e, x) => { let s = 0; for (let d = 0; d < e.length; d++) s += e[d] * qv[d]; if (s > bs) { bs = s; best = x; } });
            start = subs[best][0];
          }
        }
        m.t = Math.max(0, Math.floor(start / 10 - LEAD_IN));
        const text = tx ? slice(start, m._e + 1) : "";
        m.snippet = text.slice(0, 280) + (text.length > 280 ? " …" : "");
        m.score = Math.round(m.score * 1000) / 1000;
        delete m._s; delete m._e;
      }
      r.start = r.moments[0] ? r.moments[0].t : 0;
    }));
    return out;
  }

  async function search(q, limit, from, to, mode, who) {
    await loadMeta();
    only = who ? new Set(meta.videos.filter(v => (v[6] || []).includes(who)).map(v => v[0])) : null;
    if (!refRe) buildRefRe();
    q = (q || "").trim();
    let [text, rng] = extractDates(q);
    if (from || to) rng = [from || "0000-00-00", to || "9999-12-31"];
    const info = { query: q, date_range: rng, scripture: null, mode: mode || "words", speaker: who || null };
    if (mode === "ideas" && text) { const results = await searchIdeas(text, rng, limit); return { ...info, count: results.length, results }; }
    const ref = text ? parseRef(text) : null;
    let results;
    if (ref) {
      info.scripture = label(ref);
      results = await searchScripture(ref, rng, limit);
      const rest = ref.chapter ? text.replace(ref.match, " ").trim() : "";
      if (rest) { const keep = new Set((await searchText(rest, rng, 500)).map(r => r.id)); results = results.filter(r => keep.has(r.id)); }
      if (!results.length && ref.chapter == null) results = await searchText(text, rng, limit);
    } else if (text) results = await searchText(text, rng, limit);
    else if (rng || only) results = meta.videos.map((_, i) => video(i)).filter(v => dateOk(v, rng)).slice(0, limit)
      .map(v => ({ ...v, score: 0, start: only && v.span ? v.span[0] : 0, moments: [], kind: "date" }));
    else results = [];
    return { ...info, count: results.length, results };
  }

  const json = obj => new Response(JSON.stringify(obj), { headers: { "Content-Type": "application/json" } });
  const realFetch = window.fetch.bind(window);
  window.fetch = async (input, init) => {
    const url = new URL(typeof input === "string" ? input : input.url, location.href);
    if (url.pathname.endsWith("/api/search")) {
      const p = url.searchParams;
      try { return json(await search(p.get("q"), Math.min(+(p.get("limit") || 20), 100), p.get("from"), p.get("to"), p.get("mode"), p.get("who"))); }
      catch (e) { return json({ error: String(e) }); }
    }
    if (url.pathname.endsWith("/api/stats")) return json((await loadMeta()).stats);
    if (url.pathname.endsWith("/api/transcript")) return json((await get(`tx/${url.searchParams.get("id")}.json`)) || { w: "", t: [] });
    return realFetch(input, init);
  };
})();
