<script lang="ts">
  import { api, fmt, sleep, type Health, type ModelInfo, type OpenModel } from "./lib/api";
  import DropZone from "./lib/DropZone.svelte";
  import Graph from "./lib/Graph.svelte";
  import QC from "./lib/QC.svelte";
  import Results from "./lib/Results.svelte";
  import Run from "./lib/Run.svelte";
  import Tables from "./lib/Tables.svelte";
  import Untranslated from "./lib/Untranslated.svelte";
  import ThemePicker from "./lib/ThemePicker.svelte";
  import logoPrimary from "./assets/esma-logo-on-dark.svg";
  import logoWhite from "./assets/esma-logo-white.svg";
  import logoGreen from "./assets/esma-logo-green.svg";

  // The logo. In ESMA's colours: the primary logo, yellow mark and white
  // logotype, on the dark-green bar. In a company's colours (ThemePicker): the one-colour logo,
  // all white or all ESMA dark green, whichever contrasts more with the bar behind it.
  let header = $state<HTMLElement>();
  let logo = $state(logoPrimary);
  const ESMA_GREEN: [number, number, number] = [0x00, 0x45, 0x39];
  const WHITE: [number, number, number] = [0xff, 0xff, 0xff];
  function luminance([r, g, b]: [number, number, number]) {
    const lin = (c: number) => ((c /= 255) <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4);
    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);
  }
  function contrast(a: [number, number, number], b: [number, number, number]) {
    const [x, y] = [luminance(a), luminance(b)].sort((p, q) => q - p);
    return (x + 0.05) / (y + 0.05);
  }
  function pickLogo() {
    if (!header || !document.getElementById("otui-theme")) {
      logo = logoPrimary;
      return;
    }
    const m = getComputedStyle(header).backgroundColor.match(/\d+(\.\d+)?/g);
    const bar = (m ? m.slice(0, 3).map(Number) : ESMA_GREEN) as [number, number, number];
    logo = contrast(bar, ESMA_GREEN) > contrast(bar, WHITE) ? logoGreen : logoWhite;
  }
  $effect(() => {
    if (!header) return;
    pickLogo();
    const dark = matchMedia("(prefers-color-scheme: dark)");
    window.addEventListener("otui-theme", pickLogo);
    dark.addEventListener("change", pickLogo);
    return () => {
      window.removeEventListener("otui-theme", pickLogo);
      dark.removeEventListener("change", pickLogo);
    };
  });

  type Tab = "graph" | "tables" | "qc" | "run" | "results";
  let health = $state<Health | null>(null);
  // the TIMES.jl that runs models (the HiGHS worker), or the reader's when there is no runner
  let timesjl = $derived(health?.runner?.channel ? health.runner : health?.reader);
  // in a GitHub codespace, models go to a cloud computer under the user's GitHub account: say so
  let codespace = $derived(health?.hosting?.kind === "codespaces");
  let hideHosting = $state(false);
  let model = $state<ModelInfo | null>(null);
  let error = $state("");
  let busy = $state(false);
  let tab = $state<Tab>("graph");
  let focus = $state("");
  let regions = $state<string[]>([]);
  let elapsed = $state(0);

  // TIMES.jl status: every 2 s until both workers are ready, then every 15 s (a worker restarts
  // after Kill the job; a cuOpt worker starts when chosen)
  $effect(() => {
    let stop = false;
    (async () => {
      while (!stop) {
        try {
          health = await api.health();
        } catch {
          health = null;
        }
        const ready = health?.reader?.state === "ready" && (!health.runner || ["ready", "error"].includes(health.runner.state));
        await sleep(ready ? 15000 : 2000);
      }
    })();
    return () => (stop = true);
  });

  async function follow(m: ModelInfo) {
    model = m;
    const t0 = performance.now();
    while (model && ["compiling", "loading", "uploaded"].includes(model.status)) {
      await sleep(250);
      elapsed = (performance.now() - t0) / 1000;
      model = await api.model(model.id);
    }
    if (model?.status === "ready") {
      const r = await api.rows(model.id, "REG", { limit: 500 }).catch(() => null);
      regions = r ? r.rows.map((x) => String(x[0])) : [];
      tab = "graph";
      focus = "";
    }
  }

  async function upload(files: File[]) {
    error = "";
    busy = true;
    try {
      await follow(await api.upload(files));
    } catch (e) {
      error = String(e);
    } finally {
      busy = false;
    }
  }

  async function demo() {
    error = "";
    busy = true;
    try {
      await follow(await api.demo());
    } catch (e) {
      error = String(e);
    } finally {
      busy = false;
    }
  }

  // the open models beside the demo (OTNYC, OTCGEN): the first time, downloaded from GitHub
  let openModels = $state<OpenModel[]>([]);
  api
    .openModels()
    .then((m) => (openModels = m))
    .catch(() => {});
  let opening = $state("");
  async function tryOpen(key: string) {
    error = "";
    busy = true;
    opening = key;
    try {
      await follow(await api.openModel(key));
    } catch (e) {
      error = String(e);
    } finally {
      busy = false;
      opening = "";
    }
  }

  // The address keeps the view (#model=<id>&tab=<tab>&focus=<node>), so a reload returns to it
  // and a link can point at it. #demo opens the demo model, #otnyc and #otcgen the open models
  // (for docs and tutorials).
  $effect(() => {
    const h = location.hash.slice(1);
    if (h === "demo") return void demo();
    if (h === "otnyc" || h === "otcgen") return void tryOpen(h);
    const p = new URLSearchParams(h);
    const id = p.get("model");
    if (!id) return;
    const t = p.get("tab") as Tab | null;
    const f = p.get("focus") ?? "";
    api
      .model(id)
      .then(follow)
      .then(() => {
        if (t) tab = t;
        focus = f;
      })
      .catch(() => history.replaceState(null, "", " "));
  });
  $effect(() => {
    if (model?.status !== "ready") return;
    const p = new URLSearchParams({ model: model.id, tab });
    if (tab === "graph" && focus) p.set("focus", focus);
    const want = `#${p}`;
    if (location.hash !== want) history.replaceState(null, "", want);
  });

  async function choose(run: string) {
    if (!model) return;
    busy = true;
    try {
      await follow(await api.chooseRun(model.id, run));
    } catch (e) {
      error = String(e);
    } finally {
      busy = false;
    }
  }

  function showInGraph(id: string) {
    focus = id;
    tab = "graph";
  }

  const STATE_TEXT: Record<string, string> = {
    starting: "starting",
    warming: "warming up",
    ready: "ready",
    error: "unavailable",
  };
  const COUNTS: [string, string][] = [
    ["REG", "regions"],
    ["PRC", "processes"],
    ["COM", "commodities"],
    ["MILESTONYR", "milestone years"],
    ["TOP", "topology links"],
  ];
