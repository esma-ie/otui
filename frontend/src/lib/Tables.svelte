<script lang="ts">
  import { api, type Rows, type SymbolInfo } from "./api";

  let { modelId, initial = "" }: { modelId: string; initial?: string } = $props();

  const PAGE = 100;
  let symbols = $state<SymbolInfo[]>([]);
  let filter = $state("");
  let kind = $state<"all" | "set" | "parameter">("all");
  let withData = $state(true);
  let selected = $state<string>("");
  let rows = $state<Rows | null>(null);
  let offset = $state(0);
  let q = $state("");
  let where = $state<string[]>([]);
  let error = $state("");
  let loading = $state(false);

  $effect(() => {
    api.symbols(modelId).then((s) => {
      symbols = s.filter((x) => x.kind === "set" || x.kind === "parameter");
      selected = initial || symbols.find((x) => x.name === "TOP")?.name || "";
    });
  });

  let shown = $derived(
    symbols
      .filter((s) => (kind === "all" || s.kind === kind) && (!withData || s.has_table))
      .filter((s) => {
        const f = filter.trim().toUpperCase();
        return !f || s.name.includes(f) || s.description.toUpperCase().includes(f);
      })
      .sort((a, b) => a.name.localeCompare(b.name)),
  );
  let current = $derived(symbols.find((s) => s.name === selected));

  async function load() {
    if (!selected) return;
    loading = true;
    error = "";
    try {
      rows = await api.rows(modelId, selected, { offset, limit: PAGE, q: q.trim(), where });
    } catch (e) {
      error = String(e);
    } finally {
      loading = false;
    }
  }

  $effect(() => {
    // reload when any of these change
    void [selected, offset, where.length, q];
    load();
  });

  function pick(name: string) {
    selected = name;
    offset = 0;
    q = "";
    where = [];
  }

  function addFilter(col: string, value: string | number) {
    if (col === "value" || col === "element_text") return;
    const w = `${col}:${value}`;
    if (!where.includes(w)) where = [...where, w];
    offset = 0;
  }
</script>

