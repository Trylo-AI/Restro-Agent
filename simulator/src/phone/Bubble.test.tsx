import { fireEvent, render, screen } from "@testing-library/react";

import type { ThreadItem } from "../lib/types";
import { Bubble } from "./Bubble";
import { ListSheet } from "./ListSheet";

const AT = "2026-10-24T12:00:00Z";

function out(status: string, payload: object, violations: string[] = []): ThreadItem {
  return { dir: "out", id: "o1", wamid: "w", type: "interactive", template_name: null, payload, status, error_code: null,
    at: AT, updated_at: AT, violations } as ThreadItem;
}

test("customer text bubble", () => {
  render(<Bubble item={{ dir: "in", id: "i1", wamid: "w", type: "text", payload: { type: "text", text: { body: "hi" } }, at: AT, processed: true }}
    onButton={() => {}} onOpenList={() => {}} />);
  expect(screen.getByText("hi")).toBeInTheDocument();
});

test("ticks follow the status", () => {
  const { rerender } = render(<Bubble item={out("sent", { type: "text", text: { body: "x" } })} onButton={() => {}} onOpenList={() => {}} />);
  expect(screen.getByTitle("sent")).toHaveTextContent("✓");
  rerender(<Bubble item={out("read", { type: "text", text: { body: "x" } })} onButton={() => {}} onOpenList={() => {}} />);
  expect(screen.getByTitle("read")).toHaveClass("read");
  rerender(<Bubble item={out("failed", { type: "text", text: { body: "x" } })} onButton={() => {}} onOpenList={() => {}} />);
  expect(screen.getByTitle("Not delivered")).toBeInTheDocument();
});

test("reply buttons send their id, not their title", () => {
  const onButton = vi.fn();
  const payload = { type: "interactive", interactive: { type: "button", body: { text: "Confirm?" },
    action: { buttons: [{ type: "reply", reply: { id: "confirm:3", title: "Confirm" } }] } } };
  render(<Bubble item={out("delivered", payload)} onButton={onButton} onOpenList={() => {}} />);
  fireEvent.click(screen.getByRole("button", { name: "Confirm" }));
  expect(onButton).toHaveBeenCalledWith("confirm:3", "Confirm");
});

test("list opens a sheet and a row sends its id", () => {
  const sections = [{ title: "Cold coffee", rows: [{ id: "item:1", title: "Cold Coffee", description: "₹ 190" }] }];
  const onOpenList = vi.fn();
  const payload = { type: "interactive", interactive: { type: "list", body: { text: "Menu" }, action: { button: "See menu", sections } } };
  render(<Bubble item={out("delivered", payload)} onButton={() => {}} onOpenList={onOpenList} />);
  fireEvent.click(screen.getByRole("button", { name: /See menu/ }));
  expect(onOpenList).toHaveBeenCalledWith("Menu", "See menu", sections);

  const onPick = vi.fn();
  render(<ListSheet title="See menu" sections={sections} onPick={onPick} onClose={() => {}} />);
  fireEvent.click(screen.getByTitle("item:1"));
  expect(onPick).toHaveBeenCalledWith("item:1", "Cold Coffee");
});

test("a Meta-limit violation shows a red badge", () => {
  render(<Bubble item={out("sent", { type: "text", text: { body: "x" } }, ["button title > 20"])} onButton={() => {}} onOpenList={() => {}} />);
  expect(screen.getByText(/button title > 20/)).toBeInTheDocument();
});
