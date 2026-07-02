import { useWindows, focusedApp } from "./windows";

beforeEach(() => useWindows.getState().reset());

test("openApp adds, focuses, and raises z; reopening just focuses", () => {
  const s = useWindows.getState();
  s.openApp("browse");
  expect(useWindows.getState().open).toContain("browse");
  expect(focusedApp(useWindows.getState())).toBe("browse");
  s.openApp("wiki");
  expect(focusedApp(useWindows.getState())).toBe("wiki");
  s.openApp("browse"); // already open -> just focus
  expect(useWindows.getState().open.filter((a) => a === "browse").length).toBe(1);
  expect(focusedApp(useWindows.getState())).toBe("browse");
});

test("close removes the window", () => {
  const s = useWindows.getState();
  s.openApp("creds");
  s.close("creds");
  expect(useWindows.getState().open).not.toContain("creds");
});

test("openApp('vault') opens the vault hub", () => {
  useWindows.getState().openApp("vault");
  expect(useWindows.getState().open).toContain("vault");
});

test("focus on a non-open app is a no-op", () => {
  const s = useWindows.getState();
  s.openApp("browse");
  const zBefore = useWindows.getState().zTop;
  s.focus("wiki"); // not open
  expect(useWindows.getState().zTop).toBe(zBefore);
  expect(useWindows.getState().open).not.toContain("wiki");
});

test("focusedApp returns highest-z app after refocusing a lower one", () => {
  const s = useWindows.getState();
  s.openApp("browse");
  s.openApp("wiki");        // wiki now top
  s.focus("browse");          // browse raised above wiki
  expect(focusedApp(useWindows.getState())).toBe("browse");
});
