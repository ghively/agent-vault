import { snapFor, clampResize } from "./geometry";

test("snapFor returns halves and maximize", () => {
  expect(snapFor(2, 200, 1000, 700)).toEqual({ x: 0, y: 0, w: 500, h: 700 });      // left edge
  expect(snapFor(995, 200, 1000, 700)).toEqual({ x: 500, y: 0, w: 500, h: 700 });   // right edge
  expect(snapFor(400, 2, 1000, 700)).toEqual({ x: 0, y: 0, w: 1000, h: 700 });       // top -> max
  expect(snapFor(400, 300, 1000, 700)).toBeNull();
});

test("clampResize enforces minimums and moves origin for n/w handles", () => {
  const e = clampResize("e", 100, 0, 400, 300, 50, 50);
  expect(e.w).toBe(500);
  const w = clampResize("w", 1000, 0, 400, 300, 50, 50); // shrinking past min
  expect(w.w).toBe(360);
  expect(w.x).toBe(50 + (400 - 360));
});
