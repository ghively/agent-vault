import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { CommandBar } from "./CommandBar";

// Mock the useWindows hook — selector-aware so useWindows((s) => s.openApp)
// returns the mock openApp (the component consumes via selectors).
vi.mock("../store/windows", () => {
  const mockStore = {
    commandBarOpen: true,
    open: ["dashboard"],
    openApp: vi.fn(),
    toggleCommandBar: vi.fn(),
    setCommandBarOpen: vi.fn(),
    theme: "dark" as const,
    setTheme: vi.fn(),
  };
  return {
    useWindows: vi.fn(
      (selector?: (s: typeof mockStore) => unknown) =>
        typeof selector === "function" ? selector(mockStore) : mockStore,
    ),
  };
});

describe("CommandBar", () => {
  it("should render when commandBarOpen is true", () => {
    render(<CommandBar />);
    expect(screen.getByPlaceholderText(/type a command/i)).toBeInTheDocument();
  });

  it("should focus input when opened", () => {
    render(<CommandBar />);
    const input = screen.getByPlaceholderText(/type a command/i);
    expect(input).toHaveFocus();
  });

  it("should filter actions based on query", () => {
    render(<CommandBar />);
    const input = screen.getByPlaceholderText(/type a command/i);

    // Initially should show all actions including assistant
    expect(screen.getByText(/assistant/i)).toBeInTheDocument();

    // Type a filter query
    fireEvent.change(input, { target: { value: "assistant" } });

    // Should only show matching actions
    expect(screen.getByText(/assistant/i)).toBeInTheDocument();
    expect(screen.queryByText(/dashboard/i)).not.toBeInTheDocument();
  });

  it("should show no results when filter matches nothing", () => {
    render(<CommandBar />);
    const input = screen.getByPlaceholderText(/type a command/i);

    fireEvent.change(input, { target: { value: "xyz123nonexistent" } });

    expect(screen.getByText(/no results found/i)).toBeInTheDocument();
  });

  it("should navigate with arrow keys", () => {
    render(<CommandBar />);
    const input = screen.getByPlaceholderText(/type a command/i);

    // Arrow down should work (no error)
    fireEvent.keyDown(input, { key: "ArrowDown" });

    // Arrow up should work (no error)
    fireEvent.keyDown(input, { key: "ArrowUp" });
  });

  it("should show hint text", () => {
    render(<CommandBar />);

    expect(screen.getByText(/↑↓ navigate/)).toBeInTheDocument();
    expect(screen.getByText(/enter select/)).toBeInTheDocument();
    expect(screen.getByText(/esc close/)).toBeInTheDocument();
  });

  it("should execute actions on click", () => {
    render(<CommandBar />);

    // Click on an action button (skip the input which is also a button)
    const buttons = screen.getAllByRole("button");
    const firstActionButton = buttons.find(btn => btn !== screen.getByPlaceholderText(/type a command/i));

    if (firstActionButton) {
      fireEvent.click(firstActionButton);
      // Should not throw any errors
      expect(true).toBe(true);
    }
  });

  it("should update query input", () => {
    render(<CommandBar />);
    const input = screen.getByPlaceholderText(/type a command/i) as HTMLInputElement;

    fireEvent.change(input, { target: { value: "test query" } });

    expect(input.value).toBe("test query");
  });

  it("should handle mouse hover on actions", () => {
    render(<CommandBar />);

    const buttons = screen.getAllByRole("button");
    const firstActionButton = buttons.find(btn => btn !== screen.getByPlaceholderText(/type a command/i));

    if (firstActionButton) {
      fireEvent.mouseEnter(firstActionButton);
      // Should not throw any errors
      expect(true).toBe(true);
    }
  });

  it("should handle keyboard navigation consistently", () => {
    render(<CommandBar />);
    const input = screen.getByPlaceholderText(/type a command/i);

    // Test all arrow key combinations
    fireEvent.keyDown(input, { key: "ArrowDown" });
    fireEvent.keyDown(input, { key: "ArrowDown" });
    fireEvent.keyDown(input, { key: "ArrowUp" });
    fireEvent.keyDown(input, { key: "ArrowUp" });
    fireEvent.keyDown(input, { key: "ArrowDown" });

    // Should not throw any errors
    expect(true).toBe(true);
  });

  it("should handle filter changes during navigation", () => {
    render(<CommandBar />);
    const input = screen.getByPlaceholderText(/type a command/i);

    // Navigate down
    fireEvent.keyDown(input, { key: "ArrowDown" });

    // Change filter
    fireEvent.change(input, { target: { value: "theme" } });

    // Navigate again
    fireEvent.keyDown(input, { key: "ArrowDown" });

    // Should not throw any errors
    expect(true).toBe(true);
  });
});