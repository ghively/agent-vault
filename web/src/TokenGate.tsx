import { useState } from "react";
import { useAuth } from "./store/auth";

export function TokenGate({ children }: { children: React.ReactNode }) {
  const { token, setToken } = useAuth();
  const [input, setInput] = useState("");

  if (token) {
    return <>{children}</>;
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
        <form
          onSubmit={(e) => {
            e.preventDefault();
            if (input.trim()) {
              setToken(input.trim());
            }
          }}
        >
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
            disabled={!input.trim()}
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
            Access Vault
          </button>
        </form>
      </div>
    </div>
  );
}
