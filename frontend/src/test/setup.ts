import "@testing-library/jest-dom/vitest";

// jsdom has no matchMedia: behave like a desktop browser (≥ 768 px, < 1200 px).
if (!window.matchMedia) {
  window.matchMedia = (query: string) => ({
    matches: false, media: query, onchange: null,
    addEventListener: () => undefined, removeEventListener: () => undefined,
    addListener: () => undefined, removeListener: () => undefined, dispatchEvent: () => false,
  }) as MediaQueryList;
}
