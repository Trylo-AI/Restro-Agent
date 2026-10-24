import { render, screen } from "@testing-library/react";

import { App } from "./App";

test("signed out users see the login screen", async () => {
  render(<App />);
  expect(await screen.findByRole("heading", { name: "Sign in to the console" })).toBeInTheDocument();
  expect(screen.getByLabelText("Email")).toBeInTheDocument();
});
