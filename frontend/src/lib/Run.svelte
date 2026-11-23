<script lang="ts">
  // Run a TIMES model step by step: Read, Preprocess, Build model, Pass to
  // solver, Solve, Results, each with a live timer and its log. "Solve" runs every step; any
  // other button runs the steps up to and including it. Solvers: HiGHS (default), cuOpt, CPLEX.
  import { api, fmt, sleep, type Health, type ModelInfo, type RunStep, type RunSummary } from "./api";
  import DummyNote from "./DummyNote.svelte";
  import Parity from "./Parity.svelte";
  import Untranslated from "./Untranslated.svelte";

  let {
    model,
    health,
    onupdate,
  }: { model: ModelInfo; health: Health | null; onupdate: (m: ModelInfo) => void } = $props();

  const STEPS: [string, string][] = [
    ["read", "Read"],
    ["preprocess", "Preprocess"],
    ["build", "Build model"],
    ["pass", "Pass to solver"],
    ["solve", "Solve"],
    ["results", "Results"],
  ];
  const HINT: Record<string, string> = {
    read: "Read the run file and DD files",
    preprocess: "TIMES's preprocessing and its QA checks",
    build: "Generate the model: equations and variables",
    pass: "Copy the model into the solver's memory",
    solve: "The solver's own run",
    results: "Read the solution back",
  };

  let error = $state("");
  let solver = $state("highs");
  let now = $state(Date.now() / 1000);
  let running = $derived(model.solve?.status === "running");
  let res = $derived(model.solve?.result);
  let steps = $derived(model.solve?.steps ?? []);
  let solvers = $derived(
    health?.solvers ?? [{ id: "highs", label: "HiGHS", description: "TIMES.jl + HiGHS", available: true, reason: "" }],
  );
  let runner = $derived(health?.runner);
  // a solver with its own worker (cuOpt in WSL) starts when chosen, and warms up on demo12
  let workerState = $state<Record<string, string>>({});
  let polling = false;
  async function choose(id: string) {
    const s = solvers.find((x) => x.id === id);
    if (!s?.state || s.state === "ready" || polling) return;
    polling = true;
    try {
      workerState[id] = (await api.prepareSolver(id)).state;
      while (workerState[id] !== "ready" && workerState[id] !== "error") {
        await sleep(3000);
        const h = await api.health();
        workerState[id] = h.solvers?.find((x) => x.id === id)?.state ?? "ready";
      }
    } catch {
      /* the run itself will start it */
    } finally {
      polling = false;
    }
  }
  const stateOf = (id: string) => workerState[id] ?? solvers.find((x) => x.id === id)?.state;
  const STATE_TEXT: Record<string, string> = {
    idle: "starts when chosen",
    starting: "starting in WSL…",
    warming: "warming up on demo12…",
    ready: "ready",
    error: "could not start",
  };
  let total = $derived(steps.reduce((t, s) => t + (s.seconds ?? (s.state === "running" && s.started ? now - s.started : 0)), 0));
  // earlier runs with the other solvers, newest first: every one in the parity panel, the two
  // newest as columns to the right of the latest run's steps
  const PAST_COLUMNS = 2;
  let allHistory = $derived(model.solve?.history ?? []);
  let history = $derived(allHistory.slice(0, PAST_COLUMNS));
  // the rows: every step the latest run or an earlier one went through, in run order
  let rows = $derived(
    STEPS.map(([k]) => k).filter((k) => steps.some((s) => s.step === k) || history.some((h) => h.steps.some((s) => s.step === k))),
  );
  // solvers run through GAMS TIMES (CPLEX, and GAMS's other licensed LP solvers), not TIMES.jl
  const isGams = (id?: string) => !!id && id !== "highs" && id !== "cuopt";
  const solverLabel = (id?: string) => solvers.find((s) => s.id === id)?.label ?? id ?? "";
  const stepOf = (k: string) => steps.find((s) => s.step === k);
  const labelOf = (k: string) => STEPS.find(([key]) => key === k)?.[1] ?? k;
  function pastTime(h: RunSummary, k: string) {
    const s = h.steps.find((x) => x.step === k);
    if (!s) return "–";
    if (s.state === "failed") return `✕ ${clock(s.seconds)}`;
    if (s.state === "killed") return `■ ${clock(s.seconds)}`;
    return s.seconds == null ? "–" : clock(s.seconds);
  }
  const pastTotal = (h: RunSummary) => h.steps.reduce((t, s) => t + (s.seconds ?? 0), 0);
  // the outcome under each column: the objective, or where and why the run stopped
  function outcome(status: string | undefined, r: { status?: string; objective?: number; termination?: string; stopped_after?: string } | undefined) {
    if (status === "running") return "running…";
    if (status === "error") return "failed";
    if (status === "killed") return "killed";
    if (!r) return "";
    if (r.status === "optimal" && r.objective != null) return fmt(r.objective, 2);
    if (r.status === "generated" || r.status === "stopped") return `after ${labelOf(r.stopped_after ?? "")}`;
    if (r.status === "not_translated") return "not translated";
    return r.termination ?? r.status ?? "";
  }

  // live timers while a step runs
  $effect(() => {
    if (!running) return;
    const t = setInterval(() => (now = Date.now() / 1000), 100);
    return () => clearInterval(t);
  });

  let lastUntil = $state("results");
  let following = false;
  // follow the run until it ends (one loop at a time)
  async function follow() {
    if (following) return;
    following = true;
    try {
      for (;;) {
        await sleep(400);
        const m = await api.model(model.id);
        onupdate(m);
        if (m.solve?.status !== "running") break;
      }
    } finally {
      following = false;
    }
  }

  // a run already going when the page opens (a reload, another tab): follow it too
  $effect(() => {
    if (running) follow();
  });

  async function run(until: string) {
    error = "";
    lastUntil = until;
    try {
      onupdate(await api.solve(model.id, until, solver));
      await follow();
    } catch (e) {
      error = String(e).replace(/^Error: /, "");
    }
  }

  // Kill the job: a solve can take too long, and HiGHS struggles with larger models. A TIMES.jl solver's worker is ended and starts again, warmed; GAMS's job just ends.
  let killing = $state(false);
  const RESTART: Record<string, string> = { highs: "about 2–3 minutes", cuopt: "a few minutes" };
  async function kill(ask = true) {
    const id = model.solve?.solver ?? "";
    const label = solverLabel(id);
    const again = RESTART[id]
      ? ` otui then restarts the ${label} worker, which warms up again (${RESTART[id]}) before its next run.`
      : "";
    if (ask && !confirm(`Kill this ${label} run?${again}`)) return;
    killing = true;
    error = "";
    try {
      onupdate(await api.kill(model.id));
      await follow();
    } catch (e) {
      error = String(e).replace(/^Error: /, "");
    } finally {
      killing = false;
    }
  }

  // HiGHS taking too long: measured against CPLEX's solve of this model when
  // there is one, else after two minutes; suggest another open solver. cuOpt is currently faster
  // than HiGHS on larger models.
  const SLOW_FACTOR = 5;
  const SLOW_ALONE = 120; // seconds, with no CPLEX run to compare with
  let cplexSolve = $derived(
    allHistory.find((h) => h.solver === "cplex" && h.status === "done")?.steps.find((x) => x.step === "solve")?.seconds ?? null,
  );
  let highsSolve = $derived.by(() => {
    if (model.solve?.solver !== "highs") return null;
    const st = steps.find((x) => x.step === "solve");
    return st ? secs(st) : null;
  });
  let slow = $derived.by(() => {
    if (highsSolve == null || model.solve?.status === "error") return null;
    if (cplexSolve != null) {
      const ratio = highsSolve / Math.max(cplexSolve, 0.01);
      return highsSolve > Math.max(10, SLOW_FACTOR * cplexSolve) ? { ratio } : null;
    }
    return running && highsSolve > SLOW_ALONE ? { ratio: null } : null;
  });
  // GAMS TIMES with CPLEX when this computer has it: for a model TIMES.jl does not translate yet
  let gamsSolver = $derived(solvers.find((x) => x.id === "cplex" && x.available));

  // Report this run: a zip to attach to an issue, and the issue, filled in (TIMES.jl's for a
  // TIMES.jl run, otui's otherwise)
  let finished = $derived(["done", "error", "killed"].includes(model.solve?.status ?? ""));
  let withModel = $state(true);
  let opening = $state(false);
  let reportError = $state("");
  async function openIssue() {
    opening = true;
    reportError = "";
    try {
      const r = await api.report(model.id);
      window.open(r.issue.url, "_blank", "noopener");
    } catch (e) {
      reportError = String(e).replace(/^Error: /, "");
    } finally {
      opening = false;
    }
  }

  // other open solvers here, cuOpt first
  let openAlternatives = $derived(solvers.filter((x) => x.available && ["cuopt", "gams-highs", "cbc"].includes(x.id)));
  let cuoptHere = $derived(solvers.some((x) => x.id === "cuopt" && x.available));
  async function switchTo(id: string) {
    if (running) await kill(false);
    solver = id;
    choose(id);
    await run(lastUntil);
  }

  const secs = (s: RunStep) =>
    s.seconds != null ? s.seconds : s.state === "running" && s.started ? Math.max(0, now - s.started) : null;
  const clock = (v: number | null) => (v == null ? "" : v < 0.005 ? "< 0.01 s" : v < 60 ? `${fmt(v, v < 10 ? 2 : 1)} s` : `${Math.floor(v / 60)} min ${fmt(v % 60, 0)} s`);
  const ICON: Record<string, string> = { pending: "○", running: "◔", done: "✓", failed: "✕", killed: "■" };
