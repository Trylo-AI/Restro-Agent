import type { ListSection } from "../lib/types";

interface Props {
  title: string;
  sections: ListSection[];
  onPick: (id: string, title: string) => void;
  onClose: () => void;
}

export function ListSheet({ title, sections, onPick, onClose }: Props) {
  return (
    <div className="sheet-backdrop" onClick={onClose}>
      <div className="sheet" onClick={(e) => e.stopPropagation()} role="dialog" aria-label={title}>
        <div className="sheet-head">
          <button className="sheet-close" onClick={onClose} aria-label="Close">✕</button>
          <span>{title}</span>
        </div>
        {sections.map((s) => (
          <div key={s.title} className="sheet-section">
            <div className="sheet-section-title">{s.title}</div>
            {s.rows.map((r) => (
              <button key={r.id} className="sheet-row" onClick={() => onPick(r.id, r.title)} title={r.id}>
                <span className="sheet-row-title">{r.title}</span>
                {r.description && <span className="sheet-row-desc">{r.description}</span>}
                <span className="radio" />
              </button>
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}
