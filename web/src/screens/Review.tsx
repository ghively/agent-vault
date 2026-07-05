import React, { useEffect, useRef, useState } from "react";
import { useEntity, useReviewEntities, useReviewProposals, useSchema } from "../api/hooks";
import {
  useApproveProposal, useRejectProposal, useApproveEntity, useRejectEntity,
  useReclassifyEntity,
} from "../api/mutations";
import { useFocusTrap } from "../ui/useFocusTrap";
import { C, FONT_MONO } from "../theme";
import type { ReviewEntity, Proposal } from "../api/types";

// Entity refs are "type/slug" — the slug (everything after the first '/') is
// what the /api/entities/{slug}/* routes want.
const refSlug = (ref: string) => ref.split("/").slice(1).join("/");
const refType = (ref: string) => ref.split("/")[0] ?? "";

type EntityCardProps = {
  entity: ReviewEntity;
  onApprove: () => void;
  onReject: () => void;
  onReclassify: () => void;
  pending: boolean;
};

function EntityCard({ entity, onApprove, onReject, onReclassify, pending }: EntityCardProps) {
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
          onClick={onReclassify}
          disabled={pending}
          style={{
            cursor: pending ? "wait" : "pointer",
            fontSize: 11.5,
            padding: "5px 13px",
            borderRadius: 7,
            border: `1px solid rgba(0,243,255,0.5)`,
            color: C.cyan,
            background: "transparent",
            opacity: pending ? 0.5 : 1,
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
  const reclassify = useReclassifyEntity();

  // Reclassify dialog state + the post-success inline confirmation. The card
  // usually disappears after the invalidation (it's been re-filed), so the
  // notice renders at the top of the entities column, not on the card.
  const [reclassifyTarget, setReclassifyTarget] = useState<ReviewEntity | null>(null);
  const [reclassifyNotice, setReclassifyNotice] = useState<string | null>(null);

  const openReclassify = (entity: ReviewEntity) => {
    reclassify.reset(); // clear any error left over from a previous attempt
    setReclassifyNotice(null);
    setReclassifyTarget(entity);
  };

  const entities: ReviewEntity[] = entitiesData?.items ?? [];
  const proposals: Proposal[] = proposalsData?.items ?? [];

  // Disable a column's buttons while one of its mutations is in flight —
  // prevents double-submitting the same approve/reject.
  const entityPending = approveEntity.isPending || rejectEntity.isPending || reclassify.isPending;
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
          {reclassifyNotice && (
            <div style={{
              color: C.greenSoft,
              fontSize: 12,
              border: `1px solid rgba(0,255,0,0.3)`,
              background: "rgba(0,20,0,0.35)",
              padding: "7px 11px",
              marginBottom: 10,
            }}>
              ✓ {reclassifyNotice}
            </div>
          )}
          {entities.map((entity) => (
            <EntityCard
              key={entity.ref}
              entity={entity}
              pending={entityPending}
              onApprove={() => approveEntity.mutate({ ref: entity.ref })}
              onReject={() => rejectEntity.mutate({ ref: entity.ref })}
              onReclassify={() => openReclassify(entity)}
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

      {reclassifyTarget && (
        <ReclassifyModal
          entity={reclassifyTarget}
          mutation={reclassify}
          onClose={() => setReclassifyTarget(null)}
          onDone={(msg) => {
            setReclassifyNotice(msg);
            setReclassifyTarget(null);
          }}
        />
      )}
    </div>
  );
}

// ── Reclassify dialog ─────────────────────────────────────────────────────────

const FIELD_LABEL_STYLE: React.CSSProperties = {
  display: "block",
  color: C.dim,
  fontSize: 11,
  letterSpacing: 1,
  marginBottom: 4,
  textTransform: "uppercase",
};

const FIELD_INPUT_STYLE: React.CSSProperties = {
  width: "100%",
  boxSizing: "border-box",
  background: "rgba(0,0,0,0.4)",
  border: "1px solid rgba(0,255,0,0.3)",
  borderRadius: 5,
  color: C.text,
  fontFamily: FONT_MONO,
  fontSize: 12,
  padding: "6px 9px",
  outline: "none",
};

type ReclassifyModalProps = {
  entity: ReviewEntity;
  mutation: ReturnType<typeof useReclassifyEntity>;
  onClose: () => void;
  onDone: (msg: string) => void;
};

function ReclassifyModal({ entity, mutation, onClose, onDone }: ReclassifyModalProps) {
  const slug = refSlug(entity.ref);
  const { data: schema } = useSchema();
  // Fetch the detail so "unchanged target" can compare the subtype too — the
  // review card's ref only carries the type.
  const { data: detail } = useEntity(slug);

  const [toType, setToType] = useState("");
  const [toSubtype, setToSubtype] = useState("");
  const [reason, setReason] = useState("");
  const typeRef = useRef<HTMLSelectElement>(null);
  const dialogRef = useRef<HTMLDivElement>(null);
  // Trap Tab focus inside the dialog (it declares aria-modal). Matches the Creds
  // modal; without it Tab escapes to the windows behind the overlay.
  useFocusTrap(dialogRef);

  // Escape closes; initial focus lands on the type select (Creds modal conventions).
  useEffect(() => {
    typeRef.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  const types = schema?.types ?? [];
  const subtypes = types.find((t) => t.type === toType)?.subtypes ?? [];

  const curType = detail?.type ?? refType(entity.ref);
  const curSubtype = detail?.subtype;
  // Same (type, subtype) is not a valid move. Until the detail (and thus the
  // current subtype) has loaded, conservatively treat any same-type pick as
  // unchanged rather than let a 400 through.
  const unchanged = toType === curType && (curSubtype == null || toSubtype === curSubtype);
  const canConfirm = !!toType && !!toSubtype && !unchanged && !mutation.isPending;

  const submit = () => {
    if (!canConfirm) return;
    mutation.mutate(
      { slug, to_type: toType, to_subtype: toSubtype, reason: reason.trim() || undefined },
      {
        onSuccess: (res) =>
          onDone(`${res.slug} re-filed to ${res.to}, ${res.refs_rewritten} refs rewritten`),
        // 400/409 → keep the dialog open; mutation.error renders below.
      },
    );
  };

  return (
    <div
      ref={dialogRef}
      role="dialog"
      aria-modal="true"
      aria-label={`Reclassify ${entity.ref}`}
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(0,0,0,0.75)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        zIndex: 1000,
      }}
    >
      <div style={{
        background: "#0a0a0a",
        border: `1px solid ${C.cyan}`,
        borderRadius: 8,
        padding: "24px 28px",
        minWidth: 380,
        maxWidth: 520,
        fontFamily: FONT_MONO,
        color: C.text,
        fontSize: 13,
      }}>
        <div style={{ color: C.cyan, fontSize: 13, letterSpacing: 1, marginBottom: 4 }}>
          RECLASSIFY
        </div>
        <div style={{ color: C.dim, fontSize: 11.5, marginBottom: 16 }}>
          current: <span style={{ color: C.text }}>{entity.ref}</span>
          {curSubtype ? <span> ({curType}/{curSubtype})</span> : null}
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12, marginBottom: 12 }}>
          <label>
            <span style={FIELD_LABEL_STYLE}>to type</span>
            <select
              ref={typeRef}
              aria-label="target type"
              value={toType}
              onChange={(e) => {
                setToType(e.target.value);
                setToSubtype(""); // subtype options follow the selected type
              }}
              style={FIELD_INPUT_STYLE}
            >
              <option value="">select…</option>
              {types.map((t) => (
                <option key={t.type} value={t.type}>{t.type}</option>
              ))}
            </select>
          </label>
          <label>
            <span style={FIELD_LABEL_STYLE}>to subtype</span>
            <select
              aria-label="target subtype"
              value={toSubtype}
              onChange={(e) => setToSubtype(e.target.value)}
              disabled={!toType}
              style={{ ...FIELD_INPUT_STYLE, opacity: toType ? 1 : 0.5 }}
            >
              <option value="">select…</option>
              {subtypes.map((st) => (
                <option key={st} value={st}>{st}</option>
              ))}
            </select>
          </label>
        </div>

        <label style={{ display: "block", marginBottom: 16 }}>
          <span style={FIELD_LABEL_STYLE}>reason (optional)</span>
          <input
            aria-label="reason"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="why is this being re-filed?"
            style={FIELD_INPUT_STYLE}
          />
        </label>

        {mutation.error != null && (
          <div style={{ color: C.red, fontSize: 11.5, marginBottom: 12, whiteSpace: "pre-wrap" }}>
            {mutation.error instanceof Error ? mutation.error.message : "request failed"}
          </div>
        )}

        <div style={{ display: "flex", gap: 10, justifyContent: "flex-end" }}>
          <button
            onClick={onClose}
            style={{
              padding: "6px 16px",
              borderRadius: 5,
              border: `1px solid ${C.dim}`,
              background: "transparent",
              color: C.dim,
              fontFamily: FONT_MONO,
              fontSize: 12,
              cursor: "pointer",
            }}
          >
            Cancel
          </button>
          <button
            onClick={submit}
            disabled={!canConfirm}
            style={{
              padding: "6px 16px",
              borderRadius: 5,
              border: `1px solid ${C.cyan}`,
              background: "rgba(0,243,255,0.08)",
              color: C.cyan,
              fontFamily: FONT_MONO,
              fontSize: 12,
              cursor: mutation.isPending ? "wait" : canConfirm ? "pointer" : "not-allowed",
              opacity: canConfirm ? 1 : 0.5,
            }}
          >
            {mutation.isPending ? "Reclassifying…" : "Confirm"}
          </button>
        </div>
      </div>
    </div>
  );
}
