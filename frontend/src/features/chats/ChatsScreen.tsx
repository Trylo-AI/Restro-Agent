// Chats: read conversations, take over, reply, hand back (docs/console.md#chats).
import { useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Bot, Check, CheckCheck, CircleAlert, MessageSquarePlus, UserRound } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { useNow } from "../../app/alerts";
import { useOffline } from "../../app/App";
import { useToasts } from "../../app/toasts";
import { Dialog, Empty, SeverityChip } from "../../components/ui";
import { ApiError } from "../../lib/api/client";
import { useChats, useOpenSessions, useThread, type ChatRow, type Thread, type ThreadItem } from "../../lib/data";
import { clock, relative } from "../../lib/format";
import { usePhone } from "../../lib/media";
import { conversations } from "../../lib/mutations";
import { useSession } from "../../lib/session";
import { chatUrgency, rank, type Urgency } from "../../lib/urgency";
import { CustomerProfileBody, CustomerProfilePanel } from "../customer-profile/CustomerProfilePanel";

export const QUICK_REPLIES = ["On its way", "Just 2 more minutes", "Checking with the kitchen", "Coming to your table now"];

type Row = { c: ChatRow; u: Urgency };

export function ChatsScreen() {
  const { conversationId } = useParams();
  const chats = useChats();
  const now = useNow();
  const phone = usePhone();
  const navigate = useNavigate();
  const [messageTable, setMessageTable] = useState(false);

  const rows = useMemo<Row[]>(() => (chats.data ?? []).map((c) => ({ c, u: chatUrgency(c.mode, c.lastInAt, c.lastOutAt, now) })), [chats.data, now]);
  const waiting = rows.filter((r) => r.c.mode === "human")
    .sort((a, b) => rank(b.u.severity) - rank(a.u.severity) || Date.parse(a.c.lastInAt ?? a.c.updated_at) - Date.parse(b.c.lastInAt ?? b.c.updated_at));
  const bot = rows.filter((r) => r.c.mode === "bot")
    .sort((a, b) => Date.parse(b.c.lastInAt ?? b.c.updated_at) - Date.parse(a.c.lastInAt ?? a.c.updated_at));

  const item = ({ c, u }: Row) => (
    <li key={c.id}>
      <button className={`chat-row sev-${u.severity}${c.id === conversationId ? " on" : ""}`} onClick={() => navigate(`/chats/${c.id}`)}>
        <div className="row">
          <strong className="grow">{c.customerName ?? "Guest"}{c.table ? ` · ${c.table}` : ""}</strong>
          <span className="muted small">{relative(c.lastInAt ?? c.updated_at, now)}</span>
        </div>
        <div className="row">
          <span className="grow snippet">{c.lastText ?? "No messages yet"}</span>
          <SeverityChip severity={u.severity} label={u.chip} />
        </div>
      </button>
    </li>
  );

  const list = (
    <section className="chat-list" aria-label="Chats">
      <button className="btn small" onClick={() => setMessageTable(true)}><MessageSquarePlus size={15} /> Message a table</button>
      <h2>Waiting for you <span className="count">{waiting.length}</span></h2>
      {waiting.length ? <ul>{waiting.map(item)}</ul> : <p className="muted">No one is waiting for a person.</p>}
      <h2>Bot is handling <span className="count">{bot.length}</span></h2>
      {chats.isPending ? <p className="muted">Loading…</p> : bot.length ? <ul>{bot.map(item)}</ul> : <p className="muted">No chats in the last 24 hours.</p>}
    </section>
  );

  return (
    <div className={`chats${conversationId ? " has-thread" : ""}`}>
      {(!phone || !conversationId) && list}
      {conversationId
        ? <ThreadView key={conversationId} conversationId={conversationId} onBack={() => navigate("/chats")} />
        : !phone && <Empty>Pick a chat on the left.</Empty>}
      {messageTable && <MessageTableDialog onClose={() => setMessageTable(false)} />}
    </div>
  );
}

function Ticks({ status }: { status: string | null }) {
  if (status === "failed") return <span className="not-delivered"><CircleAlert size={13} /> Not delivered</span>;
  if (status === "read") return <CheckCheck size={14} className="read" aria-label="Read" />;
  if (status === "delivered") return <CheckCheck size={14} aria-label="Delivered" />;
  if (status === "sent") return <Check size={14} aria-label="Sent" />;
  return <span className="muted small">Sending…</span>;
}

function Bubble({ m }: { m: ThreadItem }) {
  return (
    <li className={`bubble ${m.dir} by-${m.by}`}>
      {m.dir === "out" && <div className="by">{m.by === "staff" ? <><UserRound size={12} /> Staff</> : <><Bot size={12} /> Agent</>}</div>}
      <div className="body">{m.text}</div>
      {m.buttons.length > 0 && <div className="bubble-buttons">{m.buttons.map((b, i) => <span key={i}>{b}</span>)}</div>}
      <div className="meta">{clock(m.at)} {m.dir === "out" && <Ticks status={m.status} />}</div>
    </li>
  );
}

