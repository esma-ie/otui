// Typed client for otui's local API (src/otui/server.py).

export interface EngineHealth {
  state: "starting" | "warming" | "ready" | "error";
  error: string;
  timings: Record<string, number>;
  channel?: "latest" | "pinned"; // which TIMES.jl: main's newest, or the commit otui is tested on
  commit?: string;
  fallback?: string; // why it is not on the latest TIMES.jl
}

export interface SolverInfo {
  id: string; // highs, cuopt, cplex
  label: string;
  description: string;
  available: boolean;
  reason: string;
  state?: "idle" | "starting" | "warming" | "ready" | "error"; // a solver with its own worker
}

export interface RunStep {
  step: string;
  label: string;
  state: "pending" | "running" | "done" | "failed" | "killed";
  seconds: number | null;
  started: number | null; // epoch seconds, while running
  log: string[];
}

// an earlier run of the model, one per solver, newest first: its step times (columns beside the
// latest run's) and its outcome (the parity panel)
export interface RunSummary {
  solver: string;
  status: "done" | "error" | "killed";
  code?: "TIMES.jl" | "GAMS TIMES";
  version?: string; // TIMES.jl: channel @ commit; GAMS: TIMES and GAMS versions
  result: Pick<SolveResult, "status" | "objective" | "termination" | "stopped_after" | "rows" | "columns" | "crossover_objective">;
  steps: Pick<RunStep, "step" | "label" | "state" | "seconds">[];
}

// a reference objective of GAMS TIMES + CPLEX for this model (server: references.py)
export interface Reference {
  model: string;
  objective: number;
  source: string;
}

// what TIMES.jl does not translate yet, and what in the model brings it in (server: report.py)
export interface Untranslated {
  what: string;
  gams_files: string[];
  in_model: { name: string; records: number }[];
  switches: { name: string; value: string }[];
  coverage: string; // TIMES.jl's table of what is translated
  issues: string; // TIMES.jl's issues about it
  ask: string; // a new TIMES.jl issue asking for it, filled in
}

export interface Health {
  otui: string;
  engine: string;
  reader?: EngineHealth;
  runner?: EngineHealth;
  julia?: string;
  times_jl?: string;
  times_gams_version?: string;
  threads?: number;
  solvers?: SolverInfo[];
  gams_unlicensed?: string[];
  times_jl_update?: { state: string; commit?: string; message?: string }; // latest TIMES.jl, in the background // GAMS solvers installed here but not in the licence
  steps?: { step: string; label: string }[];
  // where the server runs: the user's own computer, or a GitHub codespace under their account
  hosting?: { kind: "local" | "codespaces" };
}

export interface SolveResult {
  status?: "optimal" | "not_optimal" | "generated" | "stopped" | "not_translated";
  stopped_after?: string;
  crossover_objective?: number; // cuOpt: the vertex crossover reached, when cuOpt.jl returns barrier's point
  solver?: string;
  objective?: number;
  termination?: string;
  rows?: number;
  columns?: number;
  segment?: string;
  message?: string;
  untranslated?: Untranslated;
  compile_seconds?: number;
  preprocess_seconds?: number;
  generate_seconds?: number;
  solve_seconds?: number;
}

export interface ModelInfo {
  id: string;
  status: "uploaded" | "needs_run" | "compiling" | "loading" | "ready" | "error";
  run: string | null;
  files: string[];
  error: string;
  timings: Record<string, number>;
  run_candidates?: string[];
  summary?: {
    source: string;
    symbols: number;
    tables: number;
    records: number;
    counts: Record<string, number>;
  };
  reference?: Reference;
  untranslated?: Untranslated; // the reader stopped on a feature TIMES.jl lacks (status "error")
  solve?: {
    status: "running" | "done" | "error" | "killed";
    error: string;
    result: SolveResult;
    solver?: string;
    code?: RunSummary["code"];
    version?: string;
    steps?: RunStep[];
    history?: RunSummary[];
  };
}

export interface OpenModel {
  id: string; // otnyc, otcgen
  label: string;
  description: string;
  bytes: number; // download size, the first time
  cached: boolean;
  source: string; // its files on GitHub
}

export interface SymbolInfo {
  name: string;
  kind: "set" | "parameter" | "alias" | "variable" | "equation";
  headers: string[];
  records: number;
  description: string;
  has_table: boolean;
}

export interface Rows {
  name: string;
  columns: string[];
  total: number;
  rows: (string | number)[][];
}

export interface GNode {
  id: string;
  name: string;
  kind: "process" | "commodity";
  description: string;
  regions: string[];
  types: string[];
  flags: string[];
}

export interface GLink {
  source: string;
  target: string;
  kind: "flow" | "trade" | "emission" | "aggregation";
  regions: string[];
}

export interface GraphData {
  focus: string | null;
  depth?: number;
  reach?: number[]; // nodes within 0, 1, 2, ... links of the focus (or of the centre)
  centre?: string | null;
  auto?: boolean; // too large to draw whole: centred on the centre, at the most links that fit
  limit?: number;
  sectors?: [string, number][]; // processes by sector (first letter), over the whole network
  groups?: [string, number][];
  nodes: GNode[];
  links: GLink[];
  flags: Record<string, number>;
  dummies?: string[];
}

