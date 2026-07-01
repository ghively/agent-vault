import { useState } from "react";
import { Browse } from "./screens/Browse";
import { Wiki } from "./screens/Wiki";
import { Vault } from "./screens/Vault";
import { Creds } from "./screens/Creds";
import { Review } from "./screens/Review";
import { Pipeline } from "./screens/Pipeline";
import { CommandDeck } from "./screens/CommandDeck";

const SCREENS = [
  { id: "browse", label: "Browse", component: Browse },
  { id: "wiki", label: "Wiki", component: Wiki },
  { id: "vault", label: "Vault", component: Vault },
  { id: "creds", label: "Credentials", component: Creds },
  { id: "review", label: "Review", component: Review },
  { id: "pipeline", label: "Pipeline", component: Pipeline },
  { id: "commanddeck", label: "Command Deck", component: CommandDeck },
];

function App() {
  const [activeScreen, setActiveScreen] = useState("vault"); // Start at vault hub

  const renderScreen = () => {
    switch (activeScreen) {
      case "vault":
        return <Vault onNavigate={setActiveScreen} />;
      case "browse":
        return <Browse />;
      case "wiki":
        return <Wiki />;
      case "creds":
        return <Creds />;
      case "review":
        return <Review />;
      case "pipeline":
        return <Pipeline />;
      case "commanddeck":
        return <CommandDeck />;
      default:
        return <Vault onNavigate={setActiveScreen} />;
    }
  };

  return (
    <div style={{ display: "flex", height: "100vh", backgroundColor: "#040804", color: "#e0e0e0", fontFamily: "system-ui, sans-serif" }}>
      {/* Sidebar nav */}
      <nav style={{ width: 200, borderRight: "1px solid rgba(255,255,255,0.1)", padding: "20px 0", display: "flex", flexDirection: "column" }}>
        <div style={{ padding: "0 20px 20px", borderBottom: "1px solid rgba(255,255,255,0.1)", marginBottom: 20 }}>
          <h1 style={{ fontSize: 20, fontWeight: 700, color: "#00f3ff", margin: 0 }}>Agent Vault</h1>
        </div>
        {SCREENS.map((screen) => (
          <button
            key={screen.id}
            onClick={() => setActiveScreen(screen.id)}
            style={{
              padding: "10px 20px",
              border: "none",
              background: activeScreen === screen.id ? "rgba(0,243,255,0.1)" : "transparent",
              color: activeScreen === screen.id ? "#00f3ff" : "#888",
              cursor: "pointer",
              textAlign: "left",
              fontSize: 14,
              fontWeight: activeScreen === screen.id ? 600 : 400,
              transition: "all 0.15s",
            }}
            onMouseEnter={(e) => {
              if (activeScreen !== screen.id) {
                e.currentTarget.style.color = "#00f3ff";
              }
            }}
            onMouseLeave={(e) => {
              if (activeScreen !== screen.id) {
                e.currentTarget.style.color = "#888";
              }
            }}
          >
            {screen.label}
          </button>
        ))}
      </nav>
      {/* Main content */}
      <main style={{ flex: 1, overflow: "auto", padding: 0 }}>
        {renderScreen()}
      </main>
    </div>
  );
}

export default App;
