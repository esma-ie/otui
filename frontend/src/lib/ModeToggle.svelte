<script lang="ts">
  // Two buttons in the header, Light and Dark; the pressed one is the mode showing (lib/mode.ts).
  import { mode, setMode, type Mode } from "./mode";

  let current = $state<Mode>(mode());
  $effect(() => {
    const sync = () => (current = mode());
    window.addEventListener("otui-theme", sync);
    return () => window.removeEventListener("otui-theme", sync);
  });
</script>

<div class="mode" role="group" aria-label="Colour mode">
  <button type="button" aria-pressed={current === "light"} onclick={() => setMode("light")} title="Light mode">
    <svg viewBox="0 0 16 16" aria-hidden="true"
      ><circle cx="8" cy="8" r="3" /><path
        d="M8 1v2M8 13v2M1 8h2M13 8h2M3 3l1.4 1.4M11.6 11.6 13 13M3 13l1.4-1.4M11.6 4.4 13 3"
      /></svg
    >Light
  </button>
  <button type="button" aria-pressed={current === "dark"} onclick={() => setMode("dark")} title="Dark mode">
    <svg viewBox="0 0 16 16" aria-hidden="true"><path d="M13.5 10.2A6 6 0 0 1 5.8 2.5a6 6 0 1 0 7.7 7.7z" /></svg>Dark
  </button>
</div>

<style>
  .mode {
    display: inline-flex;
    font-size: 0.85rem;
  }
  button {
    display: inline-flex;
    align-items: center;
    gap: 0.3rem;
    background: transparent;
    color: var(--bar-ink);
    border: 1px solid rgb(255 255 255 / 0.35);
    padding: 0.3rem 0.6rem;
  }
  button:first-child {
    border-radius: 6px 0 0 6px;
  }
  button:last-child {
    border-radius: 0 6px 6px 0;
    border-left: none;
  }
  button:hover {
    border-color: var(--bar-accent);
  }
  button[aria-pressed="true"] {
    background: rgb(255 255 255 / 0.18);
    font-weight: 600;
  }
  svg {
    width: 14px;
    height: 14px;
    fill: none;
    stroke: currentColor;
    stroke-width: 1.5;
    stroke-linecap: round;
    stroke-linejoin: round;
  }
</style>
