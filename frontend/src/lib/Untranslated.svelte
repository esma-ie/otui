<script lang="ts">
  // A TIMES feature TIMES.jl does not translate yet: what it is, what in the model brings it in, and
  // what the user can do. Shown when the reader stops on it (an upload) or a run does (Run tab).
  import type { Snippet } from "svelte";
  import { fmt, type Untranslated } from "./api";

  let { u, stage, children }: { u: Untranslated; stage: string; children?: Snippet } = $props();
</script>

<div class="untr">
  <p class="big warn">This model uses a TIMES feature that TIMES.jl does not translate yet</p>
  <p class="what"><strong>{u.what}</strong></p>
  <p class="muted small">
    TIMES.jl stopped {stage}{#if u.gams_files.length}, at the GAMS code of TIMES it has not translated yet:
      {#each u.gams_files as f, i (f)}{i ? ", " : ""}<code>{f}</code>{/each}{/if}.
  </p>
  {#if u.in_model.length}
    <p>
      <strong>In this model:</strong>
      {#each u.in_model as s, i (s.name)}{i ? ", " : ""}<code>{s.name}</code> ({fmt(s.records, 0)} record{s.records === 1 ? "" : "s"}){/each}: the data
      that brings this feature in (see the Sets &amp; parameters tab).
    </p>
  {/if}
  {#if u.switches.length}
    <p>
      <strong>Run switches:</strong>
      {#each u.switches as s, i (s.name)}{i ? ", " : ""}<code>%{s.name}%</code>
        {s.value ? `set to ${s.value} in the run file` : "not set in the run file"}{/each}.
    </p>
  {/if}
  <p class="todo-head"><strong>What you can do:</strong></p>
  <ul class="todo">
    {@render children?.()}
    <li>
      See what TIMES.jl translates so far (<a href={u.coverage} target="_blank" rel="noreferrer">its coverage table</a>), and
      <a href={u.issues} target="_blank" rel="noreferrer">whether it is already asked for</a>.
    </li>
    <li>
      <a href={u.ask} target="_blank" rel="noreferrer">Ask TIMES.jl for it</a>: a new issue on GitHub, with the feature
      filled in.
    </li>
  </ul>
</div>

<style>
  .big {
    font-size: 1.15rem;
    margin: 0 0 0.5rem;
  }
  .warn {
    color: var(--warn);
  }
  .what {
    margin: 0 0 0.3rem;
  }
  .small {
    font-size: 0.85rem;
    margin: 0 0 0.5rem;
  }
  .todo-head {
    margin: 0.5rem 0 0;
  }
  ul.todo {
    margin: 0.2rem 0 0.4rem;
    padding-left: 1.2rem;
  }
  ul.todo :global(li) {
    margin: 0.2rem 0;
  }
  ul.todo :global(button) {
    margin-left: 0.3rem;
    padding: 0.1rem 0.5rem;
  }
</style>
