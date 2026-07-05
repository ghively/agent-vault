import { useState } from "react";
import { useAuth } from "./store/auth";
import { apiUrl } from "./api/client";

export function TokenGate({ children }: { children: React.ReactNode }) {
  const { token, setToken } = useAuth();
  const [input, setInput] = useState("");
  const [err, setErr] = useState("");
  const [checking, setChecking] = useState(false);

  if (token) {
    return <>{children}</>;
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const t = input.trim();
    if (!t || checking) return;
    setChecking(true);
    setErr("");
    // Validate the token against the API BEFORE trusting it — a wrong/stale
    // token otherwise mounts the app, 401s on the first call, and bounces back
    // here with no explanation.
    //
    // Probe an AUTHENTICATED endpoint (/api/status), not /api/health: health is
    // deliberately open (no auth dependency), so it returns 200 for any token —
    // including an invalid one — which would defeat this gate. /api/status is
    // read-scoped, so it returns 401 for a bad token and 403 for a token that
    // lacks read scope. On an open vault (no tokens configured) it returns 200,
    // which correctly admits the user.
    try {
      const r = await fetch(apiUrl("/api/status"), { headers: { Authorization: `Bearer ${t}` } });
      if (r.ok) {
        setToken(t);
      } else if (r.status === 401) {
        setErr("Invalid token — please re-check it.");
      } else if (r.status === 403) {
        setErr("Token is valid but not authorized for this vault.");
      } else {
        setErr(`Unexpected response (${r.status}).`);
      }
    } catch {
      setErr("Can't reach the vault service.");
    } finally {
      setChecking(false);
    }
  }

  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        height: "100vh",
        backgroundColor: "#040804",
        color: "#e0e0e0",
        fontFamily: "system-ui, sans-serif",
      }}
    >
      <div
        style={{
          background: "rgba(255,255,255,0.02)",
          border: "1px solid rgba(255,255,255,0.1)",
          borderRadius: 12,
          padding: 40,
          width: 400,
          textAlign: "center",
        }}
      >
        <h1 style={{ fontSize: 24, fontWeight: 700, color: "#00f3ff", marginBottom: 20, marginTop: 0 }}>
          Agent Vault
        </h1>
        <p style={{ fontSize: 14, color: "#888", marginBottom: 30 }}>
          Enter your vault token to continue
        </p>
        <form onSubmit={submit}>
          <input
            type="password"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="Enter vault token..."
            style={{
              width: "100%",
              padding: "12px",
              background: "rgba(0,0,0,0.4)",
              border: "1px solid rgba(0,255,0,0.3)",
              borderRadius: 6,
              color: "#e0e0e0",
              fontSize: 14,
              outline: "none",
              boxSizing: "border-box",
              marginBottom: 20,
            }}
            autoFocus
          />
          <button
            type="submit"
            disabled={!input.trim() || checking}
            style={{
              width: "100%",
              padding: "12px",
              background: input.trim() ? "rgba(0,243,255,0.12)" : "rgba(0,243,255,0.04)",
              border: `1px solid ${input.trim() ? "#00f3ff" : "rgba(0,243,255,0.3)"}`,
              borderRadius: 6,
              color: input.trim() ? "#00f3ff" : "#666",
              fontSize: 14,
              fontWeight: 600,
              cursor: input.trim() ? "pointer" : "not-allowed",
              transition: "all 0.15s",
            }}
          >
            {checking ? "Checking…" : "Access Vault"}
          </button>
          {err && (
            <div style={{ marginTop: 16, color: "#ff5f6d", fontSize: 13 }}>{err}</div>
          )}
        </form>
      </div>
    </div>
  );
}
