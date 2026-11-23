<script lang="ts">
  // A company's colours for the whole app: paste its website, and the otui server reads the
  // site's style sheets and makes light and dark themes from its brand colours (otui/theme.py).
  // The theme is a style element over the ESMA one, remembered in this browser; Reset removes it.
  import { api, type Theme } from "./api";
  import ModeToggle from "./ModeToggle.svelte";

  const KEY = "otui.theme";
  let url = $state("");
  let busy = $state(false);
  let error = $state("");
  let current = $state<Theme | null>(null);

  function css(t: Theme) {
    const block = (vars: Record<string, string>) =>
      Object.entries(vars)
        .map(([k, v]) => `${k}: ${v};`)
        .join(" ");
    return `:root { ${block(t.light)} }
:root[data-theme="dark"] { ${block(t.dark)} }`;
  }

  function apply(t: Theme | null) {
    let el = document.getElementById("otui-theme");
    if (!t) el?.remove();
    else {
      if (!el) {
        el = document.createElement("style");
        el.id = "otui-theme";
        document.head.appendChild(el); // after app.css, so it wins
      }
      el.textContent = css(t);
    }
    current = t;
    window.dispatchEvent(new Event("otui-theme")); // the graph canvas re-reads its colours
  }

  // the theme this browser last used
  try {
    const saved = localStorage.getItem(KEY);
    if (saved) apply(JSON.parse(saved));
  } catch {
    /* no storage, or an old entry: the ESMA theme */
  }

  async function submit(e: Event) {
    e.preventDefault();
    if (!url.trim()) return;
    busy = true;
    error = "";
    try {
      const t = await api.theme(url.trim());
      apply(t);
      try {
        localStorage.setItem(KEY, JSON.stringify(t));
      } catch {
        /* the theme still applies for this visit */
      }
      url = "";
    } catch (err) {
      error = String(err).replace(/^Error: /, "");
    } finally {
      busy = false;
    }
  }

  function reset() {
    apply(null);
    try {
      localStorage.removeItem(KEY);
    } catch {
      /* nothing stored */
    }
  }

  const host = (u: string) => {
    try {
      return new URL(u).hostname.replace(/^www\./, "");
    } catch {
      return u;
    }
  };
</script>

<div class="theme">
  <ModeToggle />
  <form onsubmit={submit} title="otui reads that page's style sheets (only when you press Apply) and recolours itself with the company's brand colours">
    {#if current}
      <span class="swatches" role="img" aria-label="Colours from {host(current.source)}" title="Colours from {host(current.source)}">
        {#each current.brand.slice(0, 4) as c}<i style="background: {c}"></i>{/each}
      </span>
      <button type="button" onclick={reset} title="Back to the ESMA colours">Reset</button>
    {/if}
    <input type="url" placeholder="Colours from a website" bind:value={url} aria-label="Company website for colours" disabled={busy} />
    <button type="submit" disabled={busy || !url.trim()}>{busy ? "Reading…" : "Apply"}</button>
    {#if error}<span class="err" role="alert">{error}</span>{/if}
  </form>
</div>

<style>
  .theme {
    display: flex;
    gap: 0.6rem;
    align-items: center;
    flex-wrap: wrap;
    margin-left: auto;
  }
  form {
    display: flex;
    gap: 0.35rem;
    align-items: center;
    flex-wrap: wrap;
    font-size: 0.85rem;
  }
  input {
    width: 175px;
    padding: 0.3rem 0.5rem;
    border-radius: 6px;
    border: 1px solid rgb(255 255 255 / 0.35);
    background: rgb(255 255 255 / 0.1);
    color: var(--bar-ink);
  }
  input::placeholder {
    color: rgb(255 255 255 / 0.7);
  }
  input:focus {
    outline: 2px solid var(--bar-accent);
    outline-offset: 1px;
  }
  button {
    background: transparent;
    color: var(--bar-ink);
    border-color: rgb(255 255 255 / 0.35);
    padding: 0.3rem 0.6rem;
  }
  button:hover:not(:disabled) {
    border-color: var(--bar-accent);
  }
  .swatches {
    display: inline-flex;
  }
  .swatches i {
    width: 12px;
    height: 18px;
    display: inline-block;
  }
  .swatches i:first-child {
    border-radius: 3px 0 0 3px;
  }
  .swatches i:last-child {
    border-radius: 0 3px 3px 0;
  }
  .err {
    background: var(--bar-error);
    color: #0b0b0b;
    padding: 0.1rem 0.4rem;
    border-radius: 4px;
    max-width: 320px;
  }
</style>
