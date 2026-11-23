<script lang="ts">
  // Parity: every solved run of this model side by side, across the two codes (GAMS TIMES and
  // TIMES.jl) and across solvers, with how far each objective is from the baseline. The baseline is
  // GAMS TIMES + CPLEX (TIMES's reference implementation and solver): a run of it here, else its
  // reference objective for this model, else the first GAMS run, else the first run.
  import { fmt, type ModelInfo, type SolverInfo } from "./api";

  let { model, solvers }: { model: ModelInfo; solvers: SolverInfo[] } = $props();

  // TIMES.jl's own parity check: objectives agree when their relative difference is at most 1e-6
  const TOL = 1e-6;

  interface Row {
    key: string;
    code: string;
    version: string;
    solver: string;
    label: string;
    objective: number;
    rows?: number;
    columns?: number;
    build?: number;
    solve?: number;
    total?: number;
    source?: string; // where a reference objective comes from (no run here)
    crossover?: number; // cuOpt: the vertex crossover reached, when cuOpt returned barrier's point
  }

  const label = (id: string) => (solvers.find((s) => s.id === id)?.label ?? id).replace(/ \(GAMS\)$/, "");
  const sec = (steps: { step: string; seconds: number | null }[], k: string) => steps.find((s) => s.step === k)?.seconds ?? undefined;
  const sum = (steps: { seconds: number | null }[]) => steps.reduce((t, s) => t + (s.seconds ?? 0), 0);

  let runs = $derived.by((): Row[] => {
    const s = model.solve;
    if (!s) return [];
    const all = [
      ...(s.status === "done" ? [{ solver: s.solver ?? "", code: s.code, version: s.version, result: s.result, steps: s.steps ?? [] }] : []),
      ...(s.history ?? []).filter((h) => h.status === "done"),
    ];
    return all
      .filter((r) => r.result?.status === "optimal" && r.result.objective != null)
      .map((r) => ({
        key: r.solver,
        code: r.code ?? "",
        version: r.version ?? "",
        solver: r.solver,
        label: label(r.solver),
        objective: r.result.objective as number,
        rows: r.result.rows,
        columns: r.result.columns,
        build: sec(r.steps, "build"),
        solve: sec(r.steps, "solve"),
        total: sum(r.steps),
        crossover: r.result.crossover_objective,
      }));
  });

  // the reference objective, unless GAMS TIMES + CPLEX has run here
  let reference = $derived.by((): Row | null => {
    const ref = model.reference;
    if (!ref || runs.some((r) => r.solver === "cplex")) return null;
    return { key: "reference", code: "GAMS TIMES", version: "", solver: "cplex", label: "CPLEX", objective: ref.objective, source: ref.source };
  });
  let unsorted = $derived([...(reference ? [reference] : []), ...runs]);
  let base = $derived(
    unsorted.find((r) => r.solver === "cplex") ?? unsorted.find((r) => r.code === "GAMS TIMES") ?? unsorted[0],
  );
  // the baseline first, then GAMS TIMES's runs, then TIMES.jl's, each by solver
  const ORDER = ["GAMS TIMES", "TIMES.jl"];
  let rows = $derived(
    [...unsorted].sort(
      (a, b) =>
        Number(b === base) - Number(a === base) ||
        ORDER.indexOf(a.code) - ORDER.indexOf(b.code) ||
        a.label.localeCompare(b.label),
    ),
  );
  let barrier = $derived(rows.find((r) => r.crossover != null));
  const diff = (a: Row, b: Row) => Math.abs(a.objective - b.objective) / Math.max(1, Math.abs(b.objective));
  const sci = (d: number) => (d === 0 ? "0" : d < 1e-15 ? "< 1e−15" : d.toExponential(1).replace("e-", "e−"));

  // the largest difference between two rows that pass a test, and the pair
  function widest(test: (a: Row, b: Row) => boolean) {
    let best: { d: number; a: Row; b: Row } | null = null;
    for (const [i, a] of rows.entries())
      for (const b of rows.slice(i + 1)) if (test(a, b) && (!best || diff(a, b) > best.d)) best = { d: diff(a, b), a, b };
    return best;
  }
  // across codes: the same solver on both codes is the cleanest comparison of the two codes (HiGHS
  // on TIMES.jl and through GAMS); else the widest pair of different codes, leaving out a barrier
  // point (cuOpt, issue 100), which is a solver's matter
  let sameSolver = $derived(
    rows.flatMap((a, i) =>
      rows.slice(i + 1).filter((b) => a.code !== b.code && a.label === b.label).map((b) => ({ d: diff(a, b), a, b })),
    ),
  );
  let codes = $derived(
    sameSolver.length ? null : widest((a, b) => a.code !== b.code && a.crossover == null && b.crossover == null),
  );
  let byCode = $derived(
    ["GAMS TIMES", "TIMES.jl"]
      .map((c) => ({ code: c, w: widest((a, b) => a.code === c && b.code === c) }))
      .filter((x) => x.w),
  );
  const pair = (a: Row, b: Row) => `${a.code} + ${a.label} and ${b.code} + ${b.label}`;
  const agree = (d: number) => (d <= TOL ? "agree" : "differ");

  let copied = $state(false);
  async function copy() {
    const head = "| Code | Solver | Objective | Difference | Rows × columns | Build (s) | Solve (s) | Total (s) |\n| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |";
    const body = rows
      .map((r) =>
        [
          `${r.code}${r.version ? ` (${r.version})` : ""}`,
          r.label + (r.source ? " (reference)" : ""),
          String(r.objective),
          r === base ? "baseline" : sci(diff(r, base)),
          r.rows ? `${r.rows} × ${r.columns}` : "",
          r.build?.toFixed(1) ?? "",
          r.solve?.toFixed(1) ?? "",
          r.total?.toFixed(1) ?? "",
        ].join(" | "),
      )
      .map((l) => `| ${l} |`);
    try {
      await navigator.clipboard.writeText([`${model.run}: parity of the objective`, "", head, ...body].join("\n"));
      copied = true;
      setTimeout(() => (copied = false), 2000);
    } catch {
      /* no clipboard (an insecure context): nothing to do */
    }
  }
  const t = (v?: number) => (v == null ? "–" : v < 10 ? fmt(v, 2) : fmt(v, 0));
