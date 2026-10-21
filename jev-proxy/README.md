# cwc-jev

Tiny Vercel function that lets the public (GitHub Pages) site use Jev without exposing the key.
`POST /api/rerank` with `{query, texts: [...up to 60]}` returns `{p: [probabilities]}`.
Only answers the site's own origins; about 20 searches a minute per visitor.

Deploy from this folder: `vercel deploy --prod --scope njm5`. The key is the `TYPESAFE_API_KEY`
environment variable, set with `vercel env add TYPESAFE_API_KEY production < ../.env.typesafe`.
