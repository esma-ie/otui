<script lang="ts">
  // The Results tab's filters, in one row above the charts: a button per dimension opens a list to
  // search and tick; what is ticked shows as chips to click away. Shared by every section.
  import { DIM_LABEL, type Dim, type Facets, type Filters } from "./results";

  let { facets, filters = $bindable() }: { facets: Facets | null; filters: Filters } = $props();

  const DIMS: Dim[] = ["process_set", "process", "commodity_set", "commodity", "period", "timeslice", "region"];
  const SHOWN = 200; // a long list (thousands of processes) shows this many matches; search for the rest
  let open = $state<Dim | null>(null);
  let q = $state("");

  function options(d: Dim): { id: string; description: string }[] {
    const f = facets?.[d] ?? [];
    return (f as (string | { id: string; description: string })[]).map((x) => (typeof x === "string" ? { id: x, description: "" } : x));
  }
  let matches = $derived.by(() => {
    if (!open) return [];
    const s = q.trim().toLowerCase();
    const all = options(open);
    return s ? all.filter((o) => o.id.toLowerCase().includes(s) || o.description.toLowerCase().includes(s)) : all;
  });

  function toggle(d: Dim, v: string) {
    const cur = filters[d] ?? [];
    filters = { ...filters, [d]: cur.includes(v) ? cur.filter((x) => x !== v) : [...cur, v] };
  }
  function only(d: Dim, vs: string[]) {
    filters = { ...filters, [d]: vs };
  }
  let chips = $derived(DIMS.flatMap((d) => (filters[d] ?? []).map((v) => ({ d, v }))));
  const count = (d: Dim) => (filters[d] ?? []).length;

  function openDim(d: Dim) {
    q = "";
    open = open === d ? null : d;
  }
  function onkey(e: KeyboardEvent) {
    if (e.key === "Escape") open = null;
  }
</script>

<svelte:window onkeydown={onkey} />

<div class="filters">
  <span class="muted lead">Filter</span>
  {#each DIMS as d}
    {#if options(d).length > 1 || count(d)}
      <div class="dim">
        <button class:on={count(d) > 0} aria-expanded={open === d} onclick={() => openDim(d)}>
          {DIM_LABEL[d]}{#if count(d)}<span class="n">{count(d)}</span>{/if} ▾
        </button>
        {#if open === d}
          <div class="pop panel" role="dialog" aria-label={`Filter by ${DIM_LABEL[d].toLowerCase()}`}>
            <!-- svelte-ignore a11y_autofocus -->
            <input type="search" placeholder={`Search ${options(d).length} ${DIM_LABEL[d].toLowerCase()}s`} bind:value={q} autofocus />
            <div class="acts">
              <button class="link" onclick={() => only(d, matches.map((o) => o.id))}>Tick {q ? "these" : "all"}</button>
              <button class="link" onclick={() => only(d, [])}>Clear</button>
              <button class="link close" onclick={() => (open = null)}>Done</button>
            </div>
            <ul>
              {#each matches.slice(0, SHOWN) as o (o.id)}
                <li>
                  <label>
                    <input type="checkbox" checked={(filters[d] ?? []).includes(o.id)} onchange={() => toggle(d, o.id)} />
                    <span class="mono">{o.id}</span>
                    {#if o.description}<span class="muted desc">{o.description}</span>{/if}
                  </label>
                </li>
              {/each}
              {#if matches.length > SHOWN}<li class="muted more">{matches.length - SHOWN} more: search to narrow</li>{/if}
              {#if !matches.length}<li class="muted more">None match</li>{/if}
            </ul>
          </div>
        {/if}
      </div>
    {/if}
  {/each}
  {#each chips as c (c.d + c.v)}
    <button class="chip" title={`${DIM_LABEL[c.d]}: ${c.v} (click to remove)`} onclick={() => toggle(c.d, c.v)}>
      <span class="muted">{DIM_LABEL[c.d]}</span>
      {c.v} ✕
    </button>
  {/each}
  {#if chips.length}<button class="link" onclick={() => (filters = {})}>Clear all</button>{/if}
</div>

<style>
  .filters {
    display: flex;
    flex-wrap: wrap;
    gap: 0.4rem;
    align-items: center;
  }
  .lead {
    font-size: 0.85rem;
    margin-right: 0.1rem;
  }
  .dim {
    position: relative;
  }
  .dim > button {
    font-size: 0.85rem;
    padding: 0.25rem 0.6rem;
  }
  .dim > button.on {
    border-color: var(--accent);
    font-weight: 600;
  }
  .n {
    display: inline-block;
    margin-left: 0.35rem;
    padding: 0 0.4rem;
    border-radius: 999px;
    background: var(--accent);
    color: var(--accent-ink);
    font-size: 0.75rem;
  }
  .pop {
    position: absolute;
    z-index: 20;
    top: calc(100% + 4px);
    left: 0;
    width: 340px;
    max-width: 90vw;
    padding: 0.5rem;
    box-shadow: 0 6px 24px rgb(0 0 0 / 0.18);
  }
  .pop input[type="search"] {
    width: 100%;
    padding: 0.3rem 0.5rem;
    border: 1px solid var(--line);
    border-radius: 6px;
    background: var(--bg);
  }
  .acts {
    display: flex;
    gap: 0.6rem;
    margin: 0.35rem 0;
    font-size: 0.8rem;
  }
  .acts .close {
    margin-left: auto;
  }
  ul {
    list-style: none;
    margin: 0;
    padding: 0;
    max-height: 300px;
    overflow: auto;
  }
  li label {
    display: flex;
    gap: 0.4rem;
    align-items: baseline;
    padding: 0.15rem 0.2rem;
    cursor: pointer;
    font-size: 0.85rem;
  }
  li label:hover {
    background: var(--bg);
  }
  .desc {
    font-size: 0.78rem;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .more {
    font-size: 0.8rem;
    padding: 0.3rem;
  }
  .chip {
    font-size: 0.8rem;
    padding: 0.15rem 0.55rem;
    border-radius: 999px;
    border-color: var(--accent);
  }
  .chip .muted {
    font-size: 0.72rem;
    margin-right: 0.2rem;
  }
  button.link {
    background: none;
    border: none;
    padding: 0;
    color: var(--accent);
    text-decoration: underline;
    cursor: pointer;
    font-size: 0.8rem;
  }
</style>
