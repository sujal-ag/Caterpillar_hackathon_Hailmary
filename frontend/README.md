# Operator Companion — tablet PWA

React + Vite. Talks to edge-api only through the dev/preview server's proxy
(`/api/*` → edge-api REST, `/ws/*` → edge-api WebSocket), so no CORS setup is needed and the
tablet only has to reach this server.

```sh
npm ci
EDGE_API_URL=http://localhost:8000 npm run dev -- --host   # tablet: http://<laptop-ip>:5173
npm run lint && npm run build
```

- Contracts: `../contracts/rest.md`, `../contracts/ws.md`. UI text comes from
  `../contracts/i18n/en.json` (imported at build time).
- Demo logins (seed): operator `EMP1001` / `1234`, admin `SUP001` / `9999`. Sim controls are
  shown to admins only (`/sim/*` needs the admin role).
- Optional overrides: `VITE_API_BASE_URL`, `VITE_WS_BASE_URL` (skip the proxy).
