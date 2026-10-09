import { type KeyboardEvent, useRef } from "react";

import { type Theme, useTheme } from "../lib/theme";

const CHOICES: [Theme, string][] = [
  ["system", "System"],
  ["light", "Light"],
  ["dark", "Dark"],
];

/** System (follows the computer), Light or Dark; remembered in this browser. */
export function ThemeSwitch() {
  const [theme, setTheme] = useTheme();
  const buttons = useRef<(HTMLButtonElement | null)[]>([]);
  // One Tab stop: only the picked radio is tabbable; the arrow keys move and pick.
  const onKeyDown = (e: KeyboardEvent, index: number) => {
    const step = e.key === "ArrowRight" || e.key === "ArrowDown" ? 1 : e.key === "ArrowLeft" || e.key === "ArrowUp" ? -1 : 0;
    if (!step) return;
    e.preventDefault();
    const next = (index + step + CHOICES.length) % CHOICES.length;
    setTheme(CHOICES[next][0]);
    buttons.current[next]?.focus();
  };
  return (
    <div role="radiogroup" aria-label="Theme" className="inline-flex rounded-full border border-line p-0.5">
      {CHOICES.map(([value, label], i) => (
        <button
          key={value}
          ref={(el) => {
            buttons.current[i] = el;
          }}
          type="button"
          role="radio"
          aria-checked={theme === value}
          tabIndex={theme === value ? 0 : -1}
          className={`rounded-full px-3 py-1 text-table ${theme === value ? "bg-accent text-accent-ink" : "text-ink-2 hover:text-ink"}`}
          onClick={() => setTheme(value)}
          onKeyDown={(e) => onKeyDown(e, i)}
        >
          {label}
        </button>
      ))}
    </div>
  );
}