</script>

{#if rows.length}
  <section class="panel parity">
    <div class="top">
      <h3>Parity</h3>
      {#if rows.length > 1}<button class="small" onclick={copy}>{copied ? "Copied" : "Copy as a table"}</button>{/if}
    </div>
    {#if rows.length === 1}
      <p class="muted">
        One solved run so far. Solve with another solver to compare: the objectives of TIMES.jl and GAMS TIMES, and of
        each solver, side by side.
      </p>
    {:else}
      <ul class="sum">
        {#each sameSolver as p (p.a.key + p.b.key)}
          <li class:ok={p.d <= TOL} class:bad={p.d > TOL}>
            <strong>Across codes, same solver:</strong>
            {p.a.label} on GAMS TIMES and on TIMES.jl {agree(p.d)}: relative difference
            <span class="mono">{sci(p.d)}</span>.
          </li>
        {/each}
        {#if codes}
          <li class:ok={codes.d <= TOL} class:bad={codes.d > TOL}>
            <strong>Across codes:</strong>
            TIMES.jl and GAMS TIMES {agree(codes.d)}: relative difference <span class="mono">{sci(codes.d)}</span> at most
            ({pair(codes.a, codes.b)}).
          </li>
        {/if}
        {#each byCode as { code, w } (code)}
          {#if w}
            <li class:ok={w.d <= TOL} class:bad={w.d > TOL}>
              <strong>Across solvers on {code}:</strong>
              {w.a.label} and {w.b.label}
              {agree(w.d)}: <span class="mono">{sci(w.d)}</span>{#if w.a.crossover != null || w.b.crossover != null}
                {" "}(cuOpt's barrier point: see below){/if}.
            </li>
          {/if}
        {/each}
      </ul>
    {/if}
    <div class="scroll">
      <table>
        <thead>
          <tr>
            <th>Code</th>
            <th>Solver</th>
            <th class="num">Objective</th>
            <th class="num" title="Relative difference from the baseline: |a − b| / max(1, |b|)">Difference</th>
            <th class="num">Rows × columns</th>
            <th class="num" title="Model generation">Build</th>
            <th class="num" title="The solver's own run">Solve</th>
            <th class="num">Total</th>
          </tr>
        </thead>
        <tbody>
          {#each rows as r (r.key)}
            {@const d = r === base ? null : diff(r, base)}
            <tr class:base={r === base}>
              <td>{r.code}{#if r.version}<small class="muted">{r.version}</small>{/if}</td>
              <td>{r.label}{#if r.source}<small class="muted" title={r.source}>reference</small>{/if}</td>
              <td class="num mono">{fmt(r.objective, 4)}</td>
              <td class="num mono">
                {#if d == null}<span class="muted">baseline</span>{:else}<span class={d <= TOL ? "ok" : "bad"}>{d <= TOL ? "✓" : "✕"} {sci(d)}</span>{/if}
              </td>
              <td class="num mono">{r.rows ? `${fmt(r.rows, 0)} × ${fmt(r.columns, 0)}` : "–"}</td>
              <td class="num mono">{t(r.build)}</td>
              <td class="num mono">{t(r.solve)}</td>
              <td class="num mono">{t(r.total)}</td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
    <p class="muted note">
      Objectives agree (✓) when their relative difference is at most 1e−6, TIMES.jl's own parity check. Times in seconds,
      on this computer.{#if reference}
        The reference objective: {reference.source}.{/if}
    </p>
    {#if barrier?.crossover != null}
      <p class="muted note">
        {barrier.label}'s objective is barrier's point: cuOpt's crossover reached
        <span class="mono">{fmt(barrier.crossover, 4)}</span>{#if base && barrier !== base}
          {" "}({sci(Math.abs(barrier.crossover - base.objective) / Math.max(1, Math.abs(base.objective)))} from the baseline){/if}, but
        cuOpt returned barrier's solution
        (<a href="https://github.com/esma-ie/timesjl/issues/100" target="_blank" rel="noreferrer">TIMES.jl issue 100</a>).
      </p>
    {/if}
  </section>
{/if}

<style>
  .parity {
    padding: 0.8rem 1rem;
  }
  .top {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    gap: 0.5rem;
  }
  h3 {
    margin: 0 0 0.4rem;
    color: var(--heading);
  }
  button.small {
    font-size: 0.85rem;
    padding: 0.15rem 0.5rem;
  }
  .sum {
    margin: 0 0 0.6rem;
    padding-left: 1.1rem;
  }
  .sum li {
    margin: 0.15rem 0;
  }
  .sum li.ok::marker,
  .ok {
    color: var(--ok);
  }
  .sum li.bad::marker,
  .bad {
    color: var(--error);
  }
  .scroll {
    overflow-x: auto;
  }
  table {
    width: 100%;
    border-collapse: collapse;
    font-size: 0.9rem;
  }
  th,
  td {
    padding: 0.3rem 0.45rem;
    border-bottom: 1px solid var(--line);
    text-align: left;
    vertical-align: baseline;
    white-space: nowrap;
  }
  th {
    font-weight: 600;
    color: var(--muted);
    font-size: 0.8rem;
  }
  .num {
    text-align: right;
  }
  td small {
    display: block;
    font-size: 0.72rem;
  }
  tr.base td {
    background: color-mix(in srgb, var(--accent) 7%, transparent);
  }
  .note {
    font-size: 0.85rem;
    margin: 0.5rem 0 0;
  }
</style>
