<script lang="ts">
  // The Results tab: the solved runs' capacity and flows (VAR_Cap, VAR_Ncap, VAR_FIn, VAR_FOut),
  // in four sections that share one row of filters:
  //   Tables   a table per attribute: groups by periods
  //   Charts   the four attributes as stacked columns, ready drawn; click a group to filter to it
  //   Compare  runs side by side, their differences from a base, or a heatmap of differences
  //   Export   the results (Parquet, DuckDB, CSV, Excel) and the charts (SVG, PNG, JPEG, Excel)
  // The model is the DD files; a scenario is the run file, which picks the DD files to include; a
  // run is one scenario solved with one solver, labelled <scenario>.<solver> (server: results.py).
  // Runs of every model and scenario in this session can be compared.
  import { untrack } from "svelte";
  import Bars from "./Bars.svelte";
  import Filters from "./Filters.svelte";
  import Heatmap from "./Heatmap.svelte";
  import {
    colours,
    DIM_LABEL,
    exportChart,
    full,
    OTHER,
    resultsApi,
    SLOTS,
    type Attribute,
    type By,
    type ChartSpec,
    type Facets,
    type Filters as F,
    type Query,
    type Run,
  } from "./results";

  let { modelId = null, solveKey = "" }: { modelId?: string | null; solveKey?: string } = $props();

  type Section = "tables" | "charts" | "compare" | "export";
  const SECTIONS: [Section, string][] = [
    ["tables", "Tables"],
    ["charts", "Charts"],
    ["compare", "Compare runs"],
    ["export", "Export"],
  ];
  const ATTRS = ["VAR_Cap", "VAR_Ncap", "VAR_FIn", "VAR_FOut"];
  const RTP_BY: By[] = ["process_set", "process", "region", "period"];
  const FLOW_BY: By[] = ["commodity", "commodity_set", "process", "process_set", "timeslice", "region"];
  const byFor = (attr: string) => (attr === "VAR_Cap" || attr === "VAR_Ncap" ? RTP_BY : FLOW_BY);
  const FINER: Partial<Record<By, By>> = { process_set: "process", commodity_set: "commodity" };

  let section = $state<Section>(readSection());
  function readSection(): Section {
    try {
      const s = localStorage.getItem("otui.resultsSection");
      return SECTIONS.some(([k]) => k === s) ? (s as Section) : "charts";
    } catch {
      return "charts";
    }
  }
  function setSection(s: Section) {
    section = s;
    try {
      localStorage.setItem("otui.resultsSection", s);
    } catch {
      /* private window */
    }
  }

  let runs = $state<Run[]>([]);
  let attributes = $state<Record<string, Attribute>>({});
  let loaded = $state(false);
  let error = $state("");
  let facets = $state<Facets | null>(null);
  let filters = $state<F>({});
  let selected = $state(""); // the run Tables and Charts show

  // the runs: again after each solve
  $effect(() => {
    void solveKey;
    resultsApi
      .runs()
      .then((r) => {
        runs = r.runs;
        attributes = r.attributes;
        loaded = true;
        if (!r.runs.some((s) => s.id === selected)) {
          const mine = r.runs.filter((s) => s.model_id === modelId).sort((a, b) => b.saved - a.saved);
          selected = (mine[0] ?? r.runs[r.runs.length - 1])?.id ?? "";
        }
      })
      .catch((e) => (error = String(e.message ?? e)));
  });
  $effect(() => {
    const ids = runs.map((s) => s.id);
    if (!ids.length) return;
    resultsApi
      .facets(ids)
      .then((f) => (facets = f))
      .catch(() => (facets = null));
  });
  const label = (id: string) => runs.find((s) => s.id === id)?.label ?? id;
  const attrLabel = (a: string) => `${attributes[a]?.label ?? a} (${a})`;

  // one query, re-run when what it depends on changes; a newer answer wins
  function useQuery(args: () => { runs: string[]; attribute: string; by: By; top: number; base?: string } | null) {
    let result = $state<Query | null>(null);
    let failed = $state("");
    let n = 0;
    $effect(() => {
      const a = args();
      const f = $state.snapshot(filters);
      if (!a || !a.runs.length) {
        result = null;
        return;
      }
      const mine = ++n;
      const ask = () => resultsApi.query({ ...a, filters: f });
      ask()
        .catch(() => new Promise((ok) => setTimeout(ok, 400)).then(ask)) // a dropped request: once more
        .then((q) => {
          if (mine === n) ((result = q), (failed = ""));
        })
        .catch((e) => {
          if (mine === n) failed = String(e.message ?? e);
        });
    });
    return {
      get result() {
        return result;
      },
      get failed() {
        return failed;
      },
    };
  }
  const lookup = (q: Query | null) => {
    const m = new Map<string, number>();
    for (const [s, p, g, v] of q?.rows ?? []) m.set(`${s}|${p}|${g}`, v);
    return (s: string, p: string, g: string) => m.get(`${s}|${p}|${g}`) ?? 0;
  };
  function filterNote(): string {
    const parts = Object.entries(filters)
      .filter(([, v]) => v?.length)
      .map(([d, v]) => `${DIM_LABEL[d as By]}: ${v!.length > 3 ? `${v!.slice(0, 3).join(", ")} +${v!.length - 3}` : v!.join(", ")}`);
    return parts.length ? parts.join(" · ") : "";
  }
  const unitNote = (q: Query | null) => (q?.unit ? q.unit : q && ["VAR_FIn", "VAR_FOut"].includes(q.attribute) ? "mixed units" : "");

  // --- Tables
  let tableAttr = $state("VAR_Cap");
  let tableBy = $state<By>("process");
  $effect(() => {
    if (!byFor(tableAttr).includes(tableBy)) tableBy = byFor(tableAttr)[0];
  });
  const table = useQuery(() => (selected ? { runs: [selected], attribute: tableAttr, by: tableBy, top: 0 } : null));
  let tableRows = $derived.by(() => {
    const q = table.result;
    if (!q) return [];
    const v = lookup(q);
    return q.groups.map((g) => ({ g, vals: q.periods.map((p) => v(selected, p, g)) }));
  });
  let tableTotals = $derived(table.result ? table.result.periods.map((_, i) => tableRows.reduce((a, r) => a + r.vals[i], 0)) : []);
  let tableShown = $state(200);

  // --- Charts: the four attributes, ready drawn
  let chartBy = $state<Record<string, By>>({ VAR_Cap: "process_set", VAR_Ncap: "process_set", VAR_FIn: "commodity", VAR_FOut: "commodity" });
  const charts = Object.fromEntries(
    ATTRS.map((a) => [a, useQuery(() => (selected ? { runs: [selected], attribute: a, by: chartBy[a], top: SLOTS } : null))]),
  );
  let chartSvgs = $state<Record<string, SVGSVGElement | undefined>>({});
  function pick(attr: string, by: By, g: string) {
    if (g === OTHER || by === "period" || by === "run") return;
    filters = { ...filters, [by]: [g] };
    const finer = FINER[by];
    if (finer && attr) chartBy = { ...chartBy, [attr]: finer };
    else if (finer) cmpBy = finer; // the compare chart
  }
  const chartTitle = (a: string, by: By) => `${attributes[a]?.label ?? a} by ${DIM_LABEL[by].toLowerCase()}`;

  // --- Compare
  let base = $state("");
  let others = $state<string[]>([]);
  let cmpAttr = $state("VAR_Cap");
  let cmpBy = $state<By>("process_set");
  let view = $state<"side" | "diff" | "heat">("diff");
  let cmpSvg = $state<SVGSVGElement>();
  $effect(() => {
    // a base and one other by default: the open model's first run, compared with its next (else
    // another model's). It reruns when the runs or the open model change; it writes base and
    // others only when they must change (no loop through the query)
    const ids = runs.map((s) => s.id);
    const mine = runs.filter((s) => s.model_id === modelId).map((s) => s.id);
    untrack(() => {
      const elsewhere = mine.length > 0 && !mine.includes(base);
      if (!ids.includes(base) || elsewhere) base = mine[0] ?? ids[0] ?? "";
      let kept = others.filter((o) => ids.includes(o) && o !== base);
      if (elsewhere) kept = mine.filter((i) => i !== base).slice(0, 1);
      if (!kept.length && ids.length > 1) kept.push((mine.find((i) => i !== base) ?? ids.find((i) => i !== base))!);
      if (kept.length !== others.length || kept.some((o, i) => o !== others[i])) others = kept;
    });
  });
  // a new base leaves the runs to compare with
  $effect(() => {
    const b = base;
    untrack(() => {
      if (others.includes(b)) others = others.filter((o) => o !== b);
    });
  });
  $effect(() => {
    if (!byFor(cmpAttr).includes(cmpBy)) cmpBy = byFor(cmpAttr)[0];
  });
  const cmp = useQuery(() =>
    base && others.length
      ? { runs: [base, ...others], attribute: cmpAttr, by: cmpBy, top: view === "heat" ? 16 : SLOTS, base }
      : null,
  );
  let cmpValue = $derived(lookup(cmp.result));
  // a run minus the base; within TIMES.jl's parity tolerance (1e-6, relative) it is no difference
  const TOL = 1e-6;
  function delta(s: string, p: string, g: string): number {
    const a = cmpValue(s, p, g),
      b = cmpValue(base, p, g);
    return Math.abs(a - b) <= TOL * Math.max(1, Math.abs(a), Math.abs(b)) ? 0 : a - b;
  }
  // the groups drawn: in Difference and Heatmap, only those that differ from the base
  let cmpGroups = $derived.by(() => {
    const q = cmp.result;
    if (!q) return [];
    if (view === "side") return q.groups;
    return q.groups.filter((g) => q.periods.some((p) => others.some((s) => delta(s, p, g))));
  });
  let cmpColours = $derived(colours(cmpBy, cmp.result?.groups ?? []));
  // the biggest differences from the base: what changed most
  let changes = $derived.by(() => {
    const q = cmp.result;
    if (!q) return [];
    const out: { s: string; p: string; g: string; d: number; b: number }[] = [];
    for (const s of others)
      for (const p of q.periods)
        for (const g of q.groups) {
          if (g === OTHER) continue;
          const b = cmpValue(base, p, g);
          const d = delta(s, p, g);
          if (d) out.push({ s, p, g, d, b });
        }
    return out.sort((a, b) => Math.abs(b.d) - Math.abs(a.d)).slice(0, 8);
  });
  const letter = (i: number) => String.fromCharCode(65 + i);
  let cmpSubs = $derived(view === "side" ? [base, ...others].map((id, i) => ({ id, label: `${letter(i)} ${label(id)}` })) : others.map((id, i) => ({ id, label: `${letter(i + 1)} ${label(id)}` })));
  let cmpTitle = $derived(
    view === "side"
      ? `${attributes[cmpAttr]?.label ?? cmpAttr} by ${DIM_LABEL[cmpBy].toLowerCase()}, runs side by side`
      : `${attributes[cmpAttr]?.label ?? cmpAttr}: difference from ${label(base)}`,
  );
  let cmpSubtitle = $derived(
    [
      view === "side" ? cmpSubs.map((s) => s.label).join(" · ") : `${others.map((o) => label(o)).join(", ")} minus ${label(base)}`,
      unitNote(cmp.result),
      filterNote(),
    ]
      .filter(Boolean)
      .join(" · "),
  );

  // --- Export
  let exportIds = $state<string[]>([]);
  $effect(() => {
    if (!exportIds.length && runs.length) exportIds = runs.map((s) => s.id);
  });
  let busy = $state("");
  type Drawn = { key: string; title: string; svg: SVGSVGElement | undefined; spec: () => ChartSpec | null };
  let drawn = $derived<Drawn[]>([
    ...ATTRS.map((a) => ({
      key: a,
      title: chartTitle(a, chartBy[a]),
      svg: chartSvgs[a],
      spec: () => specOf(charts[a].result, chartTitle(a, chartBy[a]), [selected]),
    })),
    ...(cmp.result ? [{ key: "compare", title: cmpTitle, svg: cmpSvg, spec: () => cmpSpec() }] : []),
  ]);
  let chosen = $state<string[]>([...ATTRS, "compare"]);
  function specOf(q: Query | null, title: string, subs: string[]): ChartSpec | null {
    if (!q) return null;
    const v = lookup(q);
    const cats = subs.length > 1 ? q.periods.flatMap((p) => subs.map((s) => `${p} ${label(s)}`)) : q.periods;
    return {
      title,
      unit: q.unit,
      categories: cats,
      category_label: subs.length > 1 ? "Period and run" : "Period",
      series: q.groups.map((g) => ({ name: g, values: q.periods.flatMap((p) => subs.map((s) => v(s, p, g))) })),
      stacked: true,
    };
  }
  function cmpSpec(): ChartSpec | null {
    const q = cmp.result;
    if (!q) return null;
    if (view === "side") return specOf(q, cmpTitle, [base, ...others]);
    const cats = others.length > 1 ? q.periods.flatMap((p) => others.map((s) => `${p} ${label(s)}`)) : q.periods;
    return {
      title: cmpTitle,
      unit: q.unit,
      categories: cats,
      category_label: others.length > 1 ? "Period and run" : "Period",
      series: cmpGroups.map((g) => ({
        name: g,
        values: q.periods.flatMap((p) => others.map((s) => delta(s, p, g))),
      })),
      stacked: view !== "heat",
    };
  }
  async function images(as: "svg" | "png" | "jpeg") {
    busy = as;
    try {
      for (const d of drawn.filter((d) => chosen.includes(d.key) && d.svg)) await exportChart(d.svg!, d.title, as);
    } finally {
      busy = "";
    }
  }
  async function toExcel() {
    busy = "xlsx";
    error = "";
    try {
      const specs = drawn.filter((d) => chosen.includes(d.key)).map((d) => d.spec()).filter((s): s is ChartSpec => !!s);
      await resultsApi.chartsXlsx(specs);
    } catch (e) {
      error = String((e as Error).message ?? e);
    } finally {
      busy = "";
    }
  }
  const toggle = (list: string[], v: string) => (list.includes(v) ? list.filter((x) => x !== v) : [...list, v]);
