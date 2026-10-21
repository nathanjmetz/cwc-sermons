// Holds the Jev (TypeSafe) key for the public site, so it never reaches the browser.
// The page sends the search and up to 60 passages; this asks Jev whether each passage
// addresses the search and returns a probability for each. Same recipe as cwc/rerank.py.
const API = "https://api.typesafe.ai/v1/systemone", MODEL = "jev-latest", PER_CALL = 30;
const ALLOWED = ["https://nathanjmetz.github.io", "http://127.0.0.1:8787", "http://localhost:8787"];
const QUESTION = id => `Passage \`passages.${id}\` is relevant to \`query\`: in it the preacher speaks to what the query ` +
  "describes, directly or by clear implication.";
const CRITERIA = {
  true: "The passage addresses the idea, theme, or situation the query describes.",
  false: "The passage is only loosely related, merely shares a word or topic, or does not address the query.",
};

// best effort: each warm instance allows 20 searches a minute per visitor
const seen = new Map();
const limited = ip => {
  const now = Date.now(), w = seen.get(ip) || [];
  const recent = w.filter(t => now - t < 60000);
  recent.push(now); seen.set(ip, recent);
  return recent.length > 20;
};

async function ask(query, texts) {
  const ids = texts.map((_, i) => "P" + String(i).padStart(2, "0"));
  const body = {
    model: MODEL,
    state: { query, passages: Object.fromEntries(ids.map((id, i) => [id, texts[i]])) },
    questions: Object.fromEntries(ids.map(id => [id, { type: "noul", instructions: QUESTION(id), criteria: CRITERIA }])),
  };
  const r = await fetch(API, { method: "POST", signal: AbortSignal.timeout(9000),
    headers: { Authorization: `Bearer ${process.env.TYPESAFE_API_KEY}`, "Content-Type": "application/json" },
    body: JSON.stringify(body) });
  if (!r.ok) throw new Error(`Jev answered ${r.status}`);
  const { answers } = await r.json();
  return ids.map(id => Number(answers[id].noul));
}

export default async function handler(req, res) {
  const origin = req.headers.origin || "";
  const ok = ALLOWED.includes(origin) || /^http:\/\/192\.168\.\d+\.\d+:8787$/.test(origin);
  if (ok) {
    res.setHeader("Access-Control-Allow-Origin", origin);
    res.setHeader("Vary", "Origin");
    res.setHeader("Access-Control-Allow-Methods", "POST, OPTIONS");
    res.setHeader("Access-Control-Allow-Headers", "Content-Type");
  }
  if (req.method === "OPTIONS") return res.status(ok ? 204 : 403).end();
  if (!ok) return res.status(403).json({ error: "Not allowed from this site." });
  if (req.method !== "POST") return res.status(405).json({ error: "POST only." });
  const ip = String(req.headers["x-forwarded-for"] || "").split(",")[0].trim();
  if (limited(ip)) return res.status(429).json({ error: "Too many searches. Wait a minute." });

  const { query, texts } = req.body || {};
  if (typeof query !== "string" || !query.trim() || query.length > 600 || !Array.isArray(texts) || !texts.length ||
      texts.length > 60 || texts.some(t => typeof t !== "string" || t.length > 2500))
    return res.status(400).json({ error: "Send a query and 1 to 60 passages." });
  try {
    const batches = [];
    for (let i = 0; i < texts.length; i += PER_CALL) batches.push(texts.slice(i, i + PER_CALL));
    const parts = await Promise.all(batches.map(b => ask(query.trim(), b)));
    res.setHeader("Cache-Control", "no-store");
    return res.status(200).json({ p: parts.flat() });
  } catch (e) {
    return res.status(502).json({ error: String(e.message || e) });
  }
}
