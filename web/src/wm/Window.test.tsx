import { render, screen, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { createRef } from "react";
import { Window } from "./Window";
import { useWindows } from "../store/windows";
import { APPS } from "./apps";

// A deterministic desktop ref: jsdom gives detached nodes a 0x0 box, which would
// make the drag/resize clamp math collapse. Supply a real 1280x720 geometry.
function deskRef() {
  const ref = createRef<HTMLDivElement>();
  (ref as { current: unknown }).current = {
    getBoundingClientRect: () => ({ left: 0, top: 0, width: 1280, height: 720 }),
    clientWidth: 1280,
    clientHeight: 720,
  };
  return ref as React.RefObject<HTMLDivElement>;
}

function renderWindow(app: "vault" = "vault") {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
  });
  const ref = deskRef();
  const utils = render(
    <QueryClientProvider client={qc}>
      <Window app={app} deskRef={ref} />
    </QueryClientProvider>
  );
  return utils;
}

describe("Window", () => {
  beforeEach(() => {
    useWindows.getState().reset();          // open: ["vault"], pos.vault {60,40}
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new Error("no network in test"));
  });
  afterEach(() => vi.restoreAllMocks());

  it("renders the title bar with the app title and traffic lights", () => {
    renderWindow();
    expect(screen.getByText(APPS.vault.title)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Close/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Minimize/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Maximize or restore/i })).toBeInTheDocument();
  });

  it("close button removes the app from the open set", async () => {
    renderWindow();
    await userEvent.click(screen.getByRole("button", { name: /Close/i }));
    expect(useWindows.getState().open).not.toContain("vault");
  });

  it("minimize hides the window (renders null) and sets minimized state", async () => {
    renderWindow();
    await userEvent.click(screen.getByRole("button", { name: /Minimize/i }));
    expect(useWindows.getState().minimized.vault).toBe(true);
    // minimized window renders nothing — the title is gone
    expect(screen.queryByText(APPS.vault.title)).not.toBeInTheDocument();
  });

  it("dragging the title bar updates the window position in the store", () => {
    renderWindow();
    const title = screen.getByText(APPS.vault.title).parentElement as HTMLElement;
    // start drag at (100,80): dx = 100-60 = 40, dy = 80-40 = 40
    fireEvent.mouseDown(title, { clientX: 100, clientY: 80 });
    // move to (200,180): x = 200-40 = 160, y = 180-40 = 140
    fireEvent.mouseMove(document, { clientX: 200, clientY: 180 });
    fireEvent.mouseUp(document);
    expect(useWindows.getState().pos.vault).toEqual({ x: 160, y: 140 });
  });

  it("dragging clamps so the window stays grabbable at the left edge", () => {
    renderWindow();
    const title = screen.getByText(APPS.vault.title).parentElement as HTMLElement;
    fireEvent.mouseDown(title, { clientX: 100, clientY: 80 });     // dx=40, dy=40
    // drag far left: raw x = -900-40 = -940, clamped to -(w - EDGE_MARGIN)
    fireEvent.mouseMove(document, { clientX: -900, clientY: 80 });
    fireEvent.mouseUp(document);
    const x = useWindows.getState().pos.vault!.x;
    expect(x).toBe(-(APPS.vault.w - 40));      // EDGE_MARGIN = 40
  });

  it("dragging the east resize handle grows the width", () => {
    const { container } = renderWindow();
    const w0 = APPS.vault.w;
    // .gv-rez handles are ordered n, s, e, w — grab the east one (index 2)
    const east = container.querySelectorAll(".gv-rez")[2] as HTMLElement;
    fireEvent.mouseDown(east, { clientX: 500, clientY: 500 });
    fireEvent.mouseMove(document, { clientX: 620, clientY: 500 });   // ddx = +120
    fireEvent.mouseUp(document);
    expect(useWindows.getState().size.vault!.w).toBe(w0 + 120);
  });

  it("clicking an unfocused window raises it to the top of the z-order", async () => {
    // open a second window so there's a z-order to change
    useWindows.getState().openApp("browse");
    const qc = new QueryClient({
      defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
    });
    const ref = deskRef();
    render(
      <QueryClientProvider client={qc}>
        <Window app="vault" deskRef={ref} />
      </QueryClientProvider>
    );
    // browse was opened last, so it's on top; focus vault by mousedown
    const before = useWindows.getState();
    expect(before.z.vault).toBeLessThan(before.z.browse!);
    const title = screen.getByText(APPS.vault.title).parentElement as HTMLElement;
    fireEvent.mouseDown(title, { clientX: 100, clientY: 80 });
    const after = useWindows.getState();
    expect(after.z.vault).toBe(after.zTop);
  });

  it("double-clicking the title bar maximizes, then restores", () => {
    renderWindow();
    const title = screen.getByText(APPS.vault.title).parentElement as HTMLElement;
    const orig = { ...useWindows.getState().pos.vault! };
    fireEvent.doubleClick(title);
    // maximized: snapped to a half-width tile at x=0
    expect(useWindows.getState().pos.vault!.x).toBe(0);
    fireEvent.doubleClick(title);
    // restored to the original position
    expect(useWindows.getState().pos.vault).toEqual(orig);
  });
});
