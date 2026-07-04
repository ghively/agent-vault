import React from "react";
import { useReviewEntities, useReviewProposals } from "../api/hooks";
import { useApproveProposal, useRejectProposal, useApproveEntity, useRejectEntity } from "../api/mutations";
import { C, FONT_MONO } from "../theme";
import type { ReviewEntity, Proposal } from "../api/types";

const RECLASSIFY_DISABLED_TITLE = "reclassify from GUI not yet implemented";

type EntityCardProps = {
  entity: ReviewEntity;
  onApprove: () => void;
  onReject: () => void;
  pending: boolean;
};

function EntityCard({ entity, onApprove, onReject, pending }: EntityCardProps) {
  const confPct = typeof entity.confidence === "number"
    ? `${Math.round(entity.confidence * 100)}%`
    : String(entity.confidence);
  const badge = entity.gated ? "GATED" : entity.inferred ? "INFERRED" : "";
  const badgeColor = entity.gated ? C.amber : C.dim;

  return (
    <div style={{
      border: `1px solid rgba(255,95,86,0.4)`,
      background: "rgba(0,0,0,0.45)",
      padding: "11px 13px",
      marginBottom: 10,
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4 }}>
        <span style={{ color: C.text, fontSize: 13 }}>{entity.ref}</span>
        <span style={{ color: C.dim, fontSize: 11 }}>{entity.title}</span>
        {badge && (
          <span style={{ marginLeft: "auto", color: badgeColor, fontSize: 11 }}>{badge}</span>
        )}
      </div>
      <div style={{ color: C.dim, fontSize: 11.5, lineHeight: 1.5, marginBottom: 9 }}>
        confidence: {confPct}
      </div>
      <div style={{ display: "flex", gap: 7 }}>
        <button
          onClick={onApprove}
          disabled={pending}
          style={{
            cursor: pending ? "wait" : "pointer",
            fontSize: 11.5,
            padding: "5px 13px",
            borderRadius: 7,
            border: `1px solid ${C.greenSoft}`,
            color: C.greenSoft,
            background: "transparent",
            opacity: pending ? 0.5 : 1,
          }}
        >
          Approve
        </button>
        <button
          disabled
          title={RECLASSIFY_DISABLED_TITLE}
          style={{
            cursor: "not-allowed",
            fontSize: 11.5,
            padding: "5px 13px",
            borderRadius: 7,
            border: `1px solid rgba(0,243,255,0.5)`,
            color: C.cyan,
            background: "transparent",
            opacity: 0.5,
          }}
        >
          Reclassify
        </button>
        <button
          onClick={onReject}
          disabled={pending}
          style={{
            cursor: pending ? "wait" : "pointer",
            fontSize: 11.5,
            padding: "5px 13px",
            borderRadius: 7,
            border: `1px solid rgba(255,95,86,0.5)`,
            color: C.red,
            background: "transparent",
            opacity: pending ? 0.5 : 1,
          }}
        >
          Reject
        </button>
      </div>
    </div>
  );
}

type ProposalCardProps = {
  proposal: Proposal;
  onApprove: () => void;
  onReject: () => void;
  pending: boolean;
};

function ProposalCard({ proposal, onApprove, onReject, pending }: ProposalCardProps) {
  return (
    <div style={{
      border: `1px solid rgba(128,0,255,0.4)`,
      background: "rgba(0,0,0,0.45)",
      padding: "11px 13px",
      marginBottom: 10,
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 5 }}>
        <span style={{
          fontSize: 10,
          padding: "1px 7px",
          border: `1px solid ${C.purple}`,
          color: C.purple,
          letterSpacing: 1,
        }}>
          {proposal.kind}
        </span>
        <span style={{ color: C.text, fontSize: 13 }}>{proposal.concept}</span>
        <span style={{ marginLeft: "auto", color: C.dim, fontSize: 11 }}>
          {proposal.sightings} sightings
        </span>
      </div>
      <div style={{ color: C.dim, fontSize: 11.5, lineHeight: 1.5, marginBottom: 9 }}>
        {proposal.reason}
      </div>
      <div style={{ display: "flex", gap: 7, alignItems: "center" }}>
        <button
          onClick={onApprove}
          disabled={pending}
          style={{
            cursor: pending ? "wait" : "pointer",
            fontSize: 11.5,
            padding: "5px 13px",
            borderRadius: 7,
            border: `1px solid ${C.greenSoft}`,
            color: C.greenSoft,
            background: "transparent",
            opacity: pending ? 0.5 : 1,
          }}
        >
          Approve
        </button>
        <button
          onClick={onReject}
          disabled={pending}
          style={{
            cursor: pending ? "wait" : "pointer",
            fontSize: 11.5,
            padding: "5px 13px",
            borderRadius: 7,
            border: `1px solid rgba(255,95,86,0.5)`,
            color: C.red,
            background: "transparent",
            opacity: pending ? 0.5 : 1,
          }}
        >
          Reject
        </button>
      </div>
    </div>
  );
}

