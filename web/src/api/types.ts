export interface DueItem { slug: string; title: string; date: string; days: number }
export interface ExpItem extends DueItem { field: "expires" | "renews" }
export interface Run { cadence: string; ts: string; rc: number; duration_s: number; detail: string }
export interface IndexStatus { generated: string | null; count: number; stale: boolean }
export interface StatusResponse {
  counts: { total: number; compiled: number; needs_review: number };
  due: DueItem[];
  expiring: ExpItem[];
  breakdown: Record<string, number>;
  last_run: Run | null;
  index?: IndexStatus;
}
export interface EntityRow {
  slug: string; ts: string; type: string; subtype: string;
  status: string; confidence: number | string; tags: string[];
}
export interface EntitiesResponse { rows: EntityRow[]; total: number }
export interface FactRow { k: string; v: string }
export interface LinkRow { label: string; ref: string; title: string; exists: boolean }
export interface EntityDetail {
  slug: string; title: string; type: string; subtype: string;
  status: string; confidence: number | string; hash: string;
  prose: string; sources: string[]; facts: FactRow[]; links: LinkRow[];
  // Optional — not guaranteed by GET /api/entities/{slug} today (facts are
  // built from a fixed field list that excludes both). The PATCH endpoint
  // accepts/returns them, so keep them optional here.
  tags?: string[]; notes?: string;
}
/** Body for PATCH /api/entities/{slug} — send only the keys that changed. */
export interface EntityPatchBody { title?: string; tags?: string[]; notes?: string }
export interface Proposal { id: string; kind: string; concept: string; sightings: number; reason: string }
export interface ReviewEntity { ref: string; title: string; confidence: number | string; gated: boolean; inferred: boolean }
export interface Ledgers { proposals: number; promoted: number; manifest: number }
export interface Cred { slug: string; title: string; ref: string; backend: string }
export interface AskResultItem {
  slug?: string; title?: string; type?: string; date?: string;
  [key: string]: unknown;
}
export interface AskResponse { intent: "due" | "expiring" | "find"; items: AskResultItem[] }
/** GET /api/schema — the registry taxonomy (types → subtypes) + valid tags. */
export interface SchemaType { type: string; subtypes: string[] }
export interface SchemaResponse { types: SchemaType[]; tags: string[] }
/** POST /api/entities/{slug}/reclassify response. */
export interface ReclassifyResponse {
  slug: string; from: string; to: string;
  moved: boolean; refs_rewritten: number; detail: string;
}
/** GET /api/entities/{slug}/raw response. */
export interface EntityRaw { path: string; content: string }
/** PUT /api/entities/{slug}/raw response. */
export interface SaveRawResponse { ok: boolean; path: string }
export interface Config {
  env: Record<string, string>;
  thresholds: { new_tag_min: number | null; new_subtype_min: number | null; new_type_locked: boolean };
  resolvers: { name: string; detail: string }[];
}
