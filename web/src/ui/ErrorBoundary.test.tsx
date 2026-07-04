import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, afterEach, vi } from "vitest";
import { ErrorBoundary } from "./ErrorBoundary";

// A child that throws on demand so the boundary's catch path can be exercised.
function Boom({ crash }: { crash: boolean }): React.ReactElement {
  if (crash) throw new Error("kaboom");
  return <div>screen ok</div>;
}

describe("ErrorBoundary", () => {
  afterEach(() => vi.restoreAllMocks());

  it("renders children when they don't throw", () => {
    render(
      <ErrorBoundary>
        <div>healthy content</div>
      </ErrorBoundary>
    );
    expect(screen.getByText("healthy content")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("catches a render error and shows a recoverable panel with the message", () => {
    // React logs the caught error to console.error; silence it for a clean run.
    vi.spyOn(console, "error").mockImplementation(() => {});
    render(
      <ErrorBoundary>
        <Boom crash />
      </ErrorBoundary>
    );
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("this screen hit an error");
    expect(alert).toHaveTextContent("kaboom");
    expect(screen.getByRole("button", { name: "retry" })).toBeInTheDocument();
  });

  it("shows a custom title and a reload button in reloadOnFail mode (F1)", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    const reload = vi.fn();
    // jsdom's window.location.reload is non-configurable; stub via defineProperty.
    Object.defineProperty(window, "location", {
      value: { ...window.location, reload },
      writable: true,
    });
    render(
      <ErrorBoundary title="Agent Vault hit an error" reloadOnFail>
        <Boom crash />
      </ErrorBoundary>
    );
    expect(screen.getByRole("alert")).toHaveTextContent("Agent Vault hit an error");
    await userEvent.click(screen.getByRole("button", { name: "reload" }));
    expect(reload).toHaveBeenCalled();
  });

  it("recovers when resetKey changes and the child no longer throws", () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    const { rerender } = render(
      <ErrorBoundary resetKey="a">
        <Boom crash />
      </ErrorBoundary>
    );
    expect(screen.getByRole("alert")).toBeInTheDocument();

    // switching apps (new resetKey) with a healthy child clears the error
    rerender(
      <ErrorBoundary resetKey="b">
        <Boom crash={false} />
      </ErrorBoundary>
    );
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByText("screen ok")).toBeInTheDocument();
  });

  it("clears the error when retry is clicked and the child has recovered", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    function Wrapper({ crash }: { crash: boolean }) {
      return (
        <ErrorBoundary resetKey="same">
          <Boom crash={crash} />
        </ErrorBoundary>
      );
    }
    const { rerender } = render(<Wrapper crash />);
    expect(screen.getByRole("alert")).toBeInTheDocument();

    // underlying cause fixed, but resetKey unchanged — the manual retry button
    // is the recovery path.
    rerender(<Wrapper crash={false} />);
    await userEvent.click(screen.getByRole("button", { name: "retry" }));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByText("screen ok")).toBeInTheDocument();
  });
});
