import type { RefObject } from "preact";
import { useEffect } from "preact/hooks";

const PAD = 6;

/**
 * A horizontally scrolling strip of links: keeps `data-overflow` ("start", "end" or "both"; absent when
 * everything fits) up to date, and scrolls the item with `aria-current` into view when `key` changes.
 */
export function useOverflowStrip(ref: RefObject<HTMLElement>, key: string): void {
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const update = () => {
      const max = el.scrollWidth - el.clientWidth;
      const start = el.scrollLeft > 1;
      const end = max > 1 && el.scrollLeft < max - 1;
      if (start && end) el.dataset.overflow = "both";
      else if (start) el.dataset.overflow = "start";
      else if (end) el.dataset.overflow = "end";
      else delete el.dataset.overflow;
    };
    // Scroll the strip itself (not the page, as `scrollIntoView` could) just far enough to show the item
    // with room for its focus outline (3px + 2px offset) at the edge.
    const reveal = (item: HTMLElement) => {
      const box = el.getBoundingClientRect();
      const r = item.getBoundingClientRect();
      if (r.left < box.left + PAD) el.scrollLeft += r.left - box.left - PAD;
      else if (r.right > box.right - PAD) el.scrollLeft += r.right - box.right + PAD;
    };
    const onFocus = (e: FocusEvent) => {
      const t = e.target as HTMLElement | null;
      if (t && !t.closest(".nav-menu-list")) reveal(t);
    };
    const current = el.querySelector<HTMLElement>('[aria-current="page"]');
    if (current) reveal(current);
    update();
    el.addEventListener("scroll", update, { passive: true });
    el.addEventListener("focusin", onFocus);
    window.addEventListener("resize", update);
    // Item widths change when the font loads: recount on any size change of the strip or its items.
    const ro = typeof ResizeObserver === "function" ? new ResizeObserver(update) : null;
    ro?.observe(el);
    for (const c of Array.from(el.children)) ro?.observe(c);
    return () => {
      ro?.disconnect();
      el.removeEventListener("scroll", update);
      el.removeEventListener("focusin", onFocus);
      window.removeEventListener("resize", update);
    };
  }, [ref, key]);
}
