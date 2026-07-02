export function snapFor(
  rx: number,
  ry: number,
  W: number,
  H: number,
  edge = 8,
): { x: number; y: number; w: number; h: number } | null {
  const R = (o: { x: number; y: number; w: number; h: number }) => ({
    x: Math.round(o.x),
    y: Math.round(o.y),
    w: Math.round(o.w),
    h: Math.round(o.h),
  });
  if (ry < edge) return R({ x: 0, y: 0, w: W, h: H });
  if (rx < edge) return R({ x: 0, y: 0, w: W / 2, h: H });
  if (rx > W - edge) return R({ x: W / 2, y: 0, w: W / 2, h: H });
  return null;
}

export function clampResize(
  dir: string,
  ddx: number,
  ddy: number,
  w0: number,
  h0: number,
  x0: number,
  y0: number,
  minW = 360,
  minH = 250,
): { w: number; h: number; x: number; y: number } {
  let w = w0, h = h0, x = x0, y = y0;
  if (dir.includes("e")) w = Math.max(minW, w0 + ddx);
  if (dir.includes("s")) h = Math.max(minH, h0 + ddy);
  if (dir.includes("w")) { w = Math.max(minW, w0 - ddx); x = x0 + (w0 - w); }
  if (dir.includes("n")) { h = Math.max(minH, h0 - ddy); y = y0 + (h0 - h); }
  return { w, h, x, y };
}