export function Review() {
  const { data: entitiesData, isLoading: entitiesLoading, isError: entitiesError } = useReviewEntities();
  const { data: proposalsData, isLoading: proposalsLoading, isError: proposalsError } = useReviewProposals();

  const approveProposal = useApproveProposal();
  const rejectProposal = useRejectProposal();
  const approveEntity = useApproveEntity();
  const rejectEntity = useRejectEntity();

  const entities: ReviewEntity[] = entitiesData?.items ?? [];
  const proposals: Proposal[] = proposalsData?.items ?? [];

  // Disable a column's buttons while one of its mutations is in flight —
  // prevents double-submitting the same approve/reject.
  const entityPending = approveEntity.isPending || rejectEntity.isPending;
  const proposalPending = approveProposal.isPending || rejectProposal.isPending;
  const entityError = approveEntity.error ?? rejectEntity.error;
  const proposalError = approveProposal.error ?? rejectProposal.error;
  const errText = (e: unknown) => (e instanceof Error ? e.message : "request failed");

  return (
    <div style={{ padding: "12px 16px", color: C.text, fontFamily: FONT_MONO, fontSize: 12 }}>
      {/* Header */}
      <div style={{
        color: C.cyan,
        fontSize: 18,
        textShadow: `0 0 6px ${C.cyan}`,
        borderBottom: `2px solid ${C.purple}`,
        display: "inline-block",
        paddingRight: 24,
        paddingBottom: 4,
        marginBottom: 6,
      }}>
        Human Review: Approve / Reject
      </div>
      <div style={{ color: C.dim, fontSize: 12, marginBottom: 18 }}>
        the LLM proposes · a human decides · deterministic code commits
      </div>

      {(entitiesError || proposalsError) && (
        <div style={{ color: C.red, fontSize: 12, marginBottom: 14 }}>
          error — could not load the review queue (/api/review)
        </div>
      )}
      {!entitiesError && !proposalsError && (entitiesLoading || proposalsLoading) && (
        <div style={{ color: C.dim, fontSize: 12, marginBottom: 14 }}>loading review queue…</div>
      )}

      {/* Two-column layout */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 20 }}>
        {/* Left: Needs-review entities */}
        <div>
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 10 }}>
            <span style={{ color: C.red, fontSize: 13, letterSpacing: 1 }}>⚑ NEEDS-REVIEW ENTITIES</span>
            <span style={{
              color: C.red,
              fontSize: 11,
              border: `1px solid rgba(255,95,86,0.5)`,
              padding: "0 7px",
            }}>
              {entities.length}
            </span>
          </div>
          {entityError != null && (
            <div style={{ color: C.red, fontSize: 12, marginBottom: 10 }}>
              error — {errText(entityError)}
            </div>
          )}
          {entities.map((entity) => (
            <EntityCard
              key={entity.ref}
              entity={entity}
              pending={entityPending}
              onApprove={() => approveEntity.mutate({ ref: entity.ref })}
              onReject={() => rejectEntity.mutate({ ref: entity.ref })}
            />
          ))}
          {entities.length === 0 && (
            <div style={{ color: C.dim, fontSize: 12 }}>no entities pending review</div>
          )}
        </div>

        {/* Right: Queued proposals */}
        <div>
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 10 }}>
            <span style={{ color: C.cyan, fontSize: 13, letterSpacing: 1 }}>⌥ QUEUED PROPOSALS</span>
            <span style={{
              color: C.cyan,
              fontSize: 11,
              border: `1px solid rgba(0,243,255,0.5)`,
              padding: "0 7px",
            }}>
              {proposals.length}
            </span>
            <span style={{ marginLeft: "auto", color: C.dim, fontSize: 11 }}>registry vocabulary</span>
          </div>
          {proposalError != null && (
            <div style={{ color: C.red, fontSize: 12, marginBottom: 10 }}>
              error — {errText(proposalError)}
            </div>
          )}
          {proposals.map((proposal) => (
            <ProposalCard
              key={proposal.id}
              proposal={proposal}
              pending={proposalPending}
              onApprove={() => approveProposal.mutate({ id: proposal.id })}
              onReject={() => rejectProposal.mutate({ id: proposal.id })}
            />
          ))}
          {proposals.length === 0 && (
            <div style={{ color: C.dim, fontSize: 12 }}>no proposals queued</div>
          )}
        </div>
      </div>
    </div>
  );
}
