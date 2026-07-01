import React, { useState } from "react";
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

  const handleReveal = async (slug: string, token: string) => {
    setResolveState({ slug, secret: null, error: null, loading: true });
    try {
      const secret = await resolveSecret(slug, token);
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

      {/* Resolve-output panel */}
      <div style={{
        marginTop: 18,
        border: `1px solid rgba(0,243,255,0.3)`,
        background: "rgba(0,0,0,0.5)",
        padding: "13px 15px",
        fontSize: 12.5,
        lineHeight: 1.85,
        color: C.dim,
      }}>
        <div style={{ color: C.purple, fontSize: 12, letterSpacing: 1, marginBottom: 8, textShadow: `0 0 6px ${C.purple}` }}>
          &gt; synapse resolve bofa-checking
        </div>
        <div style={{ color: "var(--ns-dim2)" }}># Bank of America &lt;- age://banking/bofa-login</div>
        <div style={{ color: "var(--ns-dim2)" }}># secret below; never stored — handle with care · stderr context, stdout secret</div>
        <div style={{ color: C.greenSoft, textShadow: "0 0 6px rgba(39,201,63,0.4)" }}>
          (run resolve to reveal)
        </div>
      </div>

      {/* Re-auth modal */}
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
  onReveal: (slug: string, token: string) => Promise<void>;
  onClose: () => void;
}

function ResolveModal({ slug, state, onReveal, onClose }: ResolveModalProps) {
  const [token, setToken] = useState("");
  const [copied, setCopied] = useState<"idle" | "ok" | "fail">("idle");

  const handleConfirm = async () => {
    const t = token;
    setToken(""); // clear token immediately — not stored
    await onReveal(slug, t);
  };

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
        fontFamily: FONT_MONO,
        color: C.text,
        fontSize: 13,
      }}>
        <div style={{ color: C.amber, marginBottom: 14, fontSize: 12, display: "flex", gap: 8 }}>
          <span>⚠</span>
          <span>The secret will be shown once and is never stored. Close this dialog to clear it.</span>
        </div>

        {!state?.secret && (
          <>
            <label
              htmlFor="resolve-token"
              style={{ display: "block", color: C.dim, fontSize: 11, marginBottom: 6, letterSpacing: 1 }}
            >
              Access Token
            </label>
            <input
              id="resolve-token"
              type="password"
              value={token}
              onChange={(e) => setToken(e.target.value)}
              placeholder="re-enter your access token"
              autoComplete="off"
              data-1p-ignore="true"
              data-lpignore="true"
              data-bwignore="true"
              data-form-type="other"
              style={{
                width: "100%",
                background: "rgba(0,0,0,0.6)",
                border: `1px solid ${C.purple}`,
                borderRadius: 4,
                color: C.text,
                fontFamily: FONT_MONO,
                fontSize: 12,
                padding: "6px 10px",
                outline: "none",
                boxSizing: "border-box",
                marginBottom: 16,
              }}
            />

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
                onClick={handleConfirm}
                disabled={!token || state?.loading === true}
                style={{
                  padding: "6px 16px",
                  borderRadius: 5,
                  border: `1px solid ${C.cyan}`,
                  background: "rgba(0,243,255,0.08)",
                  color: C.cyan,
                  fontFamily: FONT_MONO,
                  fontSize: 12,
                  cursor: "pointer",
                }}
              >
                {state?.loading ? "Resolving..." : "Reveal"}
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
