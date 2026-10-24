import type { ListSection, ThreadItem } from "../lib/types";

const time = (iso: string) => new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });

function Ticks({ status }: { status: string }) {
  if (status === "failed") return <span className="ticks failed" title="Not delivered">!</span>;
  if (status === "ambiguous") return <span className="ticks pending" title="Ambiguous: checking with WhatsApp">?</span>;
  if (status === "pending") return <span className="ticks pending" title="Sending">🕓</span>;
  const cls = status === "read" ? "ticks read" : "ticks";
  return <span className={cls} title={status}>{status === "sent" ? "✓" : "✓✓"}</span>;
}

interface Props {
  item: ThreadItem;
  onButton: (id: string, title: string) => void;
  onOpenList: (title: string, button: string, sections: ListSection[]) => void;
}

export function Bubble({ item, onButton, onOpenList }: Props) {
  if (item.dir === "in") {
    const p = item.payload;
    let body: string;
    if (p.type === "text") body = p.text?.body ?? "";
    else if (p.type === "interactive") body = p.interactive?.button_reply?.title ?? p.interactive?.list_reply?.title ?? "(tap)";
    else body = { image: "📷 Photo", audio: "🎤 Voice message", sticker: "🙂 Sticker" }[p.type] ?? `(${p.type})`;
    return (
      <div className="row out-row">
        <div className="bubble customer">
          <span className="body">{body}</span>
          <span className="meta">{time(item.at)}</span>
        </div>
      </div>
    );
  }

  const p = item.payload;
  const inter = p.interactive;
  return (
    <div className="row in-row">
      <div className={`bubble business${item.status === "failed" ? " failed" : ""}`}>
        {item.violations.length > 0 && (
          <div className="violation" title="Breaks a Meta limit">⚠ {item.violations.join(" · ")}</div>
        )}
        {p.type === "template" && p.template && (
          <>
            <span className="tag">Template · {p.template.name}</span>
            <span className="body">{p.template.components[0]?.parameters.map((x) => x.text).join(" · ")}</span>
          </>
        )}
        {inter?.header && <strong className="header">{inter.header.text}</strong>}
        {p.type === "text" && <span className="body">{p.text?.body}</span>}
        {inter && <span className="body">{inter.body.text}</span>}
        {inter?.footer && <span className="footer">{inter.footer.text}</span>}
        <span className="meta">
          {time(item.at)} <Ticks status={item.status} />
        </span>
      </div>
      {inter?.type === "button" && (
        <div className="buttons">
          {inter.action.buttons?.map((b) => (
            <button key={b.reply.id} onClick={() => onButton(b.reply.id, b.reply.title)} title={b.reply.id}>
              {b.reply.title}
            </button>
          ))}
        </div>
      )}
      {inter?.type === "list" && (
        <div className="buttons">
          <button onClick={() => onOpenList(inter.body.text, inter.action.button ?? "Options", inter.action.sections ?? [])}>
            ☰ {inter.action.button}
          </button>
        </div>
      )}
    </div>
  );
}
