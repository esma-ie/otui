<script lang="ts">
  // VEDA's dummy imports. Every TIMES model has them available, so they are only highlighted when
  // the solution takes them at a scale that compares with demand (otui.dummies.SIGNIFICANT, 1e-4);
  // otherwise a quiet line says they are there, not drawn, and what the solution took.
  import { api, fmt, type Dummies, type DummyScale } from "./api";

  let { modelId, solveKey = "" }: { modelId: string; solveKey?: string } = $props();
  let d = $state<Dummies | null>(null);

  $effect(() => {
    void solveKey;
    api
      .dummies(modelId)
      .then((x) => (d = x))
      .catch(() => (d = null));
  });

  const KIND: Record<string, string> = { NRG: "energy", DEM: "demand", MAT: "materials", ENV: "emissions" };
  const span = (h: string[] = []) => [...new Set(h)].join("–");
  const amount = (v: number) => fmt(v, Math.abs(v) >= 100 ? 0 : 2);
  const share = (r: number) =>
    r >= 0.001 ? `${fmt(r * 100, r >= 0.1 ? 0 : 2)}%` : `${r.toExponential(0).replace("e-", " × 10⁻")}`;
  const totals = $derived(
    (d?.totals ?? []).map((t) => `${amount(t.total)} ${t.unit} of ${KIND[t.type] ?? (t.type || "commodities")}`),
  );
  const totalText = $derived(
    totals.length > 1 ? `${totals.slice(0, -1).join(", ")} and ${totals.at(-1)}` : (totals[0] ?? ""),
  );
  const big = $derived((d?.scale ?? []).filter((s) => s.significant));
  const worst = $derived(
    (d?.scale ?? []).reduce<DummyScale | null>((a, s) => ((s.ratio ?? 0) > (a?.ratio ?? -1) ? s : a), null),
  );
</script>

{#if d && d.processes.length}
  {#if d.significant}
    <div class="dummy" role="alert">
      <p>
        <span class="icon" aria-hidden="true">⚠</span>
        <strong>Dummy imports supply {totalText} over {span(d.horizon)}</strong>:
        {#each big as s, i}{i ? "; " : ""}up to {amount(s.annual)} {s.unit} a year{#if s.ratio != null},
            <strong>{share(s.ratio)} of the model's demand in {s.unit}</strong> ({amount(s.demand ?? 0)} {s.unit} a
            year){:else}
            (no demand in {s.unit} to compare with){/if}{/each}. The model is short of real supply: these amounts come
        from {(d.suppliers ?? d.processes).join(", ")} at a penalty cost.
      </p>
      <p>
        Largest:
        {#each (d.commodities ?? []).slice(0, 5) as c, i}{i ? ", " : ""}<span class="mono">{c.commodity}</span>
          {amount(c.total)} {c.unit}{/each}{(d.commodities?.length ?? 0) > 5 ? ", …" : ""}. Each one is listed in the QC
        tab.
      </p>
    </div>
  {:else}
    <p class="quiet">
      Dummy imports ({d.processes.join(", ")}) are not drawn: like every TIMES model, this one can fall back on them to
      stay feasible.
      {#if !d.solved}
        Solving shows whether it does.
      {:else if !d.used}
        The solution takes nothing from them.
      {:else if worst}
        The solution takes a negligible amount: at most {amount(worst.annual)} {worst.unit} a year{#if worst.ratio != null},
          {share(worst.ratio)} of demand{/if}.
      {/if}
    </p>
  {/if}
{/if}

<style>
  /* ESMA's notice style (slides): dark yellow with dark green text, a dark green rule */
  .dummy {
    padding: 0.55rem 0.9rem;
    border-radius: var(--radius);
    background: var(--notice);
    color: var(--notice-ink);
    border-left: 10px solid var(--notice-ink);
    box-shadow: 0 0 0 2px var(--notice-ink);
    font-size: 1.02rem;
  }
  p {
    margin: 0.15rem 0;
  }
  strong {
    color: var(--notice-ink);
    font-weight: 700;
  }
  .icon {
    font-size: 1.15em;
    margin-right: 0.25rem;
  }
  .dummy :global(.mono) {
    color: var(--notice-ink);
  }
  .quiet {
    margin: 0;
    font-size: 0.85rem;
    color: var(--muted);
  }
</style>
