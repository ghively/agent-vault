// web/src/wm/apps.test.ts
import { APPS, APP_ORDER, APP_GROUPS, ICON_FILE, type AppId } from "./apps";

// The vault app ids that must exist
const VAULT_APP_IDS: AppId[] = [
  "browse",
  "wiki",
  "vault",
  "creds",
  "review",
  "pipeline",
  "command",
];

test("all vault app ids are registered in APPS", () => {
  for (const id of VAULT_APP_IDS) {
    expect(APPS).toHaveProperty(id);
  }
});

test("all registered apps have required fields", () => {
  for (const [id, def] of Object.entries(APPS)) {
    expect(def.glyph, `${id}.glyph`).toBeTruthy();
    expect(def.name, `${id}.name`).toBeTruthy();
    expect(def.label, `${id}.label`).toBeTruthy();
    expect(def.title, `${id}.title`).toBeTruthy();
    expect(def.color, `${id}.color`).toBeTruthy();
    expect(def.grad, `${id}.grad`).toBeTruthy();
    expect(def.w, `${id}.w`).toBeGreaterThan(0);
    expect(def.h, `${id}.h`).toBeGreaterThan(0);
  }
});

test("all app glyphs are unique", () => {
  const glyphs = Object.values(APPS).map((a) => a.glyph);
  const unique = new Set(glyphs);
  expect(unique.size).toBe(glyphs.length);
});

test("glyphs contain no emoji (only ASCII/Unicode marks)", () => {
  // Emoji codepoints are in Supplementary Multilingual Plane (> U+FFFF) or
  // specific emoji ranges. We test that each glyph is a single visible char
  // and not in common emoji blocks.
  for (const [id, def] of Object.entries(APPS)) {
    const cp = def.glyph.codePointAt(0)!;
    // No emojis: emoji range starts around U+1F000
    expect(cp, `${id} glyph should not be emoji`).toBeLessThan(0x1f000);
  }
});

test("vault app ids appear in APP_ORDER", () => {
  for (const id of VAULT_APP_IDS) {
    expect(APP_ORDER).toContain(id);
  }
});

test("launcher has exactly 1 group: VAULT", () => {
  expect(APP_GROUPS).toHaveLength(1);
  expect(APP_GROUPS[0].label).toBe("VAULT");
});

test("VAULT group contains all vault apps", () => {
  const vaultGroup = APP_GROUPS.find((g) => g.label === "VAULT")!;
  expect(vaultGroup).toBeDefined();
  expect(vaultGroup.ids).toHaveLength(7);
  for (const id of VAULT_APP_IDS) {
    expect(vaultGroup.ids).toContain(id);
  }
});

test("all vault apps have icon file mappings", () => {
  for (const id of VAULT_APP_IDS) {
    expect(ICON_FILE).toHaveProperty(id);
  }
});
