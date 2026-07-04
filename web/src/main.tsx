import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import React from "react";
import ReactDOM from "react-dom/client";
import { Desktop } from "./wm/Desktop";
import { TokenGate } from "./TokenGate";
import { ErrorBoundary } from "./ui/ErrorBoundary";
import "./theme.css";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      refetchOnWindowFocus: false,
      retry: 1,
    },
  },
});

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      {/* F1: a top-level boundary so a render throw in the shell chrome
          (Waybar/Launcher/CommandBar/Desktop) shows a recoverable panel
          instead of white-screening the whole app. */}
      <ErrorBoundary title="Agent Vault hit an error" reloadOnFail>
        <TokenGate>
          <Desktop />
        </TokenGate>
      </ErrorBoundary>
    </QueryClientProvider>
  </React.StrictMode>
);
