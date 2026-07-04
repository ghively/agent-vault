import React, { useEffect, useRef, useState } from "react";
import { useCreds } from "../api/hooks";
import { resolveSecret } from "../api/resolve";
import { C, FONT_MONO } from "../theme";
import type { Cred } from "../api/types";

interface ResolveState {
  slug: string;
  secret: string | null;
  error: string | null;
  loading: boolean;
}

export function Creds() {
  const { data, isLoading, isError } = useCreds();
  const items: Cred[] = data?.items ?? [];

  // Modal state
  const [modalSlug, setModalSlug] = useState<string | null>(null);
  const [resolveState, setResolveState] = useState<ResolveState | null>(null);

  const openModal = (slug: string) => {
    setModalSlug(slug);
    setResolveState(null);
  };

  const closeModal = () => {
    setModalSlug(null);
    setResolveState(null);
  };

  const handleReveal = async (slug: string) => {
    setResolveState({ slug, secret: null, error: null, loading: true });
    try {
      const secret = await resolveSecret(slug);
      setResolveState({ slug, secret, error: null, loading: false });
    } catch (e) {
      const msg = e instanceof Error ? e.message : "unknown error";
      setResolveState({ slug, secret: null, error: msg, loading: false });
    }
  };

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
        Credential References
      </div>

      {/* Subtitle */}
      <div style={{ color: C.dim, fontSize: 12, marginBottom: 8 }}>
        scheme://store/path — the reference is recorded, the secret never is.
      </div>

      {/* Warning banner */}
      <div style={{
        border: `1px solid rgba(255,189,46,0.4)`,
        background: "rgba(20,14,0,0.35)",
        padding: "9px 13px",
        fontSize: 11.5,
        color: C.amber,
        marginBottom: 18,
        display: "flex",
        alignItems: "center",
        gap: 8,
      }}>
        <span style={{ fontSize: 13 }}>⚠</span>
        Plaintext is never stored in the vault. `resolve` fetches on demand from the external store and prints once to stdout.
      </div>

      {/* Table */}
      <div style={{ border: "1px solid rgba(0,255,0,0.25)" }}>
        {/* Column headers */}
        <div style={{
          display: "flex",
          gap: 12,
          padding: "8px 14px",
          background: "rgba(128,0,255,0.18)",
          borderBottom: `1px solid ${C.purple}`,
          fontSize: 11,
          letterSpacing: 1,
          color: C.text,
        }}>
          <span style={{ width: 210 }}>ENTITY</span>
          <span style={{ width: 250 }}>REFERENCE</span>
          <span style={{ width: 100 }}>BACKEND</span>
          <span style={{ flex: 1 }}>RESOLVED</span>
        </div>

        {/* Error / loading states */}
        {isError && (
          <div style={{ padding: "18px 14px", color: C.red, fontSize: 12 }}>
            error — could not reach /api/creds
          </div>
        )}
        {!isError && isLoading && (
          <div style={{ padding: "18px 14px", color: C.dim, fontSize: 12 }}>loading…</div>
        )}

        {/* Rows */}
        {!isError && !isLoading && items.map((c) => (
          <div
            key={c.slug}
            style={{
              display: "flex",
              gap: 12,
              padding: "9px 14px",
              borderBottom: "1px dashed rgba(0,255,0,0.13)",
              fontSize: 12,
              alignItems: "center",
            }}
          >
            <span style={{ width: 210, color: C.text }}>{c.title}</span>
            <span style={{ width: 250, color: C.greenSoft }}>{c.ref}</span>
            <span style={{ width: 100, color: C.cyan }}>{c.backend}</span>
            <span style={{ flex: 1, display: "flex", gap: 9, alignItems: "center" }}>
              <button
                onClick={() => openModal(c.slug)}
                style={{
                  cursor: "pointer",
                  fontSize: 11,
                  padding: "4px 11px",
                  borderRadius: 6,
                  border: `1px solid ${C.cyan}`,
                  color: C.cyan,
                  background: "transparent",
                  fontFamily: FONT_MONO,
                }}
              >
                Resolve
              </button>
              <span style={{ color: C.dim, fontSize: 12, letterSpacing: 1 }}>
                (run resolve to reveal)
              </span>
            </span>
          </div>
        ))}

        {/* Empty state */}
        {!isError && !isLoading && items.length === 0 && (
          <div style={{ padding: "18px 14px", color: C.dim, fontSize: 12 }}>
            no credentials registered
          </div>
        )}
      </div>

      {/* Resolve confirmation modal */}
      {modalSlug && (
        <ResolveModal
          slug={modalSlug}
          state={resolveState}
          onReveal={handleReveal}
          onClose={closeModal}
        />
      )}
    </div>
  );
}

