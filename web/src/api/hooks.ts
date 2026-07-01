import { useQuery } from "@tanstack/react-query";
import { vaultFetch } from "./client";
import type {
  StatusResponse, EntitiesResponse, EntityDetail, Proposal, ReviewEntity,
  Run, Ledgers, Cred, AskResponse, Config,
} from "./types";

export const useStatus = () =>
  useQuery({ queryKey: ["status"], queryFn: () => vaultFetch<StatusResponse>("/api/status") });

export const useEntities = (q = "", type = "") =>
  useQuery({
    queryKey: ["entities", q, type],
    queryFn: () => vaultFetch<EntitiesResponse>(`/api/entities?q=${encodeURIComponent(q)}&type=${encodeURIComponent(type)}`),
  });

export const useEntity = (slug: string) =>
  useQuery({
    queryKey: ["entity", slug],
    queryFn: () => vaultFetch<EntityDetail>(`/api/entities/${encodeURIComponent(slug)}`),
    enabled: !!slug,
  });

export const useReviewProposals = () =>
  useQuery({ queryKey: ["review", "proposals"], queryFn: () => vaultFetch<{ items: Proposal[] }>("/api/review/proposals") });

export const useReviewEntities = () =>
  useQuery({ queryKey: ["review", "entities"], queryFn: () => vaultFetch<{ items: ReviewEntity[] }>("/api/review/entities") });

export const useRuns = () =>
  useQuery({ queryKey: ["runs"], queryFn: () => vaultFetch<{ runs: Run[] }>("/api/runs") });

export const useLedgers = () =>
  useQuery({ queryKey: ["ledgers"], queryFn: () => vaultFetch<Ledgers>("/api/ledgers") });

export const useCreds = () =>
  useQuery({ queryKey: ["creds"], queryFn: () => vaultFetch<{ items: Cred[] }>("/api/creds") });

export const useAsk = (q: string) =>
  useQuery({ queryKey: ["ask", q], queryFn: () => vaultFetch<AskResponse>(`/api/ask?q=${encodeURIComponent(q)}`), enabled: !!q });

export const useConfig = () =>
  useQuery({ queryKey: ["config"], queryFn: () => vaultFetch<Config>("/api/config") });