function ThreadView({ conversationId, onBack }: { conversationId: string; onBack: () => void }) {
  const thread = useThread(conversationId);
  const phone = usePhone();
  const wide = !phone && window.matchMedia("(min-width: 1200px)").matches;
  const [profile, setProfile] = useState(false);
  const end = useRef<HTMLDivElement>(null);
  const count = thread.data?.items.length ?? 0;

  useEffect(() => end.current?.scrollIntoView?.({ block: "end" }), [count]);

  if (thread.isPending) return <Empty>Loading chat…</Empty>;
  const t = thread.data;
  if (!t) return <Empty>This chat isn't available. <button className="btn small" onClick={onBack}>Back to chats</button></Empty>;

  return (
    <>
      <section className="thread" aria-label={`Chat with ${t.customerName ?? "guest"}`}>
        <header className="thread-head row">
          {phone && <button className="btn small ghost" onClick={onBack} aria-label="Back to chats"><ArrowLeft size={18} /></button>}
          <div className="grow">
            <strong>{t.customerName ?? "Guest"}</strong>{t.table ? ` · ${t.table}` : ""}
            <div className="muted small">{t.maskedPhone}</div>
          </div>
          {!wide && <button className="btn small" onClick={() => setProfile(true)}><UserRound size={15} /> Profile</button>}
        </header>
        <ModeBanner t={t} />
        <ol className="bubbles">
          {t.items.length ? t.items.map((m) => <Bubble key={`${m.dir}:${m.id}`} m={m} />) : <li className="muted">No messages in the last 7 days.</li>}
        </ol>
        <div ref={end} />
        <Composer t={t} />
      </section>
      {wide && <aside className="chat-profile" aria-label="Customer"><CustomerProfileBody customerId={t.conversation.customer_id} /></aside>}
      {profile && <CustomerProfilePanel customerId={t.conversation.customer_id} onClose={() => setProfile(false)} />}
    </>
  );
}

function useConversationAction() {
  const qc = useQueryClient();
  const toasts = useToasts();
  const { cafeId } = useSession();
  return async (fn: () => Promise<unknown>, done?: string): Promise<boolean> => {
    try {
      await fn();
      if (done) toasts.show(done);
      return true;
    } catch (e) {
      toasts.show(e instanceof Error ? e.message : "Something went wrong. Please try again.", true);
      return false;
    } finally {
      void qc.invalidateQueries({ queryKey: ["thread", cafeId] });
      void qc.invalidateQueries({ queryKey: ["chats", cafeId] });
    }
  };
}

function ModeBanner({ t }: { t: Thread }) {
  const run = useConversationAction();
  const offline = useOffline();
  const id = t.conversation.id;
  if (t.conversation.mode === "bot") {
    return (
      <div className="mode-banner bot row">
        <Bot size={16} /> <span className="grow">The bot is handling this chat.</span>
        <button className="btn small" disabled={offline} onClick={() => void run(() => conversations.takeover(id), "You're replying · the bot is paused")}>Take over</button>
      </div>
    );
  }
  const mine = t.assignedName === "You";
  const who = mine ? "You're replying. The bot is paused."
    : t.assignedName ? `${t.assignedName} is replying.` : "Waiting for a person. The bot is paused.";
  return (
    <div className="mode-banner human row">
      <UserRound size={16} />
      <span className="grow">{who}</span>
      {!mine && <button className="btn small" disabled={offline} onClick={() => void run(() => conversations.takeover(id), "You're replying")}>Take over</button>}
      <button className="btn small" disabled={offline} onClick={() => void run(() => conversations.handback(id), "Handed back to the bot")}>Hand back to bot</button>
    </div>
  );
}

function Composer({ t }: { t: Thread }) {
  const offline = useOffline();
  const toasts = useToasts();
  const qc = useQueryClient();
  const { cafeId } = useSession();
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);

  if (!t.windowOpen) {
    return <div className="composer closed">WhatsApp only allows a reply after the customer messages again.</div>;
  }

  async function send() {
    setBusy(true);
    try {
      await conversations.reply(t.conversation.id, text.trim());
      setText("");
    } catch (e) {
      toasts.show(e instanceof ApiError && e.code === "window_closed"
        ? "WhatsApp only allows a reply after the customer messages again." : e instanceof Error ? e.message : "Couldn't send", true);
    } finally {
      setBusy(false);
      void qc.invalidateQueries({ queryKey: ["thread", cafeId] });
      void qc.invalidateQueries({ queryKey: ["chats", cafeId] });
    }
  }

  return (
    <div className="composer">
      <div className="quick row" role="group" aria-label="Quick replies">
        {QUICK_REPLIES.map((q) => <button key={q} className="chip-btn" onClick={() => setText(q)}>{q}</button>)}
      </div>
      {t.conversation.mode === "bot" && <div className="hint">Sending a reply pauses the bot for this chat.</div>}
      <div className="row">
        <label className="grow field"><span className="sr-only">Reply</span>
          <textarea rows={2} maxLength={1000} value={text} onChange={(e) => setText(e.target.value)} placeholder="Write a reply…"
            onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey && text.trim() && !busy && !offline) { e.preventDefault(); void send(); } }} />
        </label>
        <button className="btn primary" disabled={!text.trim() || busy || offline} onClick={() => void send()}>Send</button>
      </div>
    </div>
  );
}

function MessageTableDialog({ onClose }: { onClose: () => void }) {
  const sessions = useOpenSessions();
  const navigate = useNavigate();
  const toasts = useToasts();
  const [busy, setBusy] = useState(false);

  async function open(id: string) {
    setBusy(true);
    try {
      const conv = await conversations.open(id);
      onClose();
      navigate(`/chats/${conv.id}`);
    } catch (e) {
      toasts.show(e instanceof Error ? e.message : "Couldn't open that table's chat", true);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog title="Message a table" onClose={onClose}>
      {sessions.isPending ? <p className="muted">Loading…</p> : sessions.data?.length ? (
        <div className="table-pick">
          {sessions.data.map((s) => <button key={s.id} className="btn" disabled={busy} onClick={() => void open(s.id)}>{s.label}</button>)}
        </div>
      ) : <p className="muted">No tables are seated right now. A table opens when a customer scans its QR code.</p>}
    </Dialog>
  );
}