</script>

<div class="results">
{#if !loaded && !error}
  <p class="muted">Loading results…</p>
{:else if !runs.length}
  <section class="panel empty">
    <h2>No results yet</h2>
    <p>
      Solve a model in the <strong>Run</strong> tab: its capacity and flows appear here, as tables and charts. Solve it with another
      solver, or solve another scenario (run file) or model, to compare runs.
    </p>
    {#if error}<p class="error">{error}</p>{/if}
  </section>
{:else}
  <div class="head">
    <div class="sections" role="tablist" aria-label="Results">
      {#each SECTIONS as [k, l]}
        <button role="tab" aria-selected={section === k} class:on={section === k} onclick={() => setSection(k)}>{l}</button>
      {/each}
    </div>
    {#if section === "tables" || section === "charts"}
      <label class="scen">
        Run
        <select bind:value={selected}>
          {#each runs as s (s.id)}<option value={s.id}>{s.label}</option>{/each}
        </select>
      </label>
    {/if}
  </div>
  {#if section !== "export"}
    <div class="filterbar panel"><Filters {facets} bind:filters /></div>
  {/if}
  {#if error}<p class="error">{error}</p>{/if}

  <!-- Tables -->
  <section class="panel" hidden={section !== "tables"}>
    <div class="controls">
      <label
        >Attribute
        <select bind:value={tableAttr}>
          {#each ATTRS as a}<option value={a}>{attrLabel(a)}</option>{/each}
        </select>
      </label>
      <label
        >Rows
        <select bind:value={tableBy}>
          {#each byFor(tableAttr) as b}<option value={b}>{DIM_LABEL[b]}</option>{/each}
        </select>
      </label>
      <span class="muted">{attributes[tableAttr]?.about}{unitNote(table.result) ? ` · ${unitNote(table.result)}` : ""}</span>
    </div>
    {#if table.failed}<p class="error">{table.failed}</p>{/if}
    {#if table.result}
      {#if !tableRows.length}
        <p class="muted">Nothing to show{filterNote() ? " with these filters" : ""}.</p>
      {:else}
        <div class="scroll">
          <table>
            <thead>
              <tr>
                <th>{DIM_LABEL[tableBy]}</th>
                {#each table.result.periods as p}<th class="num">{p}</th>{/each}
              </tr>
            </thead>
            <tbody>
              {#each tableRows.slice(0, tableShown) as r (r.g)}
                <tr>
                  <td class="mono">{r.g}</td>
                  {#each r.vals as v}<td class="num">{v ? full(v) : ""}</td>{/each}
                </tr>
              {/each}
            </tbody>
            <tfoot>
              <tr>
                <th>Total ({tableRows.length})</th>
                {#each tableTotals as v}<th class="num">{full(v)}</th>{/each}
              </tr>
            </tfoot>
          </table>
        </div>
        {#if tableRows.length > tableShown}
          <button onclick={() => (tableShown += 500)}>Show {Math.min(500, tableRows.length - tableShown)} more of {tableRows.length - tableShown}</button>
        {/if}
      {/if}
    {/if}
  </section>

  <!-- Charts -->
  <section hidden={section !== "charts"}>
    <p class="muted hint">
      Click a column or a legend entry to filter to it: a process set opens into its processes, a commodity set into its commodities. The
      eight largest groups are drawn; the rest are summed as Other.
    </p>
    <div class="grid">
      {#each ATTRS as a (a)}
        {@const q = charts[a].result}
        <div class="panel chart">
          <div class="controls">
            <label
              >By
              <select bind:value={chartBy[a]}>
                {#each byFor(a) as b}<option value={b}>{DIM_LABEL[b]}</option>{/each}
              </select>
            </label>
            <span class="spacer"></span>
            {#each ["svg", "png", "jpeg"] as const as f}
              <button class="small" disabled={!chartSvgs[a]} onclick={() => chartSvgs[a] && exportChart(chartSvgs[a]!, chartTitle(a, chartBy[a]), f)}
                >{f.toUpperCase()}</button
              >
            {/each}
          </div>
          {#if charts[a].failed}<p class="error">{charts[a].failed}</p>{/if}
          {#if q && q.groups.length}
            <Bars
              bind:svg={chartSvgs[a]}
              title={chartTitle(a, chartBy[a])}
              subtitle={[label(selected), unitNote(q), filterNote()].filter(Boolean).join(" · ")}
              unit={q.unit}
              periods={q.periods}
              subs={[{ id: selected, label: label(selected) }]}
              groups={q.groups}
              value={lookup(q)}
              colour={colours(chartBy[a], q.groups)}
              onpick={(g) => pick(a, chartBy[a], g)}
            />
            {#if q.other}<p class="muted note">Other: {q.other} smaller {DIM_LABEL[chartBy[a]].toLowerCase()} groups</p>{/if}
          {:else if q}
            <p class="muted">{attributes[a]?.label}: nothing to show{filterNote() ? " with these filters" : ""}.</p>
          {:else}
            <p class="muted">Drawing…</p>
          {/if}
        </div>
      {/each}
    </div>
  </section>

  <!-- Compare -->
  <section class="panel" hidden={section !== "compare"}>
    {#if runs.length < 2}
      <p>
        One run so far. Solve this scenario with another solver, or open another scenario (run file) or model and solve it,
        to compare runs here.
      </p>
    {:else}
      <div class="controls wrap">
        <label
          >Base
          <select bind:value={base}>
            {#each runs as s (s.id)}<option value={s.id}>{s.label}</option>{/each}
          </select>
        </label>
        <span class="muted">compared with</span>
        {#each runs.filter((s) => s.id !== base) as s (s.id)}
          <label class="check"><input type="checkbox" checked={others.includes(s.id)} onchange={() => (others = toggle(others, s.id))} /> {s.label}</label>
        {/each}
      </div>
      <div class="controls wrap">
        <label
          >Attribute
          <select bind:value={cmpAttr}>
            {#each ATTRS as a}<option value={a}>{attrLabel(a)}</option>{/each}
          </select>
        </label>
        <label
          >By
          <select bind:value={cmpBy}>
            {#each byFor(cmpAttr) as b}<option value={b}>{DIM_LABEL[b]}</option>{/each}
          </select>
        </label>
        <div class="seg" role="group" aria-label="View">
          {#each [["diff", "Difference"], ["side", "Side by side"], ["heat", "Heatmap"]] as [k, l]}
            <button class:on={view === k} aria-pressed={view === k} onclick={() => (view = k as typeof view)}>{l}</button>
          {/each}
        </div>
        <span class="spacer"></span>
        {#each ["svg", "png", "jpeg"] as const as f}
          <button class="small" disabled={!cmpSvg} onclick={() => cmpSvg && exportChart(cmpSvg, cmpTitle, f)}>{f.toUpperCase()}</button>
        {/each}
      </div>
      {#if cmp.failed}<p class="error">{cmp.failed}</p>{/if}
      {#if !others.length}
        <p class="muted">Tick a run to compare with the base.</p>
      {:else if cmp.result && view !== "side" && cmp.result.groups.length && !changes.length}
        <p class="none">
          No differences{filterNote() ? " with these filters" : ""}: every value of {label(base)} and {others.map((o) => label(o)).join(", ")}
          agrees within 1e−6 (relative), TIMES.jl's parity check. Try another attribute or grouping, or Side by side.
        </p>
      {:else if cmp.result && cmp.result.groups.length}
        <div class="cmp">
          <div class="cmpchart">
            {#if view === "heat"}
              {#key cmpAttr + cmpBy}
                <Heatmap
                  bind:svg={cmpSvg}
                  title={cmpTitle}
                  subtitle={cmpSubtitle}
                  unit={cmp.result.unit}
                  periods={cmp.result.periods}
                  groups={cmpGroups}
                  value={(p, g) => delta(others[0], p, g)}
                  onpick={(g) => pick("", cmpBy, g)}
                />
              {/key}
              {#if others.length > 1}<p class="muted note">The heatmap shows {label(others[0])} against the base; untick the others to choose.</p>{/if}
            {:else}
              <Bars
                bind:svg={cmpSvg}
                title={cmpTitle}
                subtitle={cmpSubtitle}
                unit={cmp.result.unit}
                periods={cmp.result.periods}
                subs={cmpSubs}
                groups={cmpGroups}
                totals={view === "side"}
                value={view === "side" ? cmpValue : delta}
                colour={cmpColours}
                onpick={(g) => pick("", cmpBy, g)}
                height={340}
              />
            {/if}
          </div>
          <aside>
            <h3>Biggest changes</h3>
            {#if changes.length}
              <ol>
                {#each changes as c}
                  <li>
                    <button class="change" onclick={() => pick("", cmpBy, c.g)} title="Filter to it">
                      <span class="mono">{c.g}</span>
                      <span class="muted">{c.p}{others.length > 1 ? ` · ${label(c.s)}` : ""}</span>
                      <span class={c.d > 0 ? "up" : "down"}>{c.d > 0 ? "▲ +" : "▼ "}{full(c.d)}</span>
                      {#if c.b}<span class="muted pct">{c.d > 0 ? "+" : ""}{((100 * c.d) / Math.abs(c.b)).toFixed(0)}%</span>{/if}
                    </button>
                  </li>
                {/each}
              </ol>
            {:else}
              <p class="muted">No differences: the runs agree{filterNote() ? " here" : ""}.</p>
            {/if}
          </aside>
        </div>
      {:else if cmp.result}
        <p class="muted">Nothing to show{filterNote() ? " with these filters" : ""}.</p>
      {/if}
    {/if}
  </section>

  <!-- Export -->
  <section class="panel exp" hidden={section !== "export"}>
    <h3>Results</h3>
    <p class="muted">
      Every record of the four attributes for the runs ticked, with a run column, and the model's process and commodity sets,
      descriptions and units.
    </p>
    <div class="controls wrap">
      {#each runs as s (s.id)}
        <label class="check"><input type="checkbox" checked={exportIds.includes(s.id)} onchange={() => (exportIds = toggle(exportIds, s.id))} /> {s.label}</label>
      {/each}
    </div>
    <div class="controls wrap">
      {#each [["parquet", "Parquet", "a zip of Parquet files: Python, R, DuckDB, Power BI"], ["duckdb", "DuckDB database", "one file with every table, to query with SQL"], ["csv", "CSV", "a zip of CSV files"], ["xlsx", "Excel", "a sheet per table (a table over a million rows continues on another sheet)"]] as [f, l, t]}
        <a class="button" class:disabled={!exportIds.length} href={exportIds.length ? resultsApi.exportUrl(exportIds, f as "parquet") : undefined} title={t} download>{l}</a>
      {/each}
    </div>

    <h3>Charts</h3>
    <p class="muted">The charts as drawn now, with their filters: as images, or in Excel as native charts with their data, to edit there.</p>
    <div class="controls wrap">
      {#each drawn as d (d.key)}
        <label class="check"><input type="checkbox" checked={chosen.includes(d.key)} onchange={() => (chosen = toggle(chosen, d.key))} /> {d.title}</label>
      {/each}
    </div>
    <div class="controls wrap">
      {#each ["svg", "png", "jpeg"] as const as f}
        <button disabled={!!busy || !chosen.length} onclick={() => images(f)}>{busy === f ? "Saving…" : f.toUpperCase()}</button>
      {/each}
      <button class="primary" disabled={!!busy || !chosen.length} onclick={toExcel}>{busy === "xlsx" ? "Making…" : "Excel, with editable charts"}</button>
    </div>
    <p class="muted note">
      TIMES.jl runs: otui computes these attributes from TIMES.jl's solution as TIMES's report does (TIMES.jl does not translate the
      report yet); flows tied to capacity (NCAP_COM) and trade and storage processes' auxiliary flows are not included yet. GAMS TIMES
      runs: TIMES's own report.
    </p>
  </section>
{/if}
</div>

<style>
  .results {
    flex: 0 0 auto;
  }
  .none {
    padding: 1rem 0;
    max-width: 46rem;
  }
  .empty {
    padding: 1.2rem 1.4rem;
    max-width: 46rem;
  }
  .empty h2 {
    margin-top: 0;
  }
  .head {
    display: flex;
    flex-wrap: wrap;
    gap: 0.8rem;
    align-items: center;
    margin-bottom: 0.6rem;
  }
  .sections,
  .seg {
    display: inline-flex;
  }
  .sections button,
  .seg button {
    border-radius: 0;
    margin-left: -1px;
  }
  .sections button:first-child,
  .seg button:first-child {
    border-radius: 6px 0 0 6px;
    margin-left: 0;
  }
  .sections button:last-child,
  .seg button:last-child {
    border-radius: 0 6px 6px 0;
  }
  .sections button.on,
  .seg button.on {
    background: var(--accent);
    color: var(--accent-ink);
    border-color: var(--accent);
    font-weight: 600;
  }
  .scen select {
    margin-left: 0.3rem;
    max-width: 22rem;
  }
  .filterbar {
    padding: 0.45rem 0.6rem;
    margin-bottom: 0.7rem;
  }
  section.panel {
    padding: 0.8rem 1rem;
  }
  section[hidden] {
    display: none;
  }
  .controls {
    display: flex;
    gap: 0.6rem;
    align-items: center;
    margin-bottom: 0.5rem;
    font-size: 0.88rem;
  }
  .controls.wrap {
    flex-wrap: wrap;
  }
  .controls select {
    margin-left: 0.25rem;
  }
  .spacer {
    flex: 1;
  }
  button.small {
    font-size: 0.75rem;
    padding: 0.15rem 0.45rem;
  }
  .check {
    display: inline-flex;
    gap: 0.3rem;
    align-items: center;
  }
  .hint {
    margin: 0 0 0.5rem;
    font-size: 0.85rem;
  }
  .grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(min(100%, 520px), 1fr));
    gap: 0.8rem;
  }
  .chart {
    padding: 0.6rem 0.7rem;
  }
  .note {
    font-size: 0.8rem;
    margin: 0.3rem 0 0;
  }
  .scroll {
    overflow: auto;
    max-height: 65vh;
  }
  table {
    border-collapse: collapse;
    font-size: 0.85rem;
    width: 100%;
  }
  th,
  td {
    padding: 0.25rem 0.6rem;
    border-bottom: 1px solid var(--line);
    text-align: left;
    white-space: nowrap;
  }
  thead th {
    position: sticky;
    top: 0;
    background: var(--panel);
  }
  .num {
    text-align: right;
    font-variant-numeric: tabular-nums;
  }
  tfoot th {
    border-top: 2px solid var(--line);
  }
  .cmp {
    display: grid;
    grid-template-columns: minmax(0, 1fr) 17rem;
    gap: 1rem;
  }
  @media (max-width: 900px) {
    .cmp {
      grid-template-columns: 1fr;
    }
  }
  aside h3 {
    margin: 0.2rem 0 0.4rem;
    font-size: 0.95rem;
  }
  aside ol {
    margin: 0;
    padding-left: 1.2rem;
  }
  aside li {
    margin-bottom: 0.3rem;
  }
  .change {
    all: unset;
    cursor: pointer;
    display: flex;
    flex-wrap: wrap;
    gap: 0 0.4rem;
    font-size: 0.85rem;
  }
  .change:hover .mono {
    text-decoration: underline;
  }
  .up,
  .down {
    font-variant-numeric: tabular-nums;
  }
  .pct {
    font-size: 0.78rem;
  }
  .exp h3 {
    margin: 0.4rem 0 0.2rem;
  }
  .exp h3 + p {
    margin-top: 0;
  }
  a.button {
    display: inline-block;
    border: 1px solid var(--line);
    border-radius: 6px;
    padding: 0.35rem 0.75rem;
    background: var(--panel);
    color: inherit;
    text-decoration: none;
  }
  a.button:hover {
    border-color: var(--accent);
  }
  a.button.disabled {
    opacity: 0.5;
    pointer-events: none;
  }
  .error {
    color: var(--error);
  }
</style>
