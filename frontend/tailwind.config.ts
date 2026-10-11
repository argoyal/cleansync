import type { Config } from "tailwindcss";

const token = (name: string) => `rgb(var(--${name}) / <alpha-value>)`;

export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        bg: token("bg"),
        surface: token("surface"),
        sunken: token("sunken"),
        ink: token("ink"),
        "ink-2": token("ink-2"),
        line: token("line"),
        accent: token("accent"),
        "accent-ink": token("accent-ink"),
        "accent-soft": token("accent-soft"),
        ok: token("ok"),
        warn: token("warn"),
        "warn-soft": token("warn-soft"),
        err: token("err"),
        "err-soft": token("err-soft"),
        run: token("run"),
        "run-soft": token("run-soft"),
        paused: token("paused"),
        rail: token("rail"),
        "rail-ink": token("rail-ink"),
        "rail-mark": token("rail-mark"),
        "rail-badge": token("rail-badge"),
        panel: token("panel"),
        ...Object.fromEntries([1, 2, 3, 4, 5, 6].map((i) => [`label-${i}`, token(`label-${i}`)])),
        ...Object.fromEntries(
          ["files", "mail", "activity", "recovery", "settings"].flatMap((s) => [
            [`sec-${s}`, token(`sec-${s}`)],
            [`sec-${s}-ink`, token(`sec-${s}-ink`)],
            [`sec-${s}-soft`, token(`sec-${s}-soft`)],
            [`sec-${s}-soft-ink`, token(`sec-${s}-soft-ink`)],
          ]),
        ),
      },
      fontFamily: {
        sans: ['"Public Sans Variable"', "system-ui", "sans-serif"],
      },
      fontSize: {
        meta: ["12px", "16px"],
        table: ["13px", "18px"],
        body: ["14px", "20px"],
        section: ["16px", "22px"],
        title: ["20px", "26px"],
        hero: ["28px", "34px"],
      },
      borderRadius: { ctl: "8px", menu: "10px", card: "12px" },
      boxShadow: {
        float: "0 10px 30px rgb(var(--shadow) / 0.16), 0 1px 3px rgb(var(--shadow) / 0.12)",
      },
    },
  },
  plugins: [],
} satisfies Config;
