import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useRef } from "react";
import { describe, it, expect } from "vitest";
import { useFocusTrap } from "./useFocusTrap";

function Dialog() {
  const ref = useRef<HTMLDivElement>(null);
  useFocusTrap(ref);
  return (
    <div>
      <button>outside-before</button>
      <div ref={ref} role="dialog">
        <button>first</button>
        <button>middle</button>
        <button>last</button>
      </div>
      <button>outside-after</button>
    </div>
  );
}

describe("useFocusTrap", () => {
  it("cycles Tab within the dialog and Shift+Tab wraps backward", async () => {
    render(<Dialog />);
    const first = screen.getByRole("button", { name: "first" });
    const last = screen.getByRole("button", { name: "last" });

    first.focus();
    // Tab from last wraps back to first (never escapes to outside-after).
    last.focus();
    await userEvent.tab();
    expect(first).toHaveFocus();

    // Shift+Tab from first wraps to last (never escapes to outside-before).
    first.focus();
    await userEvent.tab({ shift: true });
    expect(last).toHaveFocus();
  });

  it("restores focus to the trigger when the dialog unmounts", () => {
    function Harness({ open }: { open: boolean }) {
      return (
        <div>
          <button id="trigger">trigger</button>
          {open && <Dialog />}
        </div>
      );
    }
    const { rerender } = render(<Harness open={false} />);
    const trigger = screen.getByRole("button", { name: "trigger" });
    trigger.focus();
    expect(trigger).toHaveFocus();

    rerender(<Harness open />);   // opening captures previous focus (trigger)
    rerender(<Harness open={false} />);  // closing restores it
    expect(trigger).toHaveFocus();
  });
});
