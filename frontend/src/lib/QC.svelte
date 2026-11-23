<script lang="ts">
  import { api, type QCReport } from "./api";

  let { modelId, ran = false, onfocus }: { modelId: string; ran?: boolean; onfocus: (id: string) => void } =
    $props();

  let report = $state<QCReport | null>(null);
  let error = $state("");
  const SEV_CLASS = (s: string) => (/FATAL|ERROR/.test(s) ? "error" : /WARNING/.test(s) ? "warn" : "info");

  $effect(() => {
    void ran;
    api.qc(modelId).then((r) => (report = r)).catch((e) => (error = String(e)));
  });

  let groups = $derived.by(() => {
    const g = new Map<string, QCReport["issues"]>();
    for (const i of report?.issues ?? []) {
      const k = `${i.severity}|${i.source}|${i.check}`;
      (g.get(k) ?? g.set(k, []).get(k)!).push(i);
    }
    return [...g.entries()];
  });
</script>

<div class="qc">
  {#if error}<p class="err">{error}</p>{/if}
  {#if report}
    <div class="summary">
      {#each Object.entries(report.counts) as [sev, n]}
        <span class="pill {SEV_CLASS(sev)}">{n} {sev.toLowerCase()}{n === 1 ? "" : "s"}</span>
      {:else}
        <span class="pill ok">No issues found</span>
      {/each}
      {#if !report.times_log}
        <span class="muted">TIMES's own quality checks (QA_CHECK.LOG) appear after a run.</span>
      {/if}
    </div>
    {#each groups as [key, items] (key)}
      {@const [sev, source, check] = key.split("|")}
      <section class="panel">
        <h4><span class="pill {SEV_CLASS(sev)}">{sev}</span> {check} <span class="muted src">{source === "TIMES" ? "TIMES QA log" : "otui structure check"} · {items.length}</span></h4>
        <ul>
          {#each items as i}
            <li>
              {#if i.item}
                <button class="mono link" onclick={() => onfocus(i.item)} title="Show in the graph">{i.message}</button>
              {:else}
                <span class="mono">{i.message}</span>
              {/if}
            </li>
          {/each}
        </ul>
      </section>
    {/each}
  {/if}
</div>

<style>
  .qc {
    display: flex;
    flex-direction: column;
    gap: 0.75rem;
  }
  .summary {
    display: flex;
    gap: 0.5rem;
    align-items: center;
    flex-wrap: wrap;
  }
  .pill {
    font-size: 0.8rem;
    padding: 0.1rem 0.55rem;
    border-radius: 999px;
    border: 1px solid currentColor;
    font-weight: 600;
  }
  .pill.error {
    color: var(--error);
  }
  .pill.warn {
    color: var(--warn);
  }
  .pill.info,
  .pill.ok {
    color: var(--ok);
  }
  section {
    padding: 0.6rem 0.9rem;
  }
  h4 {
    margin: 0 0 0.4rem;
    display: flex;
    gap: 0.5rem;
    align-items: center;
    flex-wrap: wrap;
    font-weight: 600;
  }
  .src {
    font-weight: 400;
    font-size: 0.85rem;
  }
  ul {
    margin: 0;
    padding-left: 1.1rem;
  }
  li {
    margin: 0.15rem 0;
  }
  .link {
    border: 0;
    padding: 0;
    background: none;
    color: var(--accent);
    text-decoration: underline;
  }
  .err {
    color: var(--error);
  }
</style>
