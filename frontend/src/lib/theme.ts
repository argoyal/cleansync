import { useState } from "react";

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
}

export function useTheme(): [Theme, (theme: Theme) => void] {
  // Start from what the page shows (theme.js may have applied it), so a blocked storage
  // read can't make the switch disagree with the page.
  const [theme, setState] = useState<Theme>(() => {
    const shown = document.documentElement.getAttribute("data-theme");
    return shown === "light" || shown === "dark" ? shown : getTheme();
  });
  return [
    theme,
    (next) => {
      setTheme(next);
      setState(next);
    },
  ];
}
