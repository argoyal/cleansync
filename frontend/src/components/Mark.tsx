/** CleanSync's mark: the logo on a white tile on the rail, or on its own elsewhere. */
export function Mark({ size = 40, onRail = true }: { size?: number; onRail?: boolean }) {
  return (
    <span
      aria-hidden="true"
      style={{ width: size, height: size, padding: onRail ? size * 0.12 : 0 }}
      className={`grid shrink-0 place-items-center rounded-[11px] ${onRail ? "bg-rail-mark" : ""}`}
    >
      <img src="/logo.png" alt="" className="h-full w-full object-contain" />
    </span>
  );
}
