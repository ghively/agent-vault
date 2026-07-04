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

test("minimize hides the window from focusedApp; focus restores it", () => {
  const s = useWindows.getState();
  s.openApp("browse");      // browse on top of the default vault window
  s.minimize("browse");
  expect(useWindows.getState().minimized.browse).toBe(true);
  // minimized windows are skipped when deriving the focused app
  expect(focusedApp(useWindows.getState())).toBe("vault");
  // the Waybar task button restore path is focus()
  s.focus("browse");
  expect(useWindows.getState().minimized.browse).toBe(false);
  expect(focusedApp(useWindows.getState())).toBe("browse");
});

test("focus restores a minimized window even when it is already top-z", () => {
  const s = useWindows.getState();
  s.openApp("browse");      // browse holds zTop
  s.minimize("browse");
  s.focus("browse");        // z unchanged, but must still un-minimize
  expect(useWindows.getState().minimized.browse).toBe(false);
});

test("close clears minimized state", () => {
  const s = useWindows.getState();
  s.openApp("browse");
  s.minimize("browse");
  s.close("browse");
  expect(useWindows.getState().open).not.toContain("browse");
  expect(useWindows.getState().minimized.browse).toBeUndefined();
});

test("minimize on a non-open app is a no-op", () => {
  useWindows.getState().minimize("wiki"); // not open
  expect(useWindows.getState().minimized.wiki).toBeUndefined();
});

test("setTheme/setDensity stamp data-theme/data-density on <html>", () => {
  const s = useWindows.getState();
  s.setTheme("light");
  s.setDensity("compact");
  expect(document.documentElement.dataset.theme).toBe("light");
  expect(document.documentElement.dataset.density).toBe("compact");
  s.setTheme("dark");
  s.setDensity("comfortable");
  expect(document.documentElement.dataset.theme).toBe("dark");
  expect(document.documentElement.dataset.density).toBe("comfortable");
});
