import { render, screen } from "@testing-library/react";

import { App } from "./App";

test("renders the console placeholder", () => {
  render(<App />);
  expect(screen.getByRole("heading", { name: "Restro Console" })).toBeInTheDocument();
});
