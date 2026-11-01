// A render error never blanks the console (app/ErrorBoundary.tsx).
import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";

import { ErrorBoundary, isChunkError } from "./ErrorBoundary";

let broken = true;
function Flaky() {
  if (broken) throw new Error("boom");
  return <p>Screen works</p>;
}

beforeEach(() => {
  broken = true;
  vi.spyOn(console, "error").mockImplementation(() => undefined);
});

test("a screen error keeps the rest of the console and can be retried", () => {
  function Shell() {
    const [n, setN] = useState(0);
    return (
      <>
        <button onClick={() => setN(n + 1)}>Nav {n}</button>
        <ErrorBoundary scope="screen"><Flaky /></ErrorBoundary>
      </>
    );
  }
  render(<Shell />);
  expect(screen.getByRole("alert")).toHaveTextContent("This screen hit a problem");
  fireEvent.click(screen.getByRole("button", { name: "Nav 0" }));
  expect(screen.getByRole("button", { name: "Nav 1" })).toBeInTheDocument(); // the shell still works

  broken = false;
  fireEvent.click(screen.getByRole("button", { name: "Try again" }));
  expect(screen.getByText("Screen works")).toBeInTheDocument();
});

test("an app error shows a reload card instead of a blank page", () => {
  render(<ErrorBoundary scope="app"><Flaky /></ErrorBoundary>);
  expect(screen.getByRole("heading", { name: "Something went wrong" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Reload" })).toBeInTheDocument();
});

test("recognises a lazy screen that failed to load", () => {
  expect(isChunkError(new TypeError("Failed to fetch dynamically imported module: https://x/assets/Setup-1.js"))).toBe(true);
  expect(isChunkError(new TypeError("Importing a module script failed."))).toBe(true);
  expect(isChunkError(new Error("boom"))).toBe(false);
});
