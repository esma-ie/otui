// The Results tab: its API calls (server: /api/results, results.py), colours that follow each
// group, and chart export (SVG, PNG, JPEG, and Excel with native charts).

// A run: a scenario (the run file, which picks the DD files to include) solved with one solver.
// The model is the DD files.
export interface Run {
  id: string; // <model id>.<scenario>.<solver>
  model_id: string;
  scenario: string; // the run file's name, without .run
  run_file: string;
  solver: string;
  solver_label: string;
  label: string; // "<scenario>.<solver>", and "(model N)" when two models have the scenario
  code?: string;
  version?: string;
  objective?: number;
  records: Record<string, number>;
  saved: number;
}

export interface Attribute {
  label: string;
  dims: string[];
  about: string;
}

export type Dim = "region" | "period" | "process" | "process_set" | "commodity" | "commodity_set" | "timeslice";
export type By = Dim | "run";
export type Filters = Partial<Record<Dim, string[]>>;

export interface Named {
  id: string;
  description: string;
}

export interface Facets {
  region: string[];
  period: string[];
  process: Named[];
  process_set: string[];
  commodity: Named[];
  commodity_set: string[];
  timeslice: string[];
}

export interface Query {
  attribute: string;
  by: By;
  groups: string[];
  periods: string[];
  runs: string[];
  rows: [string, string, string, number][]; // run, period, group, value
  other: number; // groups summed into "Other"
  unit: string | null;
}

export const DIM_LABEL: Record<By, string> = {
  region: "Region",
  period: "Period",
  process: "Process",
  process_set: "Process set",
  commodity: "Commodity",
  commodity_set: "Commodity set",
  timeslice: "Timeslice",
  run: "Run",
};
export const OTHER = "Other";

async function json<T>(r: Response): Promise<T> {
  if (!r.ok) throw new Error((await r.json().catch(() => null))?.detail ?? `${r.status} ${r.url}`);
  return r.json();
}

const qs = (ids: string[]) => ids.map((i) => `r=${encodeURIComponent(i)}`).join("&");

export const resultsApi = {
  runs: () =>
    fetch("/api/results/runs").then((r) => json<{ runs: Run[]; attributes: Record<string, Attribute> }>(r)),
  facets: (ids: string[]) => fetch(`/api/results/facets?${qs(ids)}`).then((r) => json<Facets>(r)),
  query: (body: { runs: string[]; attribute: string; filters: Filters; by: By; top?: number; base?: string }) =>
    fetch("/api/results/query", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then((r) => json<Query>(r)),
  exportUrl: (ids: string[], fmt: "parquet" | "duckdb" | "csv" | "xlsx") => `/api/results/export?${qs(ids)}&fmt=${fmt}`,
  async chartsXlsx(charts: ChartSpec[]) {
    const r = await fetch("/api/results/charts.xlsx", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ charts }),
    });
    if (!r.ok) throw new Error((await r.json().catch(() => null))?.detail ?? `${r.status}`);
    download(await r.blob(), "otui-charts.xlsx");
  },
};

// --- colours: eight categorical slots (validated for colour vision deficiency in both themes:
// the dataviz palette, as --series-1..8 in app.css), assigned in order and kept by each group, so
// a filter never repaints the groups that remain. A ninth group takes the slot of a group not on
// screen; groups past eight are summed into "Other" by the server (top=8).
export const SLOTS = 8;
const owners = new Map<string, string[]>(); // a grouping -> the group in each slot

export function colours(by: string, groups: string[]): Map<string, string> {
  const slots = owners.get(by) ?? Array<string>(SLOTS).fill("");
  owners.set(by, slots);
  const showing = new Set(groups);
  const out = new Map<string, string>();
  for (const g of groups) {
    if (g === OTHER) continue;
    let i = slots.indexOf(g);
    if (i < 0) i = slots.indexOf("");
    if (i < 0) i = slots.findIndex((o) => !showing.has(o));
    if (i < 0) continue;
    slots[i] = g;
    out.set(g, `var(--series-${i + 1})`);
  }
  out.set(OTHER, "var(--series-other)");
  return out;
}

