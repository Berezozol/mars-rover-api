import { fireEvent, render, screen, waitFor } from "@testing-library/preact";
import { describe, expect, it } from "vitest";
import { App } from "../src/App";
import { Thumb } from "../src/components/Thumb";
import { detail, mockApi, page, perseverance, curiosity, photo } from "./helpers";

// AC-8 of docs/proposals/msl-thumbnails.md: Curiosity has only `small` (JPL thumbnail) and `full`.

const THUMB = "https://img.example/c/1_thumb.jpg";
const FULL = "https://img.example/c/1.jpg";

const curiosityImages = (small: string | null) => ({
  small,
  medium: null,
  large: null,
  full: FULL,
});

const curiosityOver = (_id: number, small: string | null) => ({
  rover: "curiosity",
  sol: 5066,
  camera: { code: "MAST_LEFT", name: "Mast Camera Left" },
  images: curiosityImages(small),
  img_src: FULL,
  // by contract Curiosity frame size is unknown
  width: null,
  height: null,
});

function openCuriosity(small: string | null, siblings = 1) {
  const items = Array.from({ length: siblings + 1 }, (_, i) =>
    photo(1 + i, {
      ...curiosityOver(1 + i, i === 0 ? small : `https://img.example/c/${1 + i}_thumb.jpg`),
    }),
  );
  mockApi({
    "/api/v2/rovers": { items: [perseverance, curiosity] },
    "/api/v2/photos/1": detail(1, { ...curiosityOver(1, small), extra: {}, caption: null }),
    "/api/v2/rovers/curiosity/photos": page(items, items.length),
  });
  window.history.replaceState(null, "", "/photos/1");
  render(<App />);
}

const mainImage = () => screen.getByAltText(/frame from Curiosity/) as HTMLImageElement;

