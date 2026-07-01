export interface DueItem { slug: string; title: string; date: string; days: number }
export interface ExpItem extends DueItem { field: "expires" | "renews" }
export interface Run { cadence: string; ts: string; rc: number; duration_s: number; detail: string }
export interface StatusResponse {
  counts: { total: number; compiled: number; needs_review: number };
  due: DueItem[];
  expiring: ExpItem[];
  breakdown: Record<string, number>;
  last_run: Run | null;
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
}
export interface Proposal { id: string; kind: string; concept: string; sightings: number; reason: string }
export interface ReviewEntity { ref: string; title: string; confidence: number | string; gated: boolean; inferred: boolean }
export interface Ledgers { proposals: number; promoted: number; manifest: number }
export interface Cred { slug: string; title: string; ref: string; backend: string }
export interface AskResultItem {
  slug?: string; title?: string; type?: string; date?: string;
  [key: string]: unknown;
}
export interface AskResponse { intent: "due" | "expiring" | "find"; items: AskResultItem[] }
export interface Config {
  env: Record<string, string>;
  thresholds: { new_tag_min: number | null; new_subtype_min: number | null; new_type_locked: boolean };
  resolvers: { name: string; detail: string }[];
}
