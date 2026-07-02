// web/src/wm/TabbedApp.test.tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import React from "react";
import { TabbedApp } from "./TabbedApp";
import type { AppId } from "./apps";
import type { TabDef } from "./consolidation";

const TABS: TabDef[] = [
  { id: "storage" as AppId, label: "Storage" },
  { id: "shares" as AppId, label: "Shared Folders" },
  { id: "iscsi" as AppId, label: "iSCSI" },
];

function renderTab(id: AppId) {
  return <div data-testid={`screen-${id}`}>{`screen:${id}`}</div>;
}

test("renders all tab labels", () => {
  render(<TabbedApp tabs={TABS} renderTab={renderTab} />);
  expect(screen.getByRole("tab", { name: "Storage" })).toBeInTheDocument();
  expect(screen.getByRole("tab", { name: "Shared Folders" })).toBeInTheDocument();
  expect(screen.getByRole("tab", { name: "iSCSI" })).toBeInTheDocument();
});

test("first tab is active and its screen is shown by default", () => {
  render(<TabbedApp tabs={TABS} renderTab={renderTab} />);
  // First tab is aria-selected
  expect(screen.getByRole("tab", { name: "Storage" })).toHaveAttribute("aria-selected", "true");
  expect(screen.getByRole("tab", { name: "Shared Folders" })).toHaveAttribute("aria-selected", "false");
  // First screen is rendered
  expect(screen.getByTestId("screen-storage")).toBeInTheDocument();
  expect(screen.queryByTestId("screen-shares")).not.toBeInTheDocument();
});

test("clicking a different tab switches the active screen", async () => {
  render(<TabbedApp tabs={TABS} renderTab={renderTab} />);

  await userEvent.click(screen.getByRole("tab", { name: "Shared Folders" }));

  expect(screen.getByRole("tab", { name: "Shared Folders" })).toHaveAttribute("aria-selected", "true");
  expect(screen.getByRole("tab", { name: "Storage" })).toHaveAttribute("aria-selected", "false");
  // Second screen is now rendered
  expect(screen.getByTestId("screen-shares")).toBeInTheDocument();
  expect(screen.queryByTestId("screen-storage")).not.toBeInTheDocument();
});

test("clicking the third tab switches to it", async () => {
  render(<TabbedApp tabs={TABS} renderTab={renderTab} />);

  await userEvent.click(screen.getByRole("tab", { name: "iSCSI" }));

  expect(screen.getByRole("tab", { name: "iSCSI" })).toHaveAttribute("aria-selected", "true");
  expect(screen.getByTestId("screen-iscsi")).toBeInTheDocument();
});

test("tablist has aria-label", () => {
  render(<TabbedApp tabs={TABS} renderTab={renderTab} />);
  expect(screen.getByRole("tablist")).toHaveAttribute("aria-label", "App sections");
});

test("active tab has tabIndex 0, others have -1", () => {
  render(<TabbedApp tabs={TABS} renderTab={renderTab} />);
  const tabs = screen.getAllByRole("tab");
  expect(tabs[0]).toHaveAttribute("tabindex", "0");
  expect(tabs[1]).toHaveAttribute("tabindex", "-1");
  expect(tabs[2]).toHaveAttribute("tabindex", "-1");
});

test("returns null when tabs list is empty", () => {
  const { container } = render(<TabbedApp tabs={[]} renderTab={renderTab} />);
  expect(container.firstChild).toBeNull();
});