describe("Curiosity thumbnails (AC-8)", () => {
  it("AC-8: card with small set and medium/large null shows full by default", async () => {
    openCuriosity(THUMB);
    await screen.findByRole("heading", { level: 1 });
    expect(mainImage().getAttribute("src")).toBe(FULL);
    expect((screen.getByLabelText("Full") as HTMLInputElement).checked).toBe(true);
    expect((screen.getByLabelText("Small") as HTMLInputElement).checked).toBe(false);
    expect(screen.getByRole("link", { name: /Open image/ }).getAttribute("href")).toBe(
      "/api/v2/photos/1/image?size=full",
    );
  });

  it("AC-8: Small stays available on the card and can be chosen explicitly", async () => {
    openCuriosity(THUMB);
    await screen.findByRole("heading", { level: 1 });
    const small = screen.getByLabelText("Small") as HTMLInputElement;
    expect(small.disabled).toBe(false);
    expect((screen.getByLabelText("Medium") as HTMLInputElement).disabled).toBe(true);
    expect((screen.getByLabelText("Large") as HTMLInputElement).disabled).toBe(true);
    fireEvent.click(small);
    await waitFor(() => expect(mainImage().getAttribute("src")).toBe(THUMB));
  });

  it("AC-8: grid tile of a Curiosity photo uses small", async () => {
    openCuriosity(THUMB, 2);
    await screen.findByRole("heading", { level: 1 });
    // tiles of the "same camera" strip; the card's own image has "frame from" in its alt
    const tiles = await screen.findAllByAltText(/^Mast Camera Left, sol 5066, .* UTC$/);
    expect(tiles.map((t) => t.getAttribute("src"))).toEqual([
      THUMB,
      "https://img.example/c/2_thumb.jpg",
      "https://img.example/c/3_thumb.jpg",
    ]);
  });

  it("AC-8: a Curiosity photo with small null still shows the card with full", async () => {
    openCuriosity(null);
    await screen.findByRole("heading", { level: 1 });
    expect(mainImage().getAttribute("src")).toBe(FULL);
    expect((screen.getByLabelText("Full") as HTMLInputElement).checked).toBe(true);
    expect((screen.getByLabelText("Small") as HTMLInputElement).disabled).toBe(true);
    expect(screen.getByText(/published in one size only/)).toBeTruthy();
    // credit is always visible
    expect(screen.getAllByText("NASA/JPL-Caltech/LANL/CNES/IRAP").length).toBeGreaterThanOrEqual(1);
  });

  it("AC-8: a Curiosity grid tile with small null falls back to full", async () => {
    mockApi({
      "/api/v2/rovers": { items: [perseverance, curiosity] },
      "/api/v2/photos/1": detail(1, { ...curiosityOver(1, null), extra: {}, caption: null }),
      "/api/v2/rovers/curiosity/photos": page(
        [photo(1, curiosityOver(1, null)), photo(2, curiosityOver(2, null))],
        2,
      ),
    });
    window.history.replaceState(null, "", "/photos/1");
    render(<App />);
    const tiles = await screen.findAllByAltText(/^Mast Camera Left, sol 5066, .* UTC$/);
    expect(tiles.length).toBe(2);
    for (const t of tiles) expect(t.getAttribute("src")).toBe(FULL);
  });

  it("AC-8: Perseverance card keeps its default (medium)", async () => {
    mockApi({
      "/api/v2/rovers": { items: [perseverance, curiosity] },
      "/api/v2/photos/81234": detail(81234),
      "/api/v2/rovers/perseverance/photos": page([photo(81234), photo(81233)], 2),
    });
    window.history.replaceState(null, "", "/photos/81234");
    render(<App />);
    await screen.findByRole("heading", { level: 1 });
    expect((screen.getByLabelText("Medium") as HTMLInputElement).checked).toBe(true);
    expect(screen.getByAltText(/frame from Perseverance/).getAttribute("src")).toBe(
      "https://img.example/81234_800.jpg",
    );
  });

  describe("thumbnail proportions with unknown frame size", () => {
    const loadWith = (img: HTMLElement, w: number, h: number) => {
      Object.defineProperty(img, "naturalWidth", { value: w, configurable: true });
      Object.defineProperty(img, "naturalHeight", { value: h, configurable: true });
      fireEvent.load(img);
    };
    const box = (img: HTMLElement) => img.parentElement as HTMLElement;

    it("AC-8: an 80x112 portrait thumbnail is shown whole (thumb-whole)", () => {
      render(<Thumb sources={[THUMB]} alt="portrait" width={null} height={null} />);
      const img = screen.getByAltText("portrait");
      expect(box(img).classList.contains("thumb-whole")).toBe(false);
      loadWith(img, 80, 112);
      expect(box(img).classList.contains("thumb-whole")).toBe(true);
    });

    it("AC-8: a 192x144 thumbnail keeps the plain 4:3 tile (no thumb-whole)", () => {
      render(<Thumb sources={[THUMB]} alt="landscape" width={null} height={null} />);
      const img = screen.getByAltText("landscape");
      loadWith(img, 192, 144);
      expect(box(img).classList.contains("thumb-whole")).toBe(false);
    });

    it("AC-8: thumb-whole is reset when the source changes", () => {
      const { rerender } = render(<Thumb sources={[THUMB]} alt="t" width={null} height={null} />);
      loadWith(screen.getByAltText("t"), 80, 112);
      expect(box(screen.getByAltText("t")).classList.contains("thumb-whole")).toBe(true);
      rerender(
        <Thumb
          sources={["https://img.example/c/2_thumb.jpg"]}
          alt="t"
          width={null}
          height={null}
        />,
      );
      expect(box(screen.getByAltText("t")).classList.contains("thumb-whole")).toBe(false);
    });

    it("AC-8: a known frame size never gets thumb-whole", () => {
      render(<Thumb sources={[THUMB]} alt="known" width={1648} height={1200} />);
      const img = screen.getByAltText("known");
      loadWith(img, 80, 112);
      expect(box(img).classList.contains("thumb-whole")).toBe(false);
    });
  });
});
