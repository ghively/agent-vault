# Agent Vault Web UI

A Vite + React + TypeScript single-page app that gives Agent Vault a
browser-based desktop. This doc is the architecture reference for anyone
touching `web/` for the first time — the root [`../README.md`](../README.md)
and [`../DOCS.md`](../DOCS.md) only mention "run `npm install && npm run dev`";
this fills in what's actually inside.

## Quick start

```bash
cd web
npm install
npm run dev              # Vite dev server, http://localhost:5173
```

Environment (read by `web/src/api/client.ts` at build/runtime via Vite's
`import.meta.env`):

| Var | Default | Purpose |
|---|---|---|
| `VITE_VAULT_API_URL` | `http://127.0.0.1:7778` | Base URL of the `agent-vault-serve` HTTP API |

Other scripts (`web/package.json`):

```bash
npm run build             # tsc -b && vite build -> web/dist (served by agent-vault-serve if present)
npm run preview           # preview the production build locally
npm test                  # vitest run — one-shot test run (used by CI)
npm run test:watch        # vitest — watch mode
```

There is **no lint script and no ESLint config** in `web/` today — `tsc -b`
(part of `build`) is the only static check.

## Entry point and the "PHOSPHOR" desktop shell

`web/index.html` loads `web/src/main.tsx`, which renders:

```tsx
<QueryClientProvider client={queryClient}>
  <TokenGate>
    <Desktop />
  </TokenGate>
</QueryClientProvider>
```

