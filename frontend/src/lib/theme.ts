import { useSyncExternalStore } from "react";

export type Theme = "system" | "light" | "dark";
/** Keep in step with public/theme.js, which applies the choice before the page draws. */
export const THEME_KEY = "drivesync.theme";

export function getTheme(): Theme {
  try {
    const t = localStorage.getItem(THEME_KEY);
    return t === "light" || t === "dark" ? t : "system";
  } catch {
    return "system"; // storage blocked: follow the system
  }
}

export function applyTheme(theme: Theme): void {
  const root = document.documentElement;
  if (theme === "system") root.removeAttribute("data-theme");
  else root.setAttribute("data-theme", theme);
}

/** Remember the choice in this browser (when it can) and apply it now. */
export function setTheme(theme: Theme): void {
  try {
    if (theme === "system") localStorage.removeItem(THEME_KEY);
    else localStorage.setItem(THEME_KEY, theme);
  } catch {
    // storage blocked: the choice lasts for this visit only
  }
  applyTheme(theme);
  notify();
}

const listeners = new Set<() => void>();
const notify = () => listeners.forEach((l) => l());

/** The page's own marker is the one source of truth, so every switch and every tab agree. */
function shownTheme(): Theme {
  const shown = document.documentElement.getAttribute("data-theme");
  return shown === "light" || shown === "dark" ? shown : "system";
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  const onStorage = (e: StorageEvent) => {
    if (e.key !== THEME_KEY && e.key !== null) return;
    applyTheme(getTheme()); // another tab changed the choice
    notify();
  };
  window.addEventListener("storage", onStorage);
  return () => {
    listeners.delete(listener);
    window.removeEventListener("storage", onStorage);
  };
}

export function useTheme(): [Theme, (theme: Theme) => void] {
  const theme = useSyncExternalStore(subscribe, shownTheme);
  return [theme, setTheme];
}
