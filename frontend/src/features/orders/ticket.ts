// Print ticket: a hidden iframe with plain text nodes (notes are customer text, never HTML).
import type { LiveOrder } from "../../lib/data";
import { clock } from "../../lib/format";

export function printTicket(o: LiveOrder, cafeName: string): void {
  const frame = document.createElement("iframe");
  frame.setAttribute("aria-hidden", "true");
  frame.style.cssText = "position:fixed;width:0;height:0;border:0;right:0;bottom:0";
  document.body.appendChild(frame);
  const doc = frame.contentDocument;
  const win = frame.contentWindow;
  if (!doc || !win) {
    frame.remove();
    return;
  }
  const style = doc.createElement("style");
  style.textContent = "body{font:14px monospace;width:72mm;margin:0;padding:4mm}h1{font-size:22px;margin:0}"
    + "p{margin:2px 0}.big{font-size:18px;font-weight:bold}.allergy{border:2px solid #000;padding:4px;font-weight:bold}"
    + "hr{border:0;border-top:1px dashed #000}";
  doc.head.appendChild(style);
  const line = (text: string, cls?: string) => {
    const p = doc.createElement("p");
    p.textContent = text;
    if (cls) p.className = cls;
    doc.body.appendChild(p);
  };
  const h = doc.createElement("h1");
  h.textContent = `${o.table ?? "—"}  #${o.number}`;
  doc.body.appendChild(h);
  line(`${cafeName} · ${clock(o.created_at)}${o.source === "manual" ? " · manual" : ""}`);
  if (o.allergy_note) line(`ALLERGY: ${o.allergy_note}`, "allergy");
  if (o.profileAllergies.length) line(`On profile: ${o.profileAllergies.join(", ")}`, "allergy");
  doc.body.appendChild(doc.createElement("hr"));
  for (const it of o.items) {
    line(`${it.qty} × ${it.name_snapshot}`, "big");
    if (it.note) line(`   ${it.note}`);
  }
  doc.body.appendChild(doc.createElement("hr"));
  win.addEventListener("afterprint", () => frame.remove());
  win.focus();
  win.print();
  window.setTimeout(() => frame.remove(), 60_000); // browsers without afterprint
}
