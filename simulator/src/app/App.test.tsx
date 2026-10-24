import { render, screen } from "@testing-library/react";

import { App } from "./App";

test("renders the simulator placeholder", () => {
  render(<App />);
  expect(screen.getByRole("heading", { name: "WhatsApp simulator" })).toBeInTheDocument();
});