</script>

<div class="app">
  <header bind:this={header}>
    <a class="logo" href="https://www.esma.ie" target="_blank" rel="noreferrer" title="ESMA">
      <img src={logo} alt="ESMA" width="91" height="54" />
    </a>
    <div class="brand">
      <strong>otui</strong>
      <span>Open TIMES User Interface</span>
    </div>
    {#if model?.status === "ready"}
      <div class="model">
        <span class="mono">{model.run}</span>
        <a class="btn" href={api.exportUrl(model.id, "xlsx")} download>Export .xlsx</a>
        <a class="btn" href={api.exportUrl(model.id, "zip")} download>Export CSV</a>
        <button onclick={() => ((model = null), (regions = []))}>Open another model</button>
      </div>
    {/if}
    <ThemePicker />
    <div class="engine" title={health ? `Julia ${health.julia ?? "?"} · TIMES.jl ${health.times_jl ?? "?"} (TIMES ${health.times_gams_version ?? "?"})` : ""}>
      {#if !health}
        <span class="dot err"></span> server not reachable
      {:else}
        <span class="dot {health.reader?.state}"></span> reader {STATE_TEXT[health.reader?.state ?? ""] ?? health.engine}
        {#if health.runner}
          <span class="dot {health.runner.state}"></span> solver {STATE_TEXT[health.runner.state]}
        {/if}
      {/if}
    </div>
  </header>

  {#if codespace && !hideHosting}
    <aside class="hosting" aria-label="Where otui runs">
      <span>
        <strong>Running in your GitHub codespace</strong>, a cloud computer under your GitHub account, not on
        your own computer. Models you add are uploaded to it. Only you can open this page, and ESMA cannot see
        your codespace. It uses your account's Codespaces allowance, stops when idle, and keeps your files until
        you delete it at <a href="https://github.com/codespaces" target="_blank" rel="noreferrer"
          >github.com/codespaces</a
        >.
        <a href="https://github.com/esma-ie/otui#what-runs-where" target="_blank" rel="noreferrer"
          >What runs where</a
        >
      </span>
      <button onclick={() => (hideHosting = true)} title="Hide until the page is reloaded">Hide</button>
    </aside>
  {/if}

  <main>
    {#if !model || model.status === "error"}
      <div class="start">
        <h1>Explore a TIMES model in your browser</h1>
        <p class="muted">
          Read with <a href="https://github.com/esma-ie/timesjl" target="_blank" rel="noreferrer">TIMES.jl</a>, the
          same way GAMS reads it.
          {#if codespace}Your files go to your codespace, not to ESMA.{:else if health}Nothing leaves this
            computer.{/if}
        </p>
        <DropZone onfiles={upload} {busy} />
        <p class="demo">
          No model to hand? <button onclick={demo} disabled={busy}>Try the demo model</button>
          {#each openModels as o (o.id)}
            <button
              onclick={() => tryOpen(o.id)}
              disabled={busy}
              title="{o.description}.{o.cached ? '' : ` The first time, otui downloads its run and DD files from GitHub (${fmt(o.bytes / 1e6, 0)} MB).`}"
            >
              Try {o.label}
            </button>
          {/each}
        </p>
        <ul class="muted models">
          <li><strong>demo</strong>: TIMES_Demo <code>demo12</code> (IEA-ETSAP, CC BY 4.0): 2 regions, 168 processes</li>
          {#each openModels as o (o.id)}
            <li>
              <strong>{o.label}</strong>: {o.description} (<a href={o.source} target="_blank" rel="noreferrer"
                >files on GitHub</a
              >{o.cached ? "" : `, ${fmt(o.bytes / 1e6, 0)} MB the first time`})
            </li>
          {/each}
        </ul>
        {#if model?.status === "error" && model.untranslated}
          <div class="panel pad untranslated">
            <Untranslated u={model.untranslated} stage="reading {model.run ?? 'the run file'}">
              <li>Run the model with GAMS TIMES, or open it in Veda, until TIMES.jl translates this.</li>
            </Untranslated>
          </div>
        {:else if model?.status === "error"}<p class="err">{model.error}</p>{/if}
        {#if error}<p class="err">{error}</p>{/if}
        {#if busy}<p class="muted">
            {opening && !openModels.find((o) => o.id === opening)?.cached
              ? `Downloading ${openModels.find((o) => o.id === opening)?.label} from GitHub, then reading it…`
              : opening
                ? "Reading the model…"
                : "Uploading…"}
          </p>{/if}
      </div>
    {:else if model.status === "needs_run"}
      <div class="start panel pad">
        <h2>Which run file?</h2>
        <p class="muted">The upload has more than one TIMES run file.</p>
        <div class="choices">
          {#each model.run_candidates ?? [] as r}<button onclick={() => choose(r)} disabled={busy}>{r}</button>{/each}
        </div>
      </div>
    {:else if model.status !== "ready"}
      <div class="start panel pad">
        <h2>{model.status === "loading" ? "Loading tables…" : "Reading the model with TIMES.jl…"}</h2>
        <p class="muted">{model.run} · {model.files.length} files · {fmt(elapsed)} s</p>
        {#if health?.reader?.state !== "ready"}
          <p class="muted">TIMES.jl is still starting; the first model of a session takes longer.</p>
        {/if}
      </div>
    {:else}
      <section class="summary">
        {#each COUNTS as [k, label]}
          {#if model.summary?.counts[k] != null}
            <div class="card panel"><strong>{fmt(model.summary.counts[k], 0)}</strong><span class="muted">{label}</span></div>
          {/if}
        {/each}
        <div class="card panel"><strong>{fmt(model.summary?.tables, 0)}</strong><span class="muted">tables with data</span></div>
        <div class="card panel"><strong>{fmt(model.timings.compile, 1)} s</strong><span class="muted">read by TIMES.jl</span></div>
      </section>

      <div class="tabs" role="tablist">
        {#each [["graph", "RES graph"], ["tables", "Sets & parameters"], ["qc", "QC"], ["run", "Run"], ["results", "Results"]] as [t, label]}
          <button role="tab" aria-selected={tab === t} class:active={tab === t} onclick={() => (tab = t as Tab)}>{label}</button>
        {/each}
      </div>

      <div class="tab">
        {#if tab === "graph"}
          <Graph modelId={model.id} bind:focus {regions} solveKey={model.solve?.status ?? ""} />
        {:else if tab === "tables"}
          <Tables modelId={model.id} />
        {:else if tab === "qc"}
          <QC modelId={model.id} ran={model.solve?.status === "done"} onfocus={showInGraph} />
        {:else if tab === "run"}
          <Run {model} {health} onupdate={(m) => (model = m)} />
        {:else}
          <Results modelId={model.id} solveKey={`${model.solve?.status ?? ""}:${model.solve?.solver ?? ""}:${model.solve?.result?.objective ?? ""}`} />
        {/if}
      </div>
    {/if}
  </main>

  <footer class="muted">
    otui {health?.otui ?? ""} · AGPL-3.0-or-later (<a
      href="https://github.com/esma-ie/otui"
      target="_blank"
      rel="noopener">source code</a
    >, <a href="THIRD-PARTY-NOTICES.txt" target="_blank" rel="noopener">third-party notices</a
    >) · TIMES models are read and solved by TIMES.jl
    {#if timesjl}
      <span title={timesjl.fallback ?? ""}>
        {timesjl.channel === "latest" ? "(latest: main" : "(pinned"}{timesjl.commit ? ` @ ${timesjl.commit.slice(0, 7)}` : ""}){#if timesjl.fallback}<span class="warnish">
            · not on the latest TIMES.jl: {timesjl.fallback}</span
          >{/if}
      </span>
    {/if}
    {#if health?.times_jl_update?.state === "staged"}
      · TIMES.jl's newest main ({health.times_jl_update.commit?.slice(0, 7)}) is ready from the next start
    {/if}
  </footer>
</div>

<style>
  .app {
    display: flex;
    flex-direction: column;
    min-height: 100vh;
  }
  /* The ESMA bar: dark green, the primary logo's own background, with a yellow rule */
  header {
    display: flex;
    gap: 1rem;
    align-items: center;
    flex-wrap: wrap;
    padding: 0.45rem 1rem;
    border-bottom: 3px solid var(--rule);
    background: var(--bar);
    color: var(--bar-ink);
  }
  .logo {
    display: flex;
    flex: none;
  }
  .logo img {
    display: block;
    height: 54px; /* 91 px wide: the brand's screen minimum is 90 px */
    width: auto;
  }
  .brand {
    display: flex;
    flex-direction: column;
    line-height: 1.2;
  }
  .brand strong {
    font-size: 1.2rem;
    font-weight: 700;
    color: var(--bar-accent);
  }
  .brand span {
    font-size: 0.85rem;
  }
  header .mono {
    color: var(--bar-ink);
  }
  header button,
  header .btn {
    background: transparent;
    color: var(--bar-ink);
    border-color: rgb(255 255 255 / 0.35);
  }
  header button:hover,
  header .btn:hover {
    border-color: var(--bar-accent);
  }
  .model {
    display: flex;
    gap: 0.4rem;
    align-items: center;
    flex-wrap: wrap;
  }
  .btn {
    border: 1px solid var(--line);
    border-radius: 6px;
    padding: 0.35rem 0.75rem;
    text-decoration: none;
    color: var(--ink);
  }
  .btn:hover {
    border-color: var(--accent);
  }
  .engine {
    display: flex;
    gap: 0.35rem;
    align-items: center;
    font-size: 0.85rem;
    color: var(--bar-ink);
  }
  .dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: var(--bar-accent);
    display: inline-block;
    margin-left: 0.4rem;
  }
  .dot.ready {
    background: var(--bar-ok);
  }
  .dot.error,
  .dot.err {
    background: var(--bar-error);
  }
  /* the codespace notice: where models go, under whose account */
  .hosting {
    display: flex;
    gap: 0.75rem;
    align-items: flex-start;
    padding: 0.5rem 1rem;
    font-size: 0.85rem;
    background: var(--panel);
    border-bottom: 1px solid var(--line);
    border-left: 4px solid var(--warn);
  }
  .hosting span {
    flex: 1;
  }
  .hosting button {
    flex: none;
  }
  main {
    flex: 1;
    display: flex;
    flex-direction: column;
    gap: 0.75rem;
    padding: 1rem;
    min-height: 0;
  }
  .start {
    max-width: 680px;
    width: 100%;
    margin: 2rem auto;
  }
  .untranslated {
    margin-top: 1rem;
  }
  .pad {
    padding: 1.25rem 1.5rem;
  }
  h1 {
    font-size: 1.5rem;
    margin: 0 0 0.3rem;
    color: var(--heading);
  }
  h2 {
    margin: 0 0 0.3rem;
    color: var(--heading);
  }
  .models {
    font-size: 0.85rem;
    margin: 0.5rem 0 0;
    padding-left: 1.1rem;
  }
  .models li {
    margin: 0.15rem 0;
  }
  .models strong {
    color: var(--text, inherit);
  }
  .demo {
    display: flex;
    gap: 0.5rem;
    align-items: center;
    flex-wrap: wrap;
    margin-top: 0.75rem;
  }
  .choices {
    display: flex;
    gap: 0.5rem;
    flex-wrap: wrap;
  }
  .summary {
    display: flex;
    gap: 0.6rem;
    flex-wrap: wrap;
  }
  .card {
    display: flex;
    flex-direction: column;
    padding: 0.5rem 0.9rem;
    min-width: 110px;
  }
  .card strong {
    font-size: 1.25rem;
    color: var(--heading);
  }
  .card span {
    font-size: 0.8rem;
  }
  .tabs {
    display: flex;
    gap: 0.25rem;
    border-bottom: 1px solid var(--line);
    overflow-x: auto;
  }
  .tabs button {
    border: 0;
    border-bottom: 2px solid transparent;
    border-radius: 0;
    background: transparent;
    padding: 0.45rem 0.9rem;
    white-space: nowrap;
  }
  .tabs button.active {
    border-bottom: 3px solid var(--rule);
    color: var(--heading);
    font-weight: 700;
  }
  .tab {
    flex: 1;
    min-height: 560px;
    display: flex;
    flex-direction: column;
  }
  .tab > :global(*) {
    flex: 1;
  }
  .err {
    color: var(--error);
  }
  .warnish {
    color: var(--warn);
  }
  footer {
    padding: 0.5rem 1rem;
    font-size: 0.8rem;
    border-top: 1px solid var(--line);
  }
  @media (max-width: 640px) {
    main {
      padding: 0.75rem 1rem;
    }
    .engine {
      margin-left: 0;
    }
  }
</style>