interface ResolveModalProps {
  slug: string;
  state: ResolveState | null;
  onReveal: (slug: string) => Promise<void>;
  onClose: () => void;
}

function ResolveModal({ slug, state, onReveal, onClose }: ResolveModalProps) {
  const [copied, setCopied] = useState<"idle" | "ok" | "fail">("idle");
  const confirmRef = useRef<HTMLButtonElement>(null);

  // Escape closes (and clears any revealed secret); focus the confirm button
  // when the dialog opens.
  useEffect(() => {
    confirmRef.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  const handleCopy = async () => {
    if (!state?.secret) return;
    try {
      await navigator.clipboard.writeText(state.secret);
      setCopied("ok");
    } catch {
      // Clipboard API can reject (permission denied, insecure context).
      setCopied("fail");
    }
  };

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={`Resolve secret for ${slug}`}
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
        {!state?.secret && (
          <>
            <div style={{ color: C.amber, marginBottom: 14, fontSize: 12, display: "flex", gap: 8 }}>
              <span>⚠</span>
              <span>
                The plaintext secret for <span style={{ color: C.cyan }}>{slug}</span> will be
                fetched from its external store and displayed on this screen. Anyone who can see
                your display will be able to read it. It is shown once and never stored — close
                the dialog to clear it.
              </span>
            </div>

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
                ref={confirmRef}
                onClick={() => onReveal(slug)}
                disabled={state?.loading === true}
                style={{
                  padding: "6px 16px",
                  borderRadius: 5,
                  border: `1px solid ${C.cyan}`,
                  background: "rgba(0,243,255,0.08)",
                  color: C.cyan,
                  fontFamily: FONT_MONO,
                  fontSize: 12,
                  cursor: state?.loading ? "wait" : "pointer",
                }}
              >
                {state?.loading ? "Resolving..." : "Reveal secret"}
              </button>
            </div>

            {state?.error && (
              <div style={{ color: "#ff4444", fontSize: 11, marginTop: 10 }}>
                Error: {state.error}
              </div>
            )}
          </>
        )}

        {state?.secret && (
          <>
            <div style={{ color: C.amber, marginBottom: 14, fontSize: 12, display: "flex", gap: 8 }}>
              <span>⚠</span>
              <span>Shown once, never stored. Close this dialog to clear it.</span>
            </div>
            <div style={{ color: C.greenSoft, fontFamily: FONT_MONO, fontSize: 14, marginBottom: 14, wordBreak: "break-all" }}>
              {state.secret}
            </div>
            <div style={{ display: "flex", gap: 10, justifyContent: "flex-end" }}>
              <button
                onClick={handleCopy}
                style={{
                  padding: "6px 16px",
                  borderRadius: 5,
                  border: `1px solid ${C.purple}`,
                  background: "transparent",
                  color: C.purple,
                  fontFamily: FONT_MONO,
                  fontSize: 12,
                  cursor: "pointer",
                }}
              >
                {copied === "ok" ? "Copied ✓" : copied === "fail" ? "Copy failed" : "Copy"}
              </button>
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
                Hide
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
