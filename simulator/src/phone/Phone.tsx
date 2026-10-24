import { useCallback, useEffect, useRef, useState } from "react";

import { api, type SendKind } from "../lib/api";
import { PHRASES } from "../lib/phrases";
import type { CustomersResponse, ListSection, Thread } from "../lib/types";
import { Bubble } from "./Bubble";
import { ListSheet } from "./ListSheet";

const POLL_MS = 700;

interface Props {
  index: number;
  waId: string;
  selected: boolean;
  directory: CustomersResponse | null;
  onSelect: () => void;
  onChangeCustomer: (waId: string) => void;
  onRemove?: () => void;
}

export function Phone({ index, waId, selected, directory, onSelect, onChangeCustomer, onRemove }: Props) {
  const [thread, setThread] = useState<Thread | null>(null);
  const [draft, setDraft] = useState("");
  const [sheet, setSheet] = useState<{ title: string; button: string; sections: ListSection[] } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [showPhrases, setShowPhrases] = useState(false);
  const scroller = useRef<HTMLDivElement>(null);
  const lastCount = useRef(0);
  const known = directory?.customers.find((c) => c.wa_id === waId);

  const refresh = useCallback(async () => {
    try {
      const t = await api.thread(waId);
      setThread(t);
      setError(null);
      const unread = t.items.some((i) => i.dir === "out" && (i.status === "sent" || i.status === "delivered") && i.wamid);
      if (unread && document.visibilityState === "visible") await api.read(waId);
    } catch (e) {
      setError(e instanceof Error ? e.message : "api unreachable");
    }
  }, [waId]);

  useEffect(() => {
    setThread(null);
    lastCount.current = 0;
    void refresh();
    const id = window.setInterval(() => void refresh(), POLL_MS);
    return () => window.clearInterval(id);
  }, [refresh]);

  useEffect(() => {
    const count = thread?.items.length ?? 0;
    if (count !== lastCount.current && scroller.current) scroller.current.scrollTop = scroller.current.scrollHeight;
    lastCount.current = count;
  }, [thread]);

  async function send(kind: SendKind, extra: { text?: string; reply_id?: string; reply_title?: string } = {}) {
    setBusy(true);
    try {
      await api.send(waId, kind, { ...extra, name: known?.name ?? undefined });
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "send failed");
    } finally {
      setBusy(false);
    }
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const text = draft.trim();
    if (!text) return;
    setDraft("");
    await send("text", { text });
  }

  const header = thread?.cafe ?? directory?.cafe ?? "Café";
  const subline = thread?.typing ? "typing…" : "online";

  return (
    <section className={`phone${selected ? " selected" : ""}`} onClick={onSelect} aria-label={`Phone ${index + 1}`}>
      <div className="phone-controls">
        <select value={waId} onChange={(e) => onChangeCustomer(e.target.value)} aria-label="Customer">
          {!known && <option value={waId}>New · {waId.slice(-4)}</option>}
          {directory?.customers.map((c) => (
            <option key={c.wa_id} value={c.wa_id}>
              {c.name ?? "Unnamed"} · {c.wa_id.slice(-4)}
            </option>
          ))}
        </select>
        <button onClick={async () => onChangeCustomer((await api.newCustomer()).wa_id)} title="A synthetic number with no history">
          New customer
        </button>
        {onRemove && <button onClick={onRemove} aria-label="Remove phone">✕</button>}
      </div>
      <div className="phone-controls">
        <select
          value=""
          onChange={(e) => e.target.value && void send("text", { text: e.target.value })}
          aria-label="Scan table QR"
        >
          <option value="">Scan table QR…</option>
          {directory?.tables.map((t) => (
            <option key={t.label} value={t.qr_text}>
              {t.label} · “{t.qr_text}”
            </option>
          ))}
        </select>
        <button onClick={async () => { await api.reset(waId); await refresh(); }} title="Clear conversation state (orders kept)">
          Restart
        </button>
        <button onClick={async () => { await api.closeWindow(waId); await refresh(); }} title="Move the last message 25 h back">
          Close 24-h window
        </button>
      </div>

      <div className="device">
        <header className="wa-header">
          <div className="avatar">{header.slice(0, 2).toUpperCase()}</div>
          <div>
            <div className="wa-title">{header}</div>
            <div className={`wa-sub${thread?.typing ? " typing" : ""}`}>{subline}</div>
          </div>
        </header>
        <div className="wa-thread" ref={scroller}>
          {thread?.customer && !thread.customer.window_open && (
            <div className="system-note">24-hour window closed · only templates can reach this customer</div>
          )}
          {thread?.items.length === 0 && <div className="system-note">Scan a table QR or say hi to start.</div>}
          {thread?.items.map((item) => (
            <Bubble
              key={`${item.dir}-${item.id}`}
              item={item}
              onButton={(id, title) => void send("button", { reply_id: id, reply_title: title })}
              onOpenList={(title, button, sections) => setSheet({ title, button, sections })}
            />
          ))}
          {thread?.typing && (
            <div className="row in-row">
              <div className="bubble business typing-dots"><span /><span /><span /></div>
            </div>
          )}
        </div>
        {error && <div className="wa-error">⚠ {error}</div>}
        <form className="wa-composer" onSubmit={submit}>
          <details className="attach">
            <summary aria-label="Attach">📎</summary>
            <div className="attach-menu">
              <button type="button" onClick={() => void send("image")}>📷 Photo</button>
              <button type="button" onClick={() => void send("audio")}>🎤 Voice note</button>
              <button type="button" onClick={() => void send("sticker")}>🙂 Sticker</button>
            </div>
          </details>
          <input
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder="Message"
            aria-label="Message"
            disabled={busy}
          />
          <button type="submit" className="send" disabled={busy || !draft.trim()} aria-label="Send">➤</button>
        </form>
        {sheet && (
          <ListSheet
            title={sheet.button}
            sections={sheet.sections}
            onClose={() => setSheet(null)}
            onPick={(id, title) => {
              setSheet(null);
              void send("list", { reply_id: id, reply_title: title });
            }}
          />
        )}
      </div>

      <button className="phrases-toggle" onClick={() => setShowPhrases((v) => !v)}>
        {showPhrases ? "Hide" : "Try typing…"}
      </button>
      {showPhrases && (
        <div className="phrases">
          {PHRASES.map((g) => (
            <div key={g.group}>
              <div className="phrase-group">{g.group}</div>
              {g.items.map((p) => (
                <button key={p} className="chip" onClick={() => void send("text", { text: p })}>{p}</button>
              ))}
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
