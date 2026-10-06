# FinSense AI - frontend

React 19 + TypeScript single-page app built with Vite 8 and Tailwind CSS 4. It talks only to the API
on the same origin (`/api/v1/...`), so session cookies stay first-party.

```bash
npm ci                 # install exactly the locked dependencies (Node 20.19+)
npm run dev            # http://localhost:5173, proxies /api to http://127.0.0.1:8000
npm run build          # type-check (tsc -b) and build to dist/
npm run preview        # serve dist/ on http://localhost:4173 with the same /api proxy
npm run lint           # ESLint
npm test               # Vitest + Testing Library (jsdom)
```

Set `VITE_API_PROXY_TARGET` to proxy to a different API address. In Docker the build is served by
nginx (`deploy/nginx.conf`), which also proxies `/api` and adds the security headers in
`deploy/security-headers.conf`.

Structure:

| Path | Contents |
|---|---|
| `src/api/client.ts` | the only HTTP client: CSRF header, error envelope, 401 handling |
| `src/api/types.ts` | response types mirroring the backend |
| `src/auth/` | session context and protected routes |
| `src/components/` | layout, UI primitives, KPI cards, charts, selectors, portfolio editor, optimiser |
| `src/pages/` | one file per route (dashboard, funds, risk, portfolios, assistant, documents, ML, what-if, settings, admin, auth) |
| `src/lib/` | formatting, weight validation, citation parsing |
| `src/test/` | Vitest tests |
