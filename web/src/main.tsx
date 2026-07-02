import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import React from "react";
import ReactDOM from "react-dom/client";
import { Desktop } from "./wm/Desktop";
import { TokenGate } from "./TokenGate";
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
      <TokenGate>
        <Desktop />
      </TokenGate>
    </QueryClientProvider>
  </React.StrictMode>
);
