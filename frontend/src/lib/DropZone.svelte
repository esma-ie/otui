<script lang="ts">
  // Drop a TIMES run file and its DD files, a folder, or a zip. Folders are walked
  // (webkitGetAsEntry), so dropping a model folder works as well as picking files.
  let { onfiles, busy = false }: { onfiles: (files: File[]) => void; busy?: boolean } = $props();
  let over = $state(false);
  let input: HTMLInputElement;

  async function entryFiles(entry: FileSystemEntry): Promise<File[]> {
    if (entry.isFile) {
      return [await new Promise<File>((res, rej) => (entry as FileSystemFileEntry).file(res, rej))];
    }
    if (entry.isDirectory) {
      const reader = (entry as FileSystemDirectoryEntry).createReader();
      const all: FileSystemEntry[] = [];
      // readEntries returns batches until an empty one
      for (;;) {
        const batch = await new Promise<FileSystemEntry[]>((res, rej) => reader.readEntries(res, rej));
        if (!batch.length) break;
        all.push(...batch);
      }
      return (await Promise.all(all.map(entryFiles))).flat();
    }
    return [];
  }

  async function drop(e: DragEvent) {
    e.preventDefault();
    over = false;
    if (busy || !e.dataTransfer) return;
    const entries = [...e.dataTransfer.items]
      .map((i) => i.webkitGetAsEntry?.())
      .filter((x): x is FileSystemEntry => !!x);
    const files = entries.length
      ? (await Promise.all(entries.map(entryFiles))).flat()
      : [...e.dataTransfer.files];
    const usable = files.filter((f) => !f.name.startsWith("."));
    if (usable.length) onfiles(usable);
  }

  function picked() {
    if (input.files?.length) onfiles([...input.files]);
    input.value = "";
  }
</script>

<div
  class="zone"
  class:over
  class:busy
  role="button"
  tabindex="0"
  aria-label="Drop a TIMES run file and its DD files"
  ondragover={(e) => {
    e.preventDefault();
    over = true;
  }}
  ondragleave={() => (over = false)}
  ondrop={drop}
  onclick={() => !busy && input.click()}
  onkeydown={(e) => (e.key === "Enter" || e.key === " ") && !busy && input.click()}
>
  <svg viewBox="0 0 48 48" width="40" height="40" aria-hidden="true">
    <circle cx="12" cy="24" r="7" fill="var(--com-nrg)" />
    <rect x="28" y="16" width="16" height="16" rx="3" fill="var(--process)" />
    <path d="M19 24h8" stroke="var(--link)" stroke-width="2.5" />
  </svg>
  <strong>Drop a TIMES model here</strong>
  <span class="muted">the <code>.run</code> file and its <code>.dd</code> files, a folder, or a <code>.zip</code></span>
  <span class="muted small">or click to choose files</span>
  <input bind:this={input} type="file" multiple hidden onchange={picked} />
</div>

<style>
  .zone {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 0.35rem;
    padding: 2.5rem 1rem;
    border: 2px dashed var(--line);
    border-radius: var(--radius);
    background: var(--panel);
    text-align: center;
    cursor: pointer;
    transition: border-color 0.15s, background 0.15s;
  }
  .zone:hover,
  .zone:focus-visible,
  .zone.over {
    border-color: var(--accent);
    outline: none;
  }
  .zone.over {
    background: color-mix(in srgb, var(--accent) 8%, var(--panel));
  }
  .zone.busy {
    opacity: 0.6;
    cursor: progress;
  }
  strong {
    font-size: 1.1rem;
  }
  .small {
    font-size: 0.85rem;
  }
</style>