export interface Theme {
  source: string; // the page read (after redirects)
  sheets: number;
  brand: string[]; // the company's colours, heaviest first
  neutral: string[];
  light: Record<string, string>; // CSS variables
  dark: Record<string, string>;
}

export interface DummyScale {
  unit: string;
  annual: number; // the largest annual dummy amount in any period
  demand: number | null; // the model's annual demand in that unit (COM_PROJ)
  ratio: number | null;
  significant: boolean;
}

export interface Dummies {
  processes: string[];
  solved: boolean;
  used?: boolean;
  significant?: boolean; // highlighted: within the threshold of demand
  threshold?: number;
  scale?: DummyScale[];
  suppliers?: string[];
  horizon?: string[];
  totals?: { type: string; unit: string; total: number; commodities: number }[];
  commodities?: { commodity: string; type: string; unit: string; total: number }[];
  periods?: { period: string; type: string; unit: string; annual: number }[];
}

export interface Hit {
  id: string;
  name: string;
  kind: "process" | "commodity";
  description: string;
}

export interface Issue {
  source: "TIMES" | "otui";
  severity: string;
  check: string;
  message: string;
  item: string;
}

export interface QCReport {
  times_log: boolean;
  counts: Record<string, number>;
  issues: Issue[];
}

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

async function get<T>(url: string): Promise<T> {
  const r = await fetch(url);
  if (!r.ok) throw new ApiError(r.status, (await r.json().catch(() => null))?.detail ?? `${r.status} ${url}`);
  return r.json();
}

async function post<T>(url: string, body: FormData): Promise<T> {
  const r = await fetch(url, { method: "POST", body });
  if (!r.ok) throw new Error((await r.json().catch(() => null))?.detail ?? `${r.status} ${url}`);
  return r.json();
}

const m = (id: string) => `/api/models/${encodeURIComponent(id)}`;

export const api = {
  health: () => get<Health>("/api/health"),
  upload(files: File[], run?: string) {
    const f = new FormData();
    for (const file of files) f.append("files", file, file.name);
    if (run) f.append("run", run);
    return post<ModelInfo>("/api/models", f);
  },
  demo: () => post<ModelInfo>("/api/models/demo", new FormData()),
  // open models beside the demo (OTNYC, OTCGEN): downloaded from GitHub on first use
  openModels: () => get<OpenModel[]>("/api/open-models"),
  openModel: (key: string) => post<ModelInfo>(`/api/models/open/${key}`, new FormData()),
  chooseRun(id: string, run: string) {
    const f = new FormData();
    f.append("run", run);
    return post<ModelInfo>(`${m(id)}/run`, f);
  },
  model: (id: string) => get<ModelInfo>(m(id)),
  symbols: (id: string) => get<SymbolInfo[]>(`${m(id)}/symbols`),
  rows(id: string, name: string, opts: { offset?: number; limit?: number; q?: string; where?: string[] }) {
    const p = new URLSearchParams();
    p.set("offset", String(opts.offset ?? 0));
    p.set("limit", String(opts.limit ?? 100));
    if (opts.q) p.set("q", opts.q);
    for (const w of opts.where ?? []) p.append("where", w);
    return get<Rows>(`${m(id)}/symbols/${encodeURIComponent(name)}?${p}`);
  },
  graph(id: string, opts: { focus?: string; depth?: number; region?: string }) {
    const p = new URLSearchParams();
    if (opts.focus) p.set("focus", opts.focus);
    if (opts.depth != null) p.set("depth", String(opts.depth));
    if (opts.region) p.set("region", opts.region);
    return get<GraphData>(`${m(id)}/graph?${p}`);
  },
  search: (id: string, q: string) => get<Hit[]>(`${m(id)}/search?q=${encodeURIComponent(q)}`),
  qc: (id: string) => get<QCReport>(`${m(id)}/qc`),
  dummies: (id: string) => get<Dummies>(`${m(id)}/dummies`),
  kill: (id: string) => post<ModelInfo>(`${m(id)}/kill`, new FormData()), // Kill the job
  prepareSolver: (id: string) => post<{ id: string; state: string }>(`/api/solvers/${id}/prepare`, new FormData()),
  theme(url: string) {
    const f = new FormData();
    f.append("url", url);
    return post<Theme>("/api/theme", f);
  },
  solve(id: string, until = "results", solver = "highs") {
    const f = new FormData();
    f.append("until", until);
    f.append("solver", solver);
    return post<ModelInfo>(`${m(id)}/solve`, f);
  },
  exportUrl: (id: string, kind: "xlsx" | "zip") => `${m(id)}/export.${kind}`,
  // Report this run: the zip, and where to open an issue (TIMES.jl or otui), filled in
  reportUrl: (id: string, withModel: boolean) => `${m(id)}/report.zip?model=${withModel}`,
  report: (id: string) => get<{ issue: { url: string; where: string } }>(`${m(id)}/report`),
};

export const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

export function fmt(n: number | undefined, digits = 1): string {
  if (n == null) return "";
  return n.toLocaleString(undefined, { maximumFractionDigits: digits });
}
