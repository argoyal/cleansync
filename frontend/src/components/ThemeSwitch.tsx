import { type Theme, useTheme } from "../lib/theme";

const CHOICES: [Theme, string][] = [
  ["system", "System"],
  ["light", "Light"],
  ["dark", "Dark"],
];

/** System (follows the computer), Light or Dark; remembered in this browser. */
export function ThemeSwitch() {
  const [theme, setTheme] = useTheme();
  return (
    <div role="radiogroup" aria-label="Theme" className="inline-flex rounded-full border border-line p-0.5">
      {CHOICES.map(([value, label]) => (
        <button
          key={value}
          type="button"
          role="radio"
          aria-checked={theme === value}
          className={`rounded-full px-3 py-1 text-table ${theme === value ? "bg-accent text-accent-ink" : "text-ink-2 hover:text-ink"}`}
          onClick={() => setTheme(value)}
        >
          {label}
        </button>
      ))}
    </div>
  );
}