<div class="tables">
  <aside class="panel">
    <input placeholder="Find a set or parameter" bind:value={filter} aria-label="Find a symbol" />
    <div class="opts">
      <select bind:value={kind} aria-label="Kind">
        <option value="all">All</option>
        <option value="set">Sets</option>
        <option value="parameter">Parameters</option>
      </select>
      <label><input type="checkbox" bind:checked={withData} /> with data</label>
    </div>
    <ul>
      {#each shown as s (s.name)}
        <li>
          <button class:active={s.name === selected} onclick={() => pick(s.name)} disabled={!s.has_table} title={s.description}>
            <span class="mono">{s.name}</span>
            <span class="badge {s.kind}">{s.kind === "set" ? "set" : "par"}</span>
            <span class="count">{s.records.toLocaleString()}</span>
          </button>
        </li>
      {/each}
    </ul>
    <p class="muted small">{shown.length} of {symbols.length}</p>
  </aside>

  <section class="panel view">
    {#if current}
      <header>
        <h3 class="mono">{current.name}</h3>
        <span class="badge {current.kind}">{current.kind}</span>
        <span class="muted">{current.description}</span>
      </header>
      <div class="bar">
        <input placeholder="Search in this table" bind:value={q} onchange={() => (offset = 0)} aria-label="Search rows" />
        {#each where as w (w)}
          <button class="chip" onclick={() => (where = where.filter((x) => x !== w))} title="Remove filter">
            {w.replace(":", " = ")} ✕
          </button>
        {/each}
        {#if rows}
          <span class="muted pager">
            {rows.total ? offset + 1 : 0}–{Math.min(offset + PAGE, rows.total)} of {rows.total.toLocaleString()}
            <button disabled={offset === 0} onclick={() => (offset = Math.max(0, offset - PAGE))}>‹</button>
            <button disabled={offset + PAGE >= rows.total} onclick={() => (offset += PAGE)}>›</button>
          </span>
        {/if}
      </div>
      {#if error}<p class="err">{error}</p>{/if}
      {#if rows}
        <div class="scroll" class:loading>
          <table>
            <thead>
              <tr>{#each rows.columns as c}<th class:num={c === "value"}>{c}</th>{/each}</tr>
            </thead>
            <tbody>
              {#each rows.rows as r, i (offset + i)}
                <tr>
                  {#each r as v, j}
                    <td
                      class:num={rows.columns[j] === "value"}
                      class:special={typeof v === "string" && rows.columns[j] === "value"}
                      onclick={() => addFilter(rows!.columns[j], v)}
                    >{v}</td>
                  {/each}
                </tr>
              {/each}
            </tbody>
          </table>
        </div>
        <p class="muted small">Click a cell to filter on its value.</p>
      {/if}
    {:else}
      <p class="muted">Choose a set or parameter.</p>
    {/if}
  </section>
</div>

<style>
  .tables {
    display: grid;
    grid-template-columns: minmax(220px, 280px) 1fr;
    gap: 1rem;
    min-height: 0;
    height: 100%;
  }
  aside {
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
    padding: 0.75rem;
    min-height: 0;
  }
  .opts {
    display: flex;
    gap: 0.5rem;
    align-items: center;
    font-size: 0.9rem;
  }
  ul {
    list-style: none;
    margin: 0;
    padding: 0;
    overflow: auto;
    flex: 1;
  }
  li button {
    display: flex;
    width: 100%;
    gap: 0.4rem;
    align-items: center;
    border: 0;
    border-radius: 4px;
    padding: 0.2rem 0.4rem;
    text-align: left;
    background: transparent;
  }
  li button.active {
    background: color-mix(in srgb, var(--accent) 15%, transparent);
  }
  .count {
    margin-left: auto;
    color: var(--muted);
    font-size: 0.85rem;
  }
  .badge {
    font-size: 0.7rem;
    padding: 0 0.3rem;
    border-radius: 3px;
    border: 1px solid var(--line);
    color: var(--muted);
  }
  .badge.set {
    border-color: var(--com-dem);
    color: var(--com-dem);
  }
  .badge.parameter {
    border-color: var(--com-nrg);
    color: var(--com-nrg);
  }
  .view {
    padding: 0.75rem 1rem;
    display: flex;
    flex-direction: column;
    min-height: 0;
    min-width: 0;
  }
  header {
    display: flex;
    gap: 0.6rem;
    align-items: baseline;
    flex-wrap: wrap;
  }
  h3 {
    margin: 0;
  }
  .bar {
    display: flex;
    gap: 0.4rem;
    align-items: center;
    flex-wrap: wrap;
    margin: 0.6rem 0;
  }
  .chip {
    font-size: 0.85rem;
    padding: 0.15rem 0.5rem;
    border-radius: 999px;
  }
  .pager {
    margin-left: auto;
    display: flex;
    gap: 0.3rem;
    align-items: center;
  }
  .scroll {
    overflow: auto;
    flex: 1;
    border: 1px solid var(--line);
    border-radius: 6px;
  }
  .scroll.loading {
    opacity: 0.6;
  }
  table {
    border-collapse: collapse;
    width: 100%;
    font-family: var(--mono);
    font-size: 0.85rem;
  }
  th {
    position: sticky;
    top: 0;
    background: var(--panel);
    text-align: left;
    border-bottom: 1px solid var(--line);
    padding: 0.35rem 0.6rem;
  }
  td {
    padding: 0.2rem 0.6rem;
    border-bottom: 1px solid color-mix(in srgb, var(--line) 60%, transparent);
    cursor: pointer;
    white-space: nowrap;
  }
  tr:hover td {
    background: color-mix(in srgb, var(--accent) 6%, transparent);
  }
  .num {
    text-align: right;
  }
  .special {
    color: var(--warn);
  }
  .err {
    color: var(--error);
  }
  .small {
    font-size: 0.85rem;
    margin: 0.3rem 0 0;
  }
  @media (max-width: 760px) {
    .tables {
      grid-template-columns: 1fr;
    }
    aside {
      max-height: 40vh;
    }
  }
</style>
