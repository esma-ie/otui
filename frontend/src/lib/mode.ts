// Light or dark: the system's setting until the user picks one with the buttons in the header
// (ModeToggle), then that choice, remembered in this browser. The mode is data-theme on <html>
// (app.css and company themes key their dark colours on it); index.html sets it before the app
// loads, so a dark page never flashes light. A change fires "otui-theme", so canvases and charts
// that read colours in script redraw.
export type Mode = "light" | "dark";

const KEY = "otui.mode";
const system = matchMedia("(prefers-color-scheme: dark)");

function saved(): Mode | null {
  try {
    const v = localStorage.getItem(KEY);
    return v === "light" || v === "dark" ? v : null;
  } catch {
    return null;
  }
}

export function mode(): Mode {
  return document.documentElement.dataset.theme === "dark" ? "dark" : "light";
}

function apply(m: Mode) {
  if (document.documentElement.dataset.theme === m) return;
  document.documentElement.dataset.theme = m;
  window.dispatchEvent(new Event("otui-theme"));
}

export function setMode(m: Mode) {
  try {
    localStorage.setItem(KEY, m);
  } catch {
    /* the choice still applies for this visit */
  }
  apply(m);
}

// follow the system until the user picks
apply(saved() ?? (system.matches ? "dark" : "light"));
system.addEventListener("change", () => {
  if (!saved()) apply(system.matches ? "dark" : "light");
});
