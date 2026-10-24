import { useEffect, useState } from "react";

import { api } from "../lib/api";
import type { CustomersResponse } from "../lib/types";
import { Panel } from "../panel/Panel";
import { Phone } from "../phone/Phone";
import "../styles.css";

const MAX_PHONES = 3;
const DEFAULT_PHONES = ["919999000005"];

export function App() {
  const [directory, setDirectory] = useState<CustomersResponse | null>(null);
  const [phones, setPhones] = useState<string[]>(DEFAULT_PHONES);
  const [selected, setSelected] = useState(0);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.customers().then(setDirectory).catch((e: unknown) => setError(e instanceof Error ? e.message : "api unreachable"));
  }, []);

  async function addPhone() {
    const { wa_id } = await api.newCustomer();
    setPhones((p) => [...p, wa_id]);
    setSelected(phones.length);
  }

  return (
    <div className="app">
      <header className="topbar">
        <h1>WhatsApp simulator</h1>
        <span className="pill">local only · synthetic data</span>
        <span className="muted">{directory?.cafe ?? "…"}</span>
        <span className="spacer" />
        {phones.length < MAX_PHONES && <button onClick={() => void addPhone()}>+ Add phone</button>}
        <a href="http://localhost:5173" target="_blank" rel="noreferrer">Open Restro Console ↗</a>
      </header>
      {error && <div className="banner">⚠ {error}. Is the api running on :8000 with SIMULATOR_ENABLED=true?</div>}
      <main className="stage">
        <div className="phones">
          {phones.map((waId, i) => (
            <Phone
              key={i}
              index={i}
              waId={waId}
              selected={selected === i}
              directory={directory}
              onSelect={() => setSelected(i)}
              onChangeCustomer={(id) => setPhones((p) => p.map((x, j) => (j === i ? id : x)))}
              onRemove={phones.length > 1 ? () => { setPhones((p) => p.filter((_, j) => j !== i)); setSelected(0); } : undefined}
            />
          ))}
        </div>
        <Panel waId={phones[selected] ?? null} />
      </main>
    </div>
  );
}
