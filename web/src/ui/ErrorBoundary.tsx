import React from "react";
import { C, FONT_UI, FONT_MONO } from "../theme";

interface Props {
  children: React.ReactNode;
  /** When this value changes, the boundary clears its error (e.g. the app id). */
  resetKey?: string | number;
}
interface State {
  error: Error | null;
}

/**
 * Catches render errors from a single screen so one broken screen shows a
 * recoverable panel instead of crashing the whole PHOSPHOR shell.
 */
export class ErrorBoundary extends React.Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidUpdate(prev: Props) {
    if (prev.resetKey !== this.props.resetKey && this.state.error) {
      this.setState({ error: null });
    }
  }

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;
    return (
      <div
        role="alert"
        style={{
          height: "100%",
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          gap: 12,
          padding: 24,
          fontFamily: FONT_UI,
          color: C.text,
          textAlign: "center",
        }}
      >
        <div style={{ color: C.red, fontSize: 16 }}>this screen hit an error</div>
        <div style={{ color: C.dim, fontFamily: FONT_MONO, fontSize: 12, maxWidth: 480, wordBreak: "break-word" }}>
          {error.message || String(error)}
        </div>
        <button
          onClick={() => this.setState({ error: null })}
          style={{
            cursor: "pointer",
            border: `1px solid ${C.green}`,
            background: "transparent",
            color: C.green,
            padding: "6px 16px",
            fontFamily: FONT_UI,
          }}
        >
          retry
        </button>
      </div>
    );
  }
}
