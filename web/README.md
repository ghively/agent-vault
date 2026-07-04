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
| `VITE_VAULT_API_URL` | `""` (empty — same-origin) | Base URL of the `agent-vault-serve` HTTP API. Empty means requests go to relative `/api/...`: correct in production when the UI is served by the vault service itself, and in dev because `vite.config.ts` proxies `/api` to `http://localhost:${VITE_VAULT_API_PORT:-7778}`. Set it only when the API lives on a different origin. |

All API URLs — including the SSE job stream and the token-gate health probe —
are built through `apiUrl()` in `client.ts`, so setting `VITE_VAULT_API_URL`
redirects everything consistently.

Other scripts (`web/package.json`):

```bash
npm run build             # tsc -b && vite build -> web/dist (served by agent-vault-serve if present)
npm run preview           # preview the production build locally
npm run lint              # ESLint 9 (typescript-eslint + react-hooks) — CI gate
npm run typecheck         # tsc -b (also runs inside build)
npm test                  # vitest run — one-shot test run (used by CI)
npm run test:watch        # vitest — watch mode
```

Static checks: **ESLint** (`eslint.config.js`, flat config) plus `tsc -b`. CI
(`.github/workflows/ci.yml`) runs lint → tests → build for `web/`.

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
router-driven page.

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
  (`wm/geometry.ts`), maximize/restore, minimize (yellow traffic light;
  restore via the window's Waybar task pill), and focus/z-order. Pointer
  deltas are divided by the desktop `zoom` scale so windows track the cursor
  at non-100% scale, and horizontal drags are clamped so ~40px of a window
  always stays visible.
- **`Launcher.tsx`** — search/grid app picker (Escape to close).

### Screens (`web/src/screens/`) — the actual app content

Routing between screens is a plain `switch` in `screens/ScreenRouter.tsx`,
not a router library (there is none in `package.json`).

| Screen | What it does | Backend calls (`web/src/api/`) |
|---|---|---|
| `Vault.tsx` | Hub/landing screen; tiles that open the other apps (incl. Settings) | none — pure `openApp()` navigation |
| `Browse.tsx` | Search/filter vault entities (input debounced ~250ms, previous results held as placeholder while refetching) | `GET /api/entities` |
| `Wiki.tsx` | Read entity docs; trigger a **synchronous** per-entity recompile; edit title/tags/notes via the "Edit details" modal (`PATCH` — only the changed keys are sent; unknown tags → 400, secret-shaped values → 422, both rendered verbatim in the modal); edit the raw markdown file via "Open in editor" (`GET`/`PUT .../raw` — dirty buffers ask before discard; slug/type edits are rejected server-side, use Reclassify instead) | `GET /api/entities[/:slug]`; `POST /api/entities/:slug/recompile` (returns the compile summary directly — no job/stream); `PATCH /api/entities/:slug`; `GET`/`PUT /api/entities/:slug/raw`; `GET /api/schema` (valid-tag vocabulary) |
| `Creds.tsx` | List credential references; reveal a secret after an explicit confirmation (the resolve endpoint uses the normal bearer token — no extra "re-auth" token exists) | `GET /api/creds`; `POST /api/creds/:slug/resolve` |
| `Review.tsx` | Approve/reject queued proposals and needs-review entities (buttons disable while a mutation is in flight; failures render inline); reclassify an entity via a modal dialog (type/subtype selects populated from the schema, Confirm disabled for an unchanged target; 400/409 render in the dialog, success shows "re-filed to X/Y, N refs rewritten" inline) | `GET /api/review/proposals\|entities`; `POST /api/review/{proposals,entities}/:id/{approve,reject}`; `GET /api/schema`; `POST /api/entities/:slug/reclassify` |
| `Pipeline.tsx` | View run/ledger history; run the weekly cadence or a lint dry-run (see Jobs below) | `GET /api/runs`, `/api/ledgers`; `POST /api/jobs/run` + `GET /api/jobs/:id/stream` (SSE) |
| `CommandDeck.tsx` | Natural-language "ask" query + status | `GET /api/ask?q=`, `GET /api/status` |
| `Settings.tsx` | Show vault/service/compiler/resolver config; client UI prefs | `GET /api/settings` (direct fetch, not a hook) |

See [`../docs/API.md`](../docs/API.md) for the full endpoint reference these
screens call into.

### Jobs and SSE streaming (`web/src/api/jobs.ts`)

- `startJob(op, args)` POSTs `{"op": ..., "args": [...]}` to `/api/jobs/run`
  and returns the `job_id`. Allowed ops server-side: `ingest`, `compile`,
  `promote`, `reclassify_apply`, `lint`, `compact`.
- `streamJob(jobId, onLine)` consumes `GET /api/jobs/:id/stream`. The SSE
  events are named **`stdout`** and **`stderr`** (data = the log line;
  stderr lines are surfaced with a `[stderr] ` prefix), and the terminal
  event is **`end`** with data `{"status": "completed"|"failed",
  "returncode": int|null}`. The parser accepts sse-starlette's `\r\n` line
  endings / `\r\n\r\n` frame separators (and plain `\n` / `\n\n`), joins
  multi-line `data:` fields with `\n`, and never trims data, so log
  indentation survives. `createSSEParser` is exported and covered by fixture
  tests in `jobs.test.ts`.
- **Weekly runs are chained client-side** by `Pipeline.tsx`: it starts
  `ingest` → `compile` → `promote` in sequence (the stage order of
  `cadences/weekly.sh`), only advancing when the previous job's `end` event
  reports `completed` with rc 0, and reports which stage failed otherwise.
  weekly.sh's final `validate` step is not an allowed job op, so it is not
  part of the GUI chain. "Dry run" starts a single `lint` job.
- The per-entity recompile (`recompileEntity`) is **not** a job: the endpoint
  is synchronous and returns the compile summary dict as its response body.

### API layer (`web/src/api/`)

- `client.ts` — the fetch wrapper: attaches the bearer token, builds URLs
  from `VITE_VAULT_API_URL` (`apiUrl()` — also used by the SSE stream and
  TokenGate), and **auto-clears the stored token on `401`** (but
  deliberately not on `403`, to avoid bouncing a session over a
  permission-scoped error rather than a bad token).
- `hooks.ts` — React Query hooks (`useEntities`, `useEntity`, `useCreds`,
  `useReviewProposals`, `useReviewEntities`, `useRuns`, `useLedgers`,
  `useStatus`, `useAsk`, `useConfig`, `useSchema` — the taxonomy+tags from
  `GET /api/schema`, cached with a long `staleTime` — and `useEntityRaw`
  for `GET /api/entities/:slug/raw`, …) wrapping `client.ts` calls.
- `jobs.ts` — job-run + SSE-stream helpers (see above).
- `mutations.ts` — React Query mutations for the review approve/reject
  actions (entity refs are `type/slug`; each segment is URI-encoded but the
  `/` stays literal to match the backend's `{ref:path}` route),
  `useReclassifyEntity` (`POST /api/entities/:slug/reclassify` with
  `{to_type, to_subtype, reason?}`; invalidates review/entities/status and
  the entity's detail), `usePatchEntity` (`PATCH /api/entities/:slug` with
  a subset of `{title, tags, notes}`; the response is the full updated
  detail and is written straight into the `["entity", slug]` cache),
  `useSaveEntityRaw` (`PUT /api/entities/:slug/raw` with `{content}`), and
  `useApplyConfig` (`POST /api/config/apply` with `{"env": {...}}`; the
  `thresholds` block is read-only server-side and rejected with 400).
- `resolve.ts` — the credential-resolve call used by `Creds.tsx`
  (bearer-token auth only).
- `types.ts` — shared TS types for API payloads (`StatusResponse`,
  `AskResponse`, `Config`, …) matching the live endpoints.

### Auth (`web/src/TokenGate.tsx` + `web/src/store/auth.ts`)

- `auth.ts` is a small Zustand store holding `token` in **`sessionStorage`**
  (key `vault_token`) — cleared when the tab closes, not persisted like the
  UI prefs below.
- `TokenGate` renders a password-style token entry screen when no token is
  stored. On submit it validates the token by calling `GET /api/health`
  (via `apiUrl()`) before storing it and mounting the app. A `401` is
  reported as an invalid token; a `403` as "valid but not authorized for
  this vault". Note `/api/health` itself is unauthenticated server-side.

### State (`web/src/store/`)

- `auth.ts` — token only (see above).
- `windows.ts` — all window-manager state: open windows, per-window
  position/size/z-order/minimized, launcher/command-bar/quick-panel open
  flags, and persisted UI preferences (`desktopScale`, `theme`, `density`),
  each written to its own **`localStorage`** key (`vault.desktopScale`,
  `vault.theme`, …). Theme and density are stamped onto
  `<html data-theme data-density>` on load and on change, which is what
  activates the `theme.css` variable overrides. Note the asymmetry: auth
  token → sessionStorage, UI prefs → localStorage.

### Styling

No CSS framework (no Tailwind/MUI/etc.) and no CSS-in-JS library. Components
are styled with inline `style={}` objects plus a hand-rolled CSS-string
block (`GV_CSS` in `Desktop.tsx`), backed by `web/src/theme.ts` (design
tokens) and `web/src/theme.css` (globals + `[data-theme]`/`[data-density]`
overrides) — a small custom design system.

### Testing

Vitest (`web/vite.config.ts`: `environment: "jsdom"`, `setupFiles:
["./src/test/setup.ts"]`, which just imports `@testing-library/jest-dom`).
`@testing-library/react` + `/user-event` for component tests. Tests are
colocated per-component (`*.test.tsx` / `*.test.ts` next to the file they
cover) across `wm/`, `screens/`, `store/`, `api/`, `ui/`. Run `npm test`
for a one-shot run (what CI does via `.github/workflows/ci.yml`'s
`frontend` job: `npm ci` → `npm test -- --run` → `npm run build`).

## Known gaps (don't be surprised by these)

- No ESLint config, no lint script.
- No router library — navigation is in-memory state via `store/windows.ts` +
  `ScreenRouter`'s `switch`; there's no deep-linking (a page refresh always
  lands back on the `Vault` hub screen).
- No i18n — a previous unmounted `src/i18n/` stub (empty i18next setup) and
  the associated locale selector were removed; UI strings are plain English
  literals. Former dead code (`App.tsx`, `MobileShell.tsx`/`useIsMobile.ts`,
  `TabbedApp.tsx`/`consolidation.ts`) has also been deleted — there is no
  mobile shell and no tabbed-window mode.
- A few QuickPanel glyphs are intentional stubs with "not implemented yet"
  tooltips. (Wiki's "Edit details"/"Open in editor" and Review's
  "Reclassify" are now live — see the screens table above.)
