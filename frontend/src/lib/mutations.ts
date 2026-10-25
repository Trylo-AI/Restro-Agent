// Every console write (contracts/openapi.yaml). Screens call these; they never call `api` themselves.
import { api, cafeHeader, call, newIdempotencyKey } from "./api/client";
import type { components } from "./api/schema";

export type ApiOrder = components["schemas"]["Order"];
export type CancelReason = "out_of_stock" | "kitchen_issue" | "customer_asked" | "other";

const id = (v: string) => ({ ...cafeHeader(), path: { id: v } });

export const orders = {
  transition: (orderId: string, to: "ACCEPTED" | "READY" | "SERVED", expectedVersion: number) =>
    call(api.POST("/orders/{id}/transition", { params: id(orderId), body: { to, expected_version: expectedVersion } })),
  undo: (orderId: string) => call(api.POST("/orders/{id}/undo", { params: id(orderId) })),
  delay: (orderId: string, minutes: number, note: string | null) =>
    call(api.POST("/orders/{id}/delay", { params: id(orderId), body: { minutes, note } })),
  cancel: (orderId: string, reason: CancelReason, text: string | null, expectedVersion: number) =>
    call(api.POST("/orders/{id}/cancel", {
      params: id(orderId), body: { reason_code: reason, reason_text: text, expected_version: expectedVersion },
    })),
  manual: (key: string, tableId: string, items: { item_id: string; qty: number; note: string | null }[],
    allergyNote: string | null) =>
    call(api.POST("/orders/manual", {
      params: { header: { ...cafeHeader().header, "Idempotency-Key": key } },
      body: { table_id: tableId, items, allergy_note: allergyNote },
    })),
};

export const conversations = {
  open: (tableSessionId: string) =>
    call(api.POST("/conversations/open", { params: cafeHeader(), body: { table_session_id: tableSessionId } })),
  takeover: (conversationId: string) => call(api.POST("/conversations/{id}/takeover", { params: id(conversationId) })),
  reply: (conversationId: string, text: string) =>
    call(api.POST("/conversations/{id}/reply", { params: id(conversationId), body: { text } })),
  handback: (conversationId: string) => call(api.POST("/conversations/{id}/handback", { params: id(conversationId) })),
};

export const requests = {
  ack: (requestId: string) => call(api.POST("/requests/{id}/ack", { params: id(requestId) })),
  resolve: (requestId: string) => call(api.POST("/requests/{id}/resolve", { params: id(requestId) })),
};

export const bills = {
  deliver: (tableSessionId: string) =>
    call(api.POST("/bills/{table_session_id}/deliver", { params: { ...cafeHeader(), path: { table_session_id: tableSessionId } } })),
  applyCode: (tableSessionId: string, code: string) =>
    call(api.POST("/bills/{table_session_id}/apply-code", {
      params: { ...cafeHeader(), path: { table_session_id: tableSessionId } }, body: { code },
    })),
};

export type MenuItemInput = components["schemas"]["MenuItemInput"];

export const menu = {
  setAvailable: (itemId: string, available: boolean) =>
    call(api.POST("/menu/{id}/availability", { params: id(itemId), body: { available } })),
  create: (item: MenuItemInput) => call(api.POST("/menu", { params: cafeHeader(), body: item })),
  update: (itemId: string, item: MenuItemInput, version: number) =>
    call(api.PUT("/menu/{id}", { params: id(itemId), body: { ...item, version } })),
  /** The CSV's text as the body (text/csv). */
  importCsv: (csv: string) =>
    call(api.POST("/menu/import", {
      params: cafeHeader(), body: csv, bodySerializer: (b: string) => b, headers: { "Content-Type": "text/csv" },
    })),
};

export type CafeSettingsInput = components["schemas"]["CafeSettings"];
export type TableInput = components["schemas"]["TableInput"];
export type Role = "shift_manager" | "manager" | "owner";

export const setup = {
  saveSettings: (cafeId: string, body: CafeSettingsInput) =>
    call(api.PUT("/cafes/{id}/settings", { params: { path: { id: cafeId } }, body })),
  createTable: (body: TableInput) => call(api.POST("/tables", { params: cafeHeader(), body })),
  updateTable: (tableId: string, body: TableInput) => call(api.PUT("/tables/{id}", { params: id(tableId), body })),
  invite: (email: string, role: Role, displayName: string | null) =>
    call(api.POST("/team/invite", { params: cafeHeader(), body: { email, role, display_name: displayName } })),
  updateMember: (membershipId: string, body: { role?: Role; active?: boolean }) =>
    call(api.PATCH("/team/{membership_id}", { params: { ...cafeHeader(), path: { membership_id: membershipId } }, body })),
};

export const cafes = {
  pause: (cafeId: string, paused: boolean) =>
    call(api.POST("/cafes/{id}/pause-ordering", { params: { path: { id: cafeId } }, body: { paused } })),
};

export const customers = {
  addNote: (customerId: string, text: string) =>
    call(api.POST("/customers/{id}/notes", { params: id(customerId), body: { text } })),
  undoNote: (noteId: string) => call(api.POST("/customer-notes/{id}/undo", { params: id(noteId) })),
  removePreference: (customerId: string, kind: components["schemas"]["PreferenceKind"], value: string) =>
    call(api.POST("/customers/{id}/preferences/remove", { params: id(customerId), body: { kind, value } })),
};

export { newIdempotencyKey };

export const notifications = {
  ack: (notificationId: string) => call(api.POST("/notifications/{id}/ack", { params: id(notificationId) })),
  ackAll: () => call(api.POST("/notifications/ack-all", { params: cafeHeader() })),
};
