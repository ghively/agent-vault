// web/src/ui/Ns.test.tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import React from "react";
import { NsButton, NsToggle, NsCapacityBar, NsTitle, NsChip, NsStat, NsInput } from "./Ns";

test("NsButton renders with text and fires onClick", async () => {
  const onClick = vi.fn();
  render(<NsButton variant="cyan" onClick={onClick}>+ Create</NsButton>);
  const btn = screen.getByRole("button", { name: "+ Create" });
  expect(btn).toBeInTheDocument();
  await userEvent.click(btn);
  expect(onClick).toHaveBeenCalledOnce();
});

test("NsButton green and red variants render", () => {
  const { rerender } = render(<NsButton variant="green">Go</NsButton>);
  expect(screen.getByRole("button", { name: "Go" })).toBeInTheDocument();
  rerender(<NsButton variant="red">Stop</NsButton>);
  expect(screen.getByRole("button", { name: "Stop" })).toBeInTheDocument();
});

test("NsToggle shows checked state via aria-checked and toggles on click", async () => {
  const onChange = vi.fn();
  render(<NsToggle on={false} onChange={onChange} label="SMB" />);
  const sw = screen.getByRole("switch", { name: "SMB" });
  expect(sw).toHaveAttribute("aria-checked", "false");
  await userEvent.click(sw);
  expect(onChange).toHaveBeenCalledWith(true);
});

test("NsToggle responds to Enter and Space keys", async () => {
  const onChange = vi.fn();
  render(<NsToggle on={false} onChange={onChange} label="NFS" />);
  const sw = screen.getByRole("switch", { name: "NFS" });
  sw.focus();
  await userEvent.keyboard("{Enter}");
  expect(onChange).toHaveBeenCalledWith(true);
  await userEvent.keyboard(" ");
  expect(onChange).toHaveBeenCalledTimes(2);
});

test("NsTitle renders children with optional subtitle", () => {
  render(<NsTitle sub="root@synapse:~$ zpool status">Storage</NsTitle>);
  expect(screen.getByText("Storage")).toBeInTheDocument();
  expect(screen.getByText(/root@synapse/)).toBeInTheDocument();
});

test("NsCapacityBar renders a progress element", () => {
  render(<NsCapacityBar pct={63} />);
  // The fill span will exist; we just test it doesn't throw
  expect(document.querySelector("[data-testid='ns-cap-fill']")).toBeInTheDocument();
});

test("NsChip renders and fires onClick", async () => {
  const onClick = vi.fn();
  render(<NsChip active onClick={onClick}>SMB</NsChip>);
  await userEvent.click(screen.getByText("SMB"));
  expect(onClick).toHaveBeenCalledOnce();
});

test("NsStat renders label and value", () => {
  render(<NsStat label="POOLS" value="2" />);
  expect(screen.getByText("POOLS")).toBeInTheDocument();
  expect(screen.getByText("2")).toBeInTheDocument();
});

test("NsInput fires onChange with new value", async () => {
  const onChange = vi.fn();
  render(<NsInput value="" onChange={onChange} placeholder="Search..." />);
  const input = screen.getByPlaceholderText("Search...");
  await userEvent.type(input, "a");
  expect(onChange).toHaveBeenCalledWith("a");
});
