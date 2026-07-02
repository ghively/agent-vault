import { type AppId } from "../wm/apps";
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
  switch (app) {
    case "browse":
      return <Browse />;
    case "wiki":
      return <Wiki />;
    case "vault":
      // Vault expects onNavigate but we're in WM mode - stub it
      return <Vault onNavigate={() => {}} />;
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