// a CSS colour (var(--x)) as the value it has now, so an exported chart carries real colours
export function resolve(colour: string, el: Element = document.documentElement): string {
  const m = colour.match(/^var\((--[\w-]+)\)$/);
  return m ? getComputedStyle(el).getPropertyValue(m[1]).trim() || colour : colour;
}

// --- numbers

export function compact(v: number): string {
  const a = Math.abs(v);
  if (a >= 1e9) return `${+(v / 1e9).toFixed(1)}G`;
  if (a >= 1e6) return `${+(v / 1e6).toFixed(1)}M`;
  if (a >= 1e4) return `${+(v / 1e3).toFixed(1)}K`;
  if (a >= 100) return v.toFixed(0);
  if (a >= 1) return `${+v.toFixed(2)}`;
  return a === 0 ? "0" : `${+v.toPrecision(2)}`;
}

export const full = (v: number) => v.toLocaleString(undefined, { maximumFractionDigits: Math.abs(v) >= 100 ? 0 : 3 });

// --- a chart, as data: what the Excel export draws and the table view shows

export interface ChartSpec {
  title: string;
  unit?: string | null;
  categories: string[]; // x labels (periods, or "period · run")
  category_label?: string;
  series: { name: string; values: (number | null)[] }[];
  stacked?: boolean;
  kind?: "bar" | "line";
}

// --- export of a drawn chart

export function download(blob: Blob, name: string) {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 4000);
}

export const fileName = (title: string) =>
  title
    .replace(/[^\w\- ]+/g, "")
    .trim()
    .replace(/\s+/g, "-")
    .toLowerCase() || "chart";

// The SVG as a standalone file: its size, a background, the page's font, and every CSS colour
// (fills, strokes, text) written out as the value it has now.
export function svgText(svg: SVGSVGElement, background: string): string {
  const copy = svg.cloneNode(true) as SVGSVGElement;
  const vb = svg.viewBox.baseVal;
  const w = vb?.width || svg.clientWidth;
  const h = vb?.height || svg.clientHeight;
  copy.setAttribute("xmlns", "http://www.w3.org/2000/svg");
  copy.setAttribute("width", String(w));
  copy.setAttribute("height", String(h));
  const live = svg.querySelectorAll("*");
  copy.querySelectorAll("*").forEach((el, i) => {
    const cs = getComputedStyle(live[i]);
    for (const p of ["fill", "stroke", "opacity", "font-size", "font-weight", "text-anchor"] as const) {
      const v = cs.getPropertyValue(p);
      if (v && !(p === "opacity" && v === "1")) el.setAttribute(p, v);
    }
    el.removeAttribute("class");
  });
  copy.setAttribute("font-family", getComputedStyle(svg).fontFamily);
  const bg = document.createElementNS("http://www.w3.org/2000/svg", "rect");
  bg.setAttribute("width", "100%");
  bg.setAttribute("height", "100%");
  bg.setAttribute("fill", background);
  copy.insertBefore(bg, copy.firstChild);
  return new XMLSerializer().serializeToString(copy);
}

export async function exportChart(svg: SVGSVGElement, title: string, as: "svg" | "png" | "jpeg") {
  const background = resolve("var(--panel)") || "#ffffff";
  const text = svgText(svg, background);
  const name = fileName(title);
  if (as === "svg") return download(new Blob([text], { type: "image/svg+xml" }), `${name}.svg`);
  const vb = svg.viewBox.baseVal;
  const scale = 2; // sharp on slides
  const img = new Image();
  img.src = URL.createObjectURL(new Blob([text], { type: "image/svg+xml" }));
  await img.decode();
  const canvas = document.createElement("canvas");
  canvas.width = (vb?.width || svg.clientWidth) * scale;
  canvas.height = (vb?.height || svg.clientHeight) * scale;
  const ctx = canvas.getContext("2d")!;
  ctx.fillStyle = background; // JPEG has no transparency
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
  URL.revokeObjectURL(img.src);
  const blob = await new Promise<Blob | null>((ok) => canvas.toBlob(ok, `image/${as}`, 0.92));
  if (blob) download(blob, `${name}.${as === "jpeg" ? "jpg" : "png"}`);
}
