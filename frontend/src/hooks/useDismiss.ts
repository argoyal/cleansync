import { type RefObject, useEffect, useRef } from "react";

/**
 * Closes a popover on Escape, or when a click or focus lands outside `ref` (which should wrap
 * the trigger and the panel). On Escape focus goes back to `returnFocus`. Because opening a
 * popover means clicking or focusing outside every other one, only one stays open.
 */
export function useDismiss(
  ref: RefObject<HTMLElement>,
  onClose: () => void,
  enabled: boolean,
  returnFocus?: RefObject<HTMLElement>,
): void {
  const close = useRef(onClose);
  close.current = onClose;
  useEffect(() => {
    if (!enabled) return;
    const outside = (e: Event) => {
      if (ref.current && e.target instanceof Node && !ref.current.contains(e.target)) close.current();
    };
    const key = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      close.current();
      returnFocus?.current?.focus();
    };
    document.addEventListener("pointerdown", outside);
    document.addEventListener("focusin", outside);
    document.addEventListener("keydown", key);
    return () => {
      document.removeEventListener("pointerdown", outside);
      document.removeEventListener("focusin", outside);
      document.removeEventListener("keydown", key);
    };
  }, [enabled, ref, returnFocus]);
}
