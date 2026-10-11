/** CleanSync's mark: a white tile with an indigo "C" (on the rail), or the reverse. */
export function Mark({ size = 40, onRail = true }: { size?: number; onRail?: boolean }) {
  return (
    <span
      aria-hidden="true"
      style={{ width: size, height: size, fontSize: size * 0.45 }}
      className={`grid shrink-0 place-items-center rounded-[11px] font-extrabold ${onRail ? "bg-rail-mark text-rail" : "bg-rail text-rail-mark"}`}
    >
      C
    </span>
  );
}
