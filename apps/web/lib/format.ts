export function fmtMs(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "-";
  return `${Math.round(v)} ms`;
}

export function fmtNum(v: number | null | undefined, digits = 0): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "-";
  return v.toFixed(digits);
}

export function fmtDelta(v: number | null | undefined, unit = "ms"): { text: string; cls: string } {
  if (v === null || v === undefined || Number.isNaN(v)) return { text: "-", cls: "delta-zero" };
  const rounded = Math.round(v);
  if (rounded === 0) return { text: `±0 ${unit}`, cls: "delta-zero" };
  const sign = rounded > 0 ? "+" : "";
  const cls = rounded > 0 ? "delta-pos" : "delta-neg";
  return { text: `${sign}${rounded} ${unit}`, cls };
}

export function fmtPct(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "-";
  return `${(v * 100).toFixed(0)}%`;
}