</script>

{#snippet breakable(text: string)}{#each text.split(",") as part, i}{#if i},<wbr />{/if}{part}{/each}{/snippet}

<div class="run">
  <section class="panel">
    <h3>Run the model</h3>
    <fieldset class="solvers">
      <legend>Solver</legend>
      {#each solvers as s (s.id)}
        <label class:off={!s.available} title={s.available ? s.description : s.reason}>
          <input type="radio" name="solver" value={s.id} bind:group={solver} disabled={!s.available || running} onchange={() => choose(s.id)} />
          <span>
            <strong>{s.label}</strong> <span class="muted">{s.description}</span>
            {#if s.available && stateOf(s.id)}<span class="wstate" class:ready={stateOf(s.id) === "ready"}>· {STATE_TEXT[stateOf(s.id) ?? ""] ?? stateOf(s.id)}</span>{/if}
            {#if !s.available}<span class="muted">· {s.reason}</span>{/if}
          </span>
        </label>
      {/each}
    </fieldset>

    <div class="buttons" role="group" aria-label="Run up to a step">
      {#each STEPS.slice(0, 4) as [key, label], i}
        <button onclick={() => run(key)} disabled={running} title="{HINT[key]}: runs {i ? `the steps up to ${label.toLowerCase()}` : 'this step'}">
          {label}
        </button>
        <span class="arrow" aria-hidden="true">›</span>
      {/each}
      <button class="primary" onclick={() => run("results")} disabled={running} title="Run every step: read, preprocess, build, pass to the solver, solve, read the results">
        {running ? "Running…" : "Solve"}
      </button>
      {#if running}
        <button class="kill" onclick={() => kill()} disabled={killing} title="Stop this run now, if it is taking too long">
          {killing ? "Killing…" : "Kill the job"}
        </button>
      {/if}
    </div>
    {#if slow}
      <div class="notice slow" role="status">
        <p>
          {#if slow.ratio != null}
            <strong>HiGHS is taking much longer than CPLEX on this model:</strong>
            {clock(highsSolve)} on the solve{running ? " so far" : ""}, {fmt(slow.ratio, slow.ratio < 10 ? 1 : 0)}× CPLEX's
            {clock(cplexSolve)}.
          {:else}
            <strong>HiGHS has been solving for {clock(highsSolve)}.</strong>
          {/if}
          HiGHS struggles with larger models. Try another open solver: cuOpt is currently faster than HiGHS on larger
          models{cuoptHere ? "." : ", but needs an NVIDIA GPU and its set-up (~/.otui/config.toml)."}
        </p>
        {#if openAlternatives.length}
          <p class="alt">
            {#each openAlternatives as a (a.id)}
              <button onclick={() => switchTo(a.id)} disabled={killing}>{running ? "Kill and run with" : "Run with"} {a.label}</button>
            {/each}
          </p>
        {/if}
      </div>
    {/if}
    <p class="muted small">
      <strong>Solve</strong> runs every step. Any other button runs the steps up to and including it.
      {#if solver === "highs" && runner && runner.state !== "ready"}
        TIMES.jl is still warming up (about 2–3 minutes the first time); a run waits for it.
      {:else if solver === "cuopt"}
        cuOpt's worker starts in WSL when chosen and warms up (the first time: a few minutes), then stays ready.
      {:else if isGams(solver)}
        GAMS TIMES and {solverLabel(solver)} run the same DD files on this computer, with its GAMS licence.
      {/if}
    </p>
    {#if health?.gams_unlicensed?.length}
      <p class="muted small">Also installed in GAMS, but not in this computer's licence: {health.gams_unlicensed.join(", ")}.</p>
    {/if}
    {#if error}<p class="err">{error}</p>{/if}
  </section>

  {#if steps.length}
    <section class="panel" style="--cols: {1 + history.length}">
      <div class="row head">
        <h3>Steps</h3>
        <span class="col latest" title="The latest run">{solverLabel(model.solve?.solver)}<small>{running ? "running" : "latest"}</small></span>
        {#each history as h (h.solver)}
          <span class="col past" title="An earlier run with {solverLabel(h.solver)}">{solverLabel(h.solver)}<small>earlier</small></span>
        {/each}
      </div>
      <ol class="steps">
        {#each rows as k (k)}
          {@const s = stepOf(k)}
          <li class={s?.state ?? "skipped"}>
            {#if s}
              <details open={s.state === "running" || s.state === "failed"}>
                <summary class="row">
                  <span class="icon" aria-hidden="true">{ICON[s.state] ?? "○"}</span>
                  <span class="label">{s.label}</span>
                  <span class="muted hint">{HINT[s.step]}</span>
                  <span class="time mono">{clock(secs(s))}</span>
                  {#each history as h (h.solver)}<span class="time mono past">{pastTime(h, k)}</span>{/each}
                </summary>
                {#if s.log.length}<pre>{s.log.join("\n")}</pre>{:else}<p class="muted small">{s.state === "pending" ? "Not started." : "No log lines."}</p>{/if}
              </details>
            {:else}
              <div class="row" title="Not part of the latest run">
                <span class="icon muted" aria-hidden="true">–</span>
                <span class="label muted">{labelOf(k)}</span>
                <span class="muted hint">{HINT[k]}</span>
                <span class="time mono muted">–</span>
                {#each history as h (h.solver)}<span class="time mono past">{pastTime(h, k)}</span>{/each}
              </div>
            {/if}
          </li>
        {/each}
      </ol>
      <div class="row foot">
        <span></span><span class="label">Total</span><span class="hint"></span>
        <span class="time mono total">{clock(total)}</span>
        {#each history as h (h.solver)}<span class="time mono past total">{clock(pastTotal(h))}</span>{/each}
      </div>
      <div class="row foot obj">
        <span></span><span class="label">Objective</span><span class="muted hint">or where the run stopped</span>
        <span class="time mono">{@render breakable(outcome(model.solve?.status, res))}</span>
        {#each history as h (h.solver)}<span class="time mono past">{@render breakable(outcome(h.status, h.result))}</span>{/each}
      </div>
    </section>
  {/if}

  {#if model.solve?.status === "error"}
    <section class="panel err">Run failed: {model.solve.error}</section>
  {:else if model.solve?.status === "killed"}
    {@const at = steps.find((x) => x.state === "killed")}
    <section class="panel">
      <p class="big warn">Killed{at ? ` during ${at.label}` : " before it started"}, after {clock(total)}.</p>
      {#if RESTART[model.solve.solver ?? ""]}
        <p class="muted">
          The {solverLabel(model.solve.solver)} worker is starting again and warms up ({RESTART[model.solve.solver ?? ""]}); its
          next run waits for it.
        </p>
      {/if}
    </section>
  {:else if res && model.solve?.status === "done"}
    <section class="panel">
      {#if res.status === "optimal"}
        <p class="big"><span class="ok">Optimal</span> · objective <strong class="mono">{fmt(res.objective, 6)}</strong></p>
      {:else if res.status === "not_translated"}
        {#if res.untranslated}
          <Untranslated
            u={res.untranslated}
            stage={res.segment === "model generation" ? "in model generation" : `in preprocessing (${res.segment})`}
          >
            <li>Browsing, the graph, QC and export work as before.</li>
            {#if gamsSolver}
              <li>
                Run it with GAMS TIMES:
                <button onclick={() => switchTo(gamsSolver.id)} disabled={running}>Run with {gamsSolver.label}</button>
              </li>
            {:else}
              <li>Run it with GAMS TIMES, where GAMS is installed (set up in <code>~/.otui/config.toml</code>).</li>
            {/if}
            <li><strong>Report this run</strong>, below, makes a zip of the run to attach to the issue.</li>
          </Untranslated>
        {:else}
          <p class="big warn">Not translated in TIMES.jl yet</p>
          <pre>{res.message}</pre>
        {/if}
      {:else if res.status === "generated" || res.status === "stopped"}
        <p class="big">Stopped after <strong>{STEPS.find(([k]) => k === res.stopped_after)?.[1] ?? res.stopped_after}</strong>, as asked.</p>
      {:else}
        <p class="big warn">{res.termination ?? res.status}</p>
      {/if}
      {#if res.crossover_objective != null}
        <p class="warn small">
          cuOpt's crossover reached objective <span class="mono">{fmt(res.crossover_objective, 6)}</span>, but cuOpt
          returned barrier's solution (above). The values otui shows are barrier's
          (<a href="https://github.com/esma-ie/timesjl/issues/100" target="_blank" rel="noreferrer">TIMES.jl issue 100</a>).
        </p>
      {/if}
      {#if res.rows}
        <p class="muted">Model: {fmt(res.rows, 0)} rows × {fmt(res.columns, 0)} columns</p>
      {/if}
      {#if res.status === "optimal" && !isGams(model.solve?.solver)}<DummyNote modelId={model.id} solveKey={model.solve?.status ?? ""} />{/if}
      <p class="muted">TIMES's own quality checks from this run are in the QC tab.</p>
    </section>
  {/if}

  <Parity {model} {solvers} />

  {#if finished}
    {@const trouble = model.solve?.status !== "done" || res?.status !== "optimal"}
    <details class="panel report" open={trouble}>
      <summary><h3>Report this run</h3></summary>
      <p class="muted small">
        A zip to attach to an issue: the versions, each step's time and log, the solver's log, TIMES's QA_CHECK.LOG and
        this model's other runs. The logs can show folder names from this computer.
      </p>
      <label class="check">
        <input type="checkbox" bind:checked={withModel} />
        Include the model's files (needed to reproduce the run; leave them out if the model is not yours to share)
      </label>
      <p class="buttons">
        <a class="button" href={api.reportUrl(model.id, withModel)} download>Download the report</a>
        <button onclick={openIssue} disabled={opening}>
          {model.solve?.code === "TIMES.jl" ? "Open a TIMES.jl issue" : "Open an otui issue"}
        </button>
      </p>
      <p class="muted small">The issue opens on GitHub with the details filled in; drag the zip into it.</p>
      {#if reportError}<p class="err small">{reportError}</p>{/if}
    </details>
  {/if}
</div>

<style>
  .run {
    display: flex;
    flex-direction: column;
    gap: 0.75rem;
    max-width: 900px;
  }
  section {
    padding: 0.8rem 1rem;
  }
  h3 {
    margin: 0 0 0.4rem;
    color: var(--heading);
  }
  .solvers {
    border: 0;
    padding: 0;
    margin: 0 0 0.6rem;
    display: flex;
    flex-direction: column;
    gap: 0.2rem;
  }
  .solvers legend {
    font-weight: 600;
    margin-bottom: 0.2rem;
  }
  .solvers label {
    display: flex;
    gap: 0.4rem;
    align-items: baseline;
    cursor: pointer;
  }
  .solvers label.off {
    opacity: 0.55;
    cursor: not-allowed;
  }
  .buttons {
    display: flex;
    flex-wrap: wrap;
    gap: 0.3rem;
    align-items: center;
  }
  .wstate {
    font-size: 0.85rem;
    color: var(--warn);
  }
  .wstate.ready {
    color: var(--ok);
  }
  .arrow {
    color: var(--muted);
  }
  .small {
    font-size: 0.85rem;
    margin: 0.4rem 0 0;
  }
  /* one grid for the header, the steps and the totals: the latest run's column, then up to two
     earlier runs' columns (--cols) */
  .row {
    display: grid;
    grid-template-columns: 1.4rem 9rem minmax(0, 1fr) repeat(var(--cols, 1), 9.5rem);
    gap: 0.4rem;
    align-items: baseline;
    padding: 0.35rem 0.6rem;
  }
  .row.head {
    padding-top: 0;
    padding-bottom: 0.25rem;
    /* the steps' left border is 4px wider than the header's */
    padding-left: calc(0.6rem + 5px);
    padding-right: calc(0.6rem + 1px);
  }
  .row.head h3 {
    grid-column: 1 / span 3;
    margin: 0;
  }
  .col {
    text-align: right;
    font-weight: 600;
    display: flex;
    flex-direction: column;
    line-height: 1.2;
  }
  .col small {
    font-weight: 400;
    font-size: 0.75rem;
    color: var(--muted);
  }
  .col.latest {
    color: var(--heading);
    border-bottom: 2px solid var(--accent);
  }
  .col.past {
    color: var(--muted);
    border-bottom: 2px solid var(--line);
  }
  .past {
    color: var(--muted);
  }
  .row.foot {
    padding-left: calc(0.6rem + 5px);
    padding-right: calc(0.6rem + 1px);
    border-top: 1px solid var(--line);
    margin-top: 0.25rem;
  }
  .row.foot + .row.foot {
    border-top: 0;
    margin-top: 0;
    padding-top: 0;
  }
  .total {
    font-weight: 600;
  }
  .steps li.skipped {
    border-style: dashed;
  }
  @media (max-width: 640px) {
    .row {
      grid-template-columns: 1rem minmax(5.5rem, 1.3fr) repeat(var(--cols, 1), minmax(0, 1fr));
      gap: 0.3rem;
      font-size: 0.9rem;
    }
    .time {
      font-size: 0.8rem;
    }
    .row .hint {
      display: none;
    }
    /* objectives break at their thousands separators only */
    .row.obj .time {
      overflow-wrap: normal;
    }
    .row.head h3 {
      grid-column: 1 / span 2;
    }
  }
  .steps {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: 0.25rem;
  }
  .steps li {
    border: 1px solid var(--line);
    border-left: 4px solid var(--line);
    border-radius: 6px;
  }
  .steps li.running {
    border-left-color: var(--rule);
  }
  .steps li.done {
    border-left-color: var(--ok);
  }
  .steps li.failed {
    border-left-color: var(--error);
  }
  .steps li.killed {
    border-left-color: var(--warn);
  }
  .killed .icon {
    color: var(--warn);
  }
  button.kill {
    margin-left: 0.4rem;
    border-color: var(--error);
    color: var(--error);
    font-weight: 600;
  }
  button.kill:hover:not(:disabled) {
    background: var(--error);
    color: var(--bg);
  }
  /* ESMA's notice style, as the dummy-import warning */
  .notice.slow {
    margin-top: 0.6rem;
    padding: 0.6rem 0.8rem;
    border-radius: 6px;
    background: var(--notice);
    color: var(--notice-ink);
  }
  .notice.slow p {
    margin: 0;
  }
  .notice.slow .alt {
    margin-top: 0.5rem;
    display: flex;
    flex-wrap: wrap;
    gap: 0.4rem;
  }
  .notice.slow button {
    color: var(--notice-ink);
    border-color: var(--notice-ink);
    background: transparent;
  }
  summary {
    cursor: pointer;
    list-style: none;
  }
  summary::-webkit-details-marker {
    display: none;
  }
  .icon {
    text-align: center;
  }
  .done .icon {
    color: var(--ok);
  }
  .failed .icon {
    color: var(--error);
  }
  .running .icon {
    color: var(--accent);
    animation: spin 1.2s linear infinite;
    display: inline-block;
  }
  @keyframes spin {
    to {
      transform: rotate(360deg);
    }
  }
  .label {
    font-weight: 600;
  }
  .hint {
    font-size: 0.85rem;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .time {
    text-align: right;
    overflow-wrap: anywhere;
  }
  details pre,
  details > p.small {
    margin: 0 0.6rem 0.5rem;
  }
  .big {
    font-size: 1.15rem;
    margin: 0 0 0.5rem;
  }
  .ok {
    color: var(--ok);
    font-weight: 600;
  }
  .warn {
    color: var(--warn);
  }
  .err {
    color: var(--error);
  }
  pre {
    white-space: pre-wrap;
    font-size: 0.8rem;
    background: var(--bg);
    padding: 0.5rem;
    border-radius: 6px;
    max-height: 260px;
    overflow: auto;
  }
  /* Report this run */
  details.report {
    padding: 0.6rem 1rem;
  }
  details.report summary h3 {
    display: inline;
    font-size: 1rem;
  }
  details.report summary::before {
    content: "▸ ";
    color: var(--muted);
  }
  details.report[open] summary::before {
    content: "▾ ";
  }
  label.check {
    display: flex;
    gap: 0.4rem;
    align-items: baseline;
    margin: 0.5rem 0;
    font-size: 0.9rem;
  }
  details.report .buttons {
    margin: 0.4rem 0 0;
  }
  a.button {
    display: inline-block;
    background: var(--accent);
    color: var(--accent-ink);
    border: 1px solid var(--accent);
    border-radius: 6px;
    padding: 0.35rem 0.75rem;
    font-weight: 600;
    text-decoration: none;
  }
  @media (prefers-reduced-motion: reduce) {
    .running .icon {
      animation: none;
    }
  }
</style>
