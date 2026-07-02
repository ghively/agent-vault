import { type AppId } from "../wm/apps";
import { useWindows } from "../store/windows";
import { Browse } from "./Browse";
import { Wiki } from "./Wiki";
import { Vault } from "./Vault";
import { Creds } from "./Creds";
import { Review } from "./Review";
import { Pipeline } from "./Pipeline";
import { CommandDeck } from "./CommandDeck";

interface ScreenRouterProps {
  app: AppId;
}

export function ScreenRouter({ app }: ScreenRouterProps) {
  const openApp = useWindows((s) => s.openApp);
  switch (app) {
    case "browse":
      return <Browse />;
    case "wiki":
      return <Wiki />;
    case "vault":
      // Vault hub tiles navigate by opening the target app in its own window.
      return <Vault onNavigate={(id) => openApp(id as AppId)} />;
    case "creds":
      return <Creds />;
    case "review":
      return <Review />;
    case "pipeline":
      return <Pipeline />;
    case "command":
      return <CommandDeck />;
    default:
      return null;
  }
}