`Desktop` (`web/src/wm/Desktop.tsx`) is a self-contained window-manager shell
(internally referred to as "PHOSPHOR", ported from a sibling project called
SynapseNAS and trimmed down to Agent Vault's own apps) — not a conventional
router-driven page. **`web/src/App.tsx` (a simpler sidebar-nav component) is
dead code**: nothing imports it except its own orphaned `App.test.tsx`. Don't
extend `App.tsx` expecting it to run — extend `Desktop`/`screens/` instead.

### The window manager (`web/src/wm/`)

- **`apps.ts`** is the app registry: an `AppId` union and an `APPS` map
  (glyph/label/color/gradient/default size) for each app, plus `APP_ORDER`
  and `APP_GROUPS` (one "VAULT" group). **8 apps** are registered today:
  `browse`, `wiki`, `vault`, `creds`, `review`, `pipeline`, `command`
  (Command Deck), `settings`.
- **`Desktop.tsx`** is the shell root: renders the `Waybar` taskbar,
  `QuickPanel`, one `Window` per open app (open-window state lives in
  `store/windows.ts`), plus the `Launcher` and `CommandBar` overlays.
  `Cmd/Ctrl+K` toggles the command bar globally.
- **`Window.tsx`** implements real drag (title-bar mousedown → global
  mousemove/mouseup), 8-direction resize with edge snapping
  (`wm/geometry.ts`), maximize/restore, and focus/z-order — a genuine desktop
  window manager, not a static mock.
- **`Launcher.tsx`** — search/grid app picker (Escape to close).
- **`TabbedApp.tsx`** — generic tab-bar wrapper some apps use to show
  multiple screens as tabs inside one window (`wm/consolidation.ts` wires
  which apps do this); it takes a `renderTab` callback rather than importing
  `screens/` directly, to avoid a circular import with `ScreenRouter`.
- **`MobileShell.tsx` + `useIsMobile.ts` are unwired dead code.** Both are
  fully implemented (hamburger drawer, `matchMedia`-based breakpoint hook)
  and have their own tests, but `Desktop.tsx`/`main.tsx` never call
  `useIsMobile()` or render `MobileShell` — there is currently no live
  responsive/mobile switch. (`MobileShell.tsx` still shows the fallback title
  `"SynapseNAS"`, a leftover from the port, which is itself a sign it isn't
  exercised.) If you're asked to make the UI mobile-responsive, this is the
  half-finished starting point, not a working feature to build on top of.

### Screens (`web/src/screens/`) — the actual app content

Routing between screens is a plain `switch` in `screens/ScreenRouter.tsx`,
not a router library (there is none in `package.json`).

| Screen | What it does | Backend calls (`web/src/api/`) |
|---|---|---|
| `Vault.tsx` | Hub/landing screen; tiles that open the other apps | none — pure `openApp()` navigation |
| `Browse.tsx` | Search/filter vault entities | `GET /api/entities` |
| `Wiki.tsx` | Read entity docs; trigger recompile with a live log stream | `GET /api/entities[/:slug]`; `POST /api/entities/:slug/recompile` + `GET /api/jobs/:id/stream` (SSE) |
| `Creds.tsx` | List and reveal credential references/secrets | `GET /api/creds`; `POST /api/creds/:slug/resolve` |
| `Review.tsx` | Approve/reject queued proposals and needs-review entities | `GET /api/review/proposals\|entities`; `POST /api/review/{proposals,entities}/:id/{approve,reject}` |
| `Pipeline.tsx` | View run/ledger history; kick off ingest/compile/promote jobs | `GET /api/runs`, `/api/ledgers`; `POST /api/jobs/run` + `GET /api/jobs/:id/stream` (SSE) |
| `CommandDeck.tsx` | Natural-language "ask" query + status | `GET /api/ask?q=`, `GET /api/status` — **neither endpoint exists server-side today**, see Known gaps |
| `Settings.tsx` | Show vault/service/compiler/resolver config; client UI prefs | `GET /api/settings` (direct fetch, not a hook) |

See [`../docs/API.md`](../docs/API.md) for the full endpoint reference these
screens call into.

### API layer (`web/src/api/`)

- `client.ts` — the fetch wrapper: attaches the bearer token, points at
  `VITE_VAULT_API_URL`, and **auto-clears the stored token on `401`** (but
  deliberately not on `403`, to avoid bouncing a session over a
  permission-scoped error rather than a bad token).
- `hooks.ts` — React Query hooks (`useEntities`, `useEntity`, `useCreds`,
  `useReviewProposals`, `useReviewEntities`, `useRuns`, `useLedgers`, …)
  wrapping `client.ts` calls.
- `jobs.ts` — job-run + SSE-stream helpers (`startJob`, `useJobStream`,
  `recompileEntity`) used by `Pipeline.tsx` and `Wiki.tsx`.
- `mutations.ts` — React Query mutations for the review approve/reject actions.
- `resolve.ts` — the credential-resolve call used by `Creds.tsx`.
- `types.ts` — shared TS types for API payloads.

### Auth (`web/src/TokenGate.tsx` + `web/src/store/auth.ts`)

- `auth.ts` is a small Zustand store holding `token` in **`sessionStorage`**
  (key `vault_token`) — cleared when the tab closes, not persisted like the
  UI prefs below.
- `TokenGate` renders a password-style token entry screen when no token is
  stored. On submit it validates the token by calling `GET /api/health` with
  it *before* storing it and mounting the app, so a bad token never gets past
  the gate.
- If the service has no `VAULT_TOKEN` configured, `/api/health` succeeds with
  no `Authorization` header needed and the gate effectively passes through.

### State (`web/src/store/`)

- `auth.ts` — token only (see above).
- `windows.ts` — all window-manager state: open windows, per-window
  position/size/z-order, launcher/command-bar/quick-panel open flags, and
  persisted UI preferences (`desktopScale`, `theme`, `density`, `locale`),
  each written to its own **`localStorage`** key (`vault.desktopScale`,
  `vault.theme`, …). Note the asymmetry: auth token → sessionStorage, UI
  prefs → localStorage.

### i18n (`web/src/i18n/`) — a stub, not real localization

`i18next`/`react-i18next` are initialized with an **empty** translation table
(`resources: { en: { translation: {} } }`). No `I18nProvider` is mounted in
`main.tsx`. The one real consumer, `wm/QuickPanel.tsx`, calls `useT()`, which
would throw outside a provider if actually exercised — despite `store/windows.ts`
carrying a `locale`/`setLocale` field, there is no working localization today.
Treat this as scaffolding, not a feature to wire UI strings into yet.

### Styling

No CSS framework (no Tailwind/MUI/etc.) and no CSS-in-JS library. Components
are styled with inline `style={}` objects plus a few hand-rolled CSS-string
blocks (`GV_CSS` in `Desktop.tsx`/`MobileShell.tsx`), backed by
`web/src/theme.ts` (design tokens) and `web/src/theme.css` (globals) — a
small custom design system.

### Testing

Vitest (`web/vite.config.ts`: `environment: "jsdom"`, `setupFiles:
["./src/test/setup.ts"]`, which just imports `@testing-library/jest-dom`).
`@testing-library/react` + `/user-event` for component tests. Tests are
colocated per-component (`*.test.tsx` / `*.test.ts` next to the file they
cover) across `wm/`, `screens/`, `store/`, `ui/`. Run `npm test` for a
one-shot run (what CI does via `.github/workflows/ci.yml`'s `frontend` job:
`npm ci` → `npm test -- --run` → `npm run build`).

## Known gaps (don't be surprised by these)

- **`CommandDeck` ("Command Deck") is a broken app: it calls `GET /api/ask`
  and `GET /api/status`, and neither route exists in `agent_vault/api/`** (see
  [`../docs/API.md`](../docs/API.md) for the actual route list). `useStatus()`
  fires unconditionally on mount, so opening this app always hits a 404. This
  isn't a regression to "fix" lightly — it's a UI built ahead of its backend;
  implementing `/api/ask` (a query endpoint over the entity index, likely
  wrapping the same lookup logic as `agent_vault/synapse.py`'s `find`/`due`/
  `expiring`) and `/api/status` (a vault summary: due items, review count,
  last compile run, entity count — matching the shape `CommandDeck.tsx`
  already destructures) is real, scoped backend work if this app is wanted.
- `App.tsx` — dead code, see above.
- `MobileShell.tsx` / `useIsMobile.ts` — implemented but unwired.
- i18n — stub only, no provider mounted.
- No ESLint config, no lint script.
- No router library — navigation is in-memory state via `store/windows.ts` +
  `ScreenRouter`'s `switch`; there's no deep-linking (a page refresh always
  lands back on the `Vault` hub screen).
