// Campaigns (owner): list, report, templates and the five-step wizard (docs/console.md#campaigns).
// Only opted-in customers are ever messaged; the backend re-checks consent at every send.
import { useQueryClient } from "@tanstack/react-query";
import { Plus } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Link, Navigate, useNavigate, useParams } from "react-router-dom";

import { useToasts } from "../../app/toasts";
import { Empty } from "../../components/ui";
import { useCafe, useCampaigns, type CampaignRow, type Funnel } from "../../lib/data";
import { dayMonth, inr } from "../../lib/format";
import { campaigns, type CampaignInput } from "../../lib/mutations";
import { atLeast, useSession } from "../../lib/session";

const SEGMENTS: [CampaignInput["segment"], string, string][] = [
  ["all_opted_in", "Everyone opted in", "Every customer who said yes to offers"],
  ["lapsed", "Lapsed", "No order in the last 30 days"],
  ["regulars", "Regulars", "3 or more orders in the last 60 days"],
];
const SEGMENT_LABEL: Record<string, string> = Object.fromEntries(SEGMENTS.map(([k, l]) => [k, l]));
const STATUS: Record<CampaignRow["status"], { label: string; cls: string }> = {
  draft: { label: "Draft", cls: "calm" }, scheduled: { label: "Scheduled", cls: "info" }, sending: { label: "Sending", cls: "warning" },
  completed: { label: "Completed", cls: "ok" }, cancelled: { label: "Cancelled", cls: "calm" },
};
// What the approved marketing templates say ({{1}} first name, {{2}} offer, {{3}} code, {{4}} valid until).
const TEMPLATE_TEXT: Record<string, string> = {
  weekly_offer: "Hi {{1}}! {{2}} at {cafe} this week. Show code {{3}} at the counter. Valid till {{4}}.",
  winback: "We miss you, {{1}}! {{2}} on your next visit to {cafe}. Show code {{3}} at the counter. Valid till {{4}}.",
};

function pct(n: number, of: number): string {
  return of ? `${Math.round((n / of) * 100)} %` : "—";
}

export function CampaignsScreen() {
  const { role } = useSession();
  const { campaignId } = useParams();
  if (!atLeast(role, "owner")) return <Navigate to="/orders" replace />;
  if (campaignId === "new") return <Wizard />;
  if (campaignId) return <Report campaignId={campaignId} />;
  return <CampaignList />;
}

function CampaignList() {
  const q = useCampaigns();
  const navigate = useNavigate();
  return (
    <div className="campaigns">
      <div className="banner dark">Only opted-in customers can be messaged. Sending is throttled and may roll over to the next day.</div>
      <div className="row toolbar"><span className="grow" /><Link className="btn primary small" to="/campaigns/new"><Plus size={15} /> New campaign</Link></div>
      {q.isPending ? <Empty>Loading campaigns…</Empty> : !q.data?.list.length ? <Empty>No campaigns yet.</Empty> : (
        <table className="table">
          <thead><tr><th>Name</th><th>Audience</th><th>Status</th><th className="num">Sent</th><th className="num">Came back</th></tr></thead>
          <tbody>{q.data.list.map((c) => {
            const f = q.data.funnels.get(c.id);
            return (
              <tr key={c.id} className="clickable" onClick={() => navigate(`/campaigns/${c.id}`)}>
                <td><button className="link">{c.name}</button><div className="muted small">{c.scheduled_at ? dayMonth(c.scheduled_at) : "not scheduled"}</div></td>
                <td>{SEGMENT_LABEL[c.segment]}</td>
                <td><span className={`chip ${STATUS[c.status].cls}`}>{STATUS[c.status].label}</span></td>
                <td className="num">{f?.sent ?? 0}</td>
                <td className="num">{f?.came_back ?? 0}{f?.sent ? <span className="muted small"> · {pct(f.came_back, f.sent)}</span> : null}</td>
              </tr>
            );
          })}</tbody>
        </table>
      )}
      <h3>Templates</h3>
      <p className="muted small">Templates are created by the Restro team.</p>
      <ul className="plist">{(q.data?.templates ?? []).map((t) => (
        <li key={t.name} className="row"><span className="grow mono">{t.name}</span><span className="muted small">{t.category}</span>
          <span className={`chip ${t.status === "approved" ? "ok" : t.status === "rejected" ? "critical" : "warning"}`}>{t.status}</span></li>
      ))}</ul>
    </div>
  );
}

function FunnelBars({ f }: { f: Funnel }) {
  const steps: [string, number, string?][] = [
    ["Sent", f.sent], ["Held by WhatsApp", f.held, "Normal: WhatsApp limits how many marketing messages one person gets from all businesses."],
    ["Delivered", f.delivered + f.read], ["Read", f.read], ["Came back", f.came_back],
  ];
  return (
    <ul className="funnel">
      {steps.map(([label, n, tip]) => (
        <li key={label} title={tip}>
          <span className="label">{label}{tip ? " ⓘ" : ""}</span>
          <span className="track"><span className="fill" style={{ width: f.sent ? `${(n / f.sent) * 100}%` : 0 }} /></span>
          <span className="val">{n} · {pct(n, f.sent)}</span>
        </li>
      ))}
    </ul>
  );
}

function Report({ campaignId }: { campaignId: string }) {
  const q = useCampaigns();
  const toasts = useToasts();
  const qc = useQueryClient();
  const { cafeId } = useSession();
  const navigate = useNavigate();
  const c = q.data?.list.find((x) => x.id === campaignId);
  const f = q.data?.funnels.get(campaignId);
  if (q.isPending) return <Empty>Loading…</Empty>;
  if (!c) return <Empty>This campaign isn't available. <Link to="/campaigns">Back</Link></Empty>;
  if (c.status === "draft") return <Wizard draft={c} />;
  async function cancel() {
    try {
      await campaigns.cancel(campaignId);
      toasts.show("Campaign cancelled · messages not yet sent won't go out");
      void qc.invalidateQueries({ queryKey: ["campaigns", cafeId] });
    } catch (e) {
      toasts.show(e instanceof Error ? e.message : "Couldn't cancel", true);
    }
  }
  return (
    <div className="campaigns">
      <div className="row toolbar">
        <button className="btn small ghost" onClick={() => navigate("/campaigns")}>← Campaigns</button>
        <h2 className="grow">{c.name} <span className={`chip ${STATUS[c.status].cls}`}>{STATUS[c.status].label}</span></h2>
        {(c.status === "scheduled" || c.status === "sending") && <button className="btn danger small" onClick={() => void cancel()}>{c.status === "scheduled" ? "Cancel scheduled send" : "Stop sending"}</button>}
      </div>
      <p className="muted">{SEGMENT_LABEL[c.segment]} · {c.template_name} · {c.offer_text ?? "no offer text"}{c.discount_percent ? ` · ${c.discount_percent} % off the bill` : ""}
        {c.scheduled_at ? ` · ${new Date(c.scheduled_at).toLocaleString("en-IN")}` : ""}</p>
      {f ? (
        <>
          <FunnelBars f={f} />
          <div className="tiles">
            <div className="tile"><div className="tile-label">They spent</div><div className="tile-value">{inr(f.spent_paise)}</div><div className="tile-sub">orders with a redeemed code, cancelled excluded</div></div>
            <div className="tile"><div className="tile-label">Discount given</div><div className="tile-value">{inr(f.discount_paise)}</div></div>
            <div className="tile"><div className="tile-label">WhatsApp cost</div><div className="tile-value">—</div><div className="tile-sub">not tracked until GCP</div></div>
            {f.queued > 0 && <div className="tile"><div className="tile-label">Still to send</div><div className="tile-value">{f.queued}</div><div className="tile-sub">within the daily limit</div></div>}
          </div>
        </>
      ) : <Empty>No messages sent yet.</Empty>}
    </div>
  );
}

const STEPS = ["Audience", "Template", "Message", "Schedule", "Review"] as const;

function localInput(d: Date): string {
  const z = new Date(d.getTime() - d.getTimezoneOffset() * 60_000);
  return z.toISOString().slice(0, 16);
}

function Wizard({ draft }: { draft?: CampaignRow }) {
  const q = useCampaigns();
  const cafe = useCafe();
  const toasts = useToasts();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const { cafeId } = useSession();
  const [step, setStep] = useState(0);
  const [id, setId] = useState<string | null>(draft?.id ?? null);
  const [segment, setSegment] = useState<CampaignInput["segment"]>(draft?.segment ?? "all_opted_in");
  const [template, setTemplate] = useState(draft?.template_name ?? "");
  const [name, setName] = useState(draft?.name ?? "");
  const [offer, setOffer] = useState(draft?.offer_text ?? "");
  const [discount, setDiscount] = useState(draft?.discount_percent ? String(draft.discount_percent) : "20");
  const [prefix, setPrefix] = useState(draft?.code_prefix ?? "BACK20");
  const [validDays, setValidDays] = useState("7");
  const [when, setWhen] = useState(localInput(new Date(Date.now() + 2 * 3600_000)));
  const [preview, setPreview] = useState<Awaited<ReturnType<typeof campaigns.preview>> | null>(null);
  const [busy, setBusy] = useState(false);

  const marketing = useMemo(() => (q.data?.templates ?? []).filter((t) => t.category === "marketing"), [q.data]);
  useEffect(() => { if (!template && marketing.find((t) => t.status === "approved")) setTemplate(marketing.find((t) => t.status === "approved")!.name); }, [marketing, template]);
  useEffect(() => {
    if (!template) return;
    let live = true;
    void campaigns.preview(segment, template).then((p) => live && setPreview(p)).catch(() => live && setPreview(null));
    return () => { live = false; };
  }, [segment, template]);

  const discountN = Number(discount);
  const validUntil = new Date(Date.now() + Number(validDays) * 86_400_000);
  const body: CampaignInput = {
    name: name.trim() || `${SEGMENT_LABEL[segment]} · ${new Date().toLocaleDateString("en-IN", { day: "numeric", month: "short" })}`,
    segment, template_name: template, offer_text: offer.trim() || null, discount_percent: discountN || null,
    code_prefix: prefix || null, valid_until: validUntil.toISOString(),
  };
  const sample = `${prefix || "OFFER"}-7KQ`;
  const text = (TEMPLATE_TEXT[template] ?? "{{2}} · code {{3}} · valid till {{4}}")
    .replace("{{1}}", "Riya").replace("{{2}}", offer.trim() || "A little something on us").replace("{{3}}", sample)
    .replace("{{4}}", validUntil.toLocaleDateString("en-IN", { day: "numeric", month: "short" })).replace("{cafe}", cafe.data?.name ?? "us");
  const scheduledAt = new Date(when);
  const approved = marketing.find((t) => t.name === template)?.status === "approved";
  const blockers = [
    !approved && "The template isn't approved by WhatsApp yet.",
    preview && preview.will_receive === 0 && "Nobody in this audience has opted in to offers.",
    !(scheduledAt.getTime() > Date.now()) && "Pick a time in the future.",
    cafe.data?.status === "disconnected" && "WhatsApp is disconnected.",
    !/^[A-Z0-9]{2,10}$/.test(prefix) && "The code prefix needs 2–10 capital letters or digits.",
  ].filter(Boolean) as string[];
  const messageValid = offer.length <= 120 && /^[A-Z0-9]{2,10}$/.test(prefix) && discountN >= 1 && discountN <= 90 && Number(validDays) >= 1;

  async function saveDraft(): Promise<string | null> {
    try {
      const saved = id ? await campaigns.update(id, body) : await campaigns.create(body);
      setId(saved.id);
      void qc.invalidateQueries({ queryKey: ["campaigns", cafeId] });
      return saved.id;
    } catch (e) {
      toasts.show(e instanceof Error ? e.message : "Couldn't save the draft", true);
      return null;
    }
  }

  async function next() {
    if (step === 2 && !(await saveDraft())) return; // draft saved on Continue past Message
    setStep(step + 1);
  }

  async function schedule() {
    setBusy(true);
    const cid = await saveDraft();
    if (cid) {
      try {
        await campaigns.schedule(cid, scheduledAt.toISOString());
        toasts.show(`Scheduled for ${scheduledAt.toLocaleString("en-IN")}`);
        void qc.invalidateQueries({ queryKey: ["campaigns", cafeId] });
        navigate(`/campaigns/${cid}`);
      } catch (e) {
        toasts.show(e instanceof Error ? e.message : "Couldn't schedule", true);
      }
    }
    setBusy(false);
  }

  return (
    <div className="wizard">
      <ol className="steps">{STEPS.map((s, i) => <li key={s} className={i === step ? "on" : i < step ? "done" : ""}>{i + 1}. {s}</li>)}</ol>
      <div className="wizard-body">
        <section className="wizard-form">
          {step === 0 && (
            <fieldset className="choices"><legend>Who gets it?</legend>
              {SEGMENTS.map(([k, label, hint]) => (
                <label key={k} className={`choice${segment === k ? " on" : ""}`}><input type="radio" name="segment" checked={segment === k} onChange={() => setSegment(k)} />
                  <span><strong>{label}</strong><span className="muted small"> · {hint}</span></span></label>
              ))}
              {preview && <p className="muted">{preview.will_receive} opted in · {preview.excluded} in this group haven't opted in and won't get it.</p>}
            </fieldset>
          )}
          {step === 1 && (
            <fieldset className="choices"><legend>Template</legend>
              {marketing.length ? marketing.map((t) => (
                <label key={t.name} className={`choice${template === t.name ? " on" : ""}`}>
                  <input type="radio" name="template" disabled={t.status !== "approved"} checked={template === t.name} onChange={() => setTemplate(t.name)} />
                  <span className="mono">{t.name}</span> <span className={`chip ${t.status === "approved" ? "ok" : "warning"}`}>{t.status}</span></label>
              )) : <p className="muted">No marketing templates yet. The Restro team submits them to WhatsApp.</p>}
            </fieldset>
          )}
          {step === 2 && (
            <div className="fields">
              <label className="field">Campaign name (only you see it)<input value={name} maxLength={80} onChange={(e) => setName(e.target.value)} placeholder={body.name} /></label>
              <label className="field">Offer text<input value={offer} maxLength={120} onChange={(e) => setOffer(e.target.value)} placeholder="20% off your next visit" /></label>
              <label className="field">Discount on the bill (%)<input inputMode="numeric" value={discount} onChange={(e) => setDiscount(e.target.value.replace(/\D/g, ""))} /></label>
              <label className="field">Code prefix<input value={prefix} maxLength={10} onChange={(e) => setPrefix(e.target.value.toUpperCase().replace(/[^A-Z0-9]/g, ""))} /></label>
              <label className="field">Valid for (days)<input inputMode="numeric" value={validDays} onChange={(e) => setValidDays(e.target.value.replace(/\D/g, ""))} /></label>
              <p className="hint">Each customer gets their own code like {sample}; the shift manager types it on the bill.</p>
            </div>
          )}
          {step === 3 && (
            <label className="field">Send at<input type="datetime-local" value={when} onChange={(e) => setWhen(e.target.value)} /></label>
          )}
          {step === 4 && (
            <div className="review">
              <dl className="facts">
                <dt>Audience</dt><dd>{SEGMENT_LABEL[segment]}</dd>
                <dt>In this group</dt><dd>{preview?.in_segment ?? "—"}</dd>
                <dt>Not opted in (excluded)</dt><dd>{preview?.excluded ?? "—"}</dd>
                <dt>Will receive</dt><dd><strong>{preview?.will_receive ?? "—"}</strong></dd>
                <dt>Daily limit</dt><dd>{preview?.daily_limit ?? "—"} a day from this number · {preview?.days_needed ?? "—"} day(s) needed</dd>
                <dt>Estimated cost</dt><dd>not tracked until GCP</dd>
                <dt>Send at</dt><dd>{scheduledAt.toLocaleString("en-IN")}</dd>
              </dl>
              {preview?.quality && preview.quality !== "High" && <div className="banner warning">Number quality is {preview.quality}: marketing can lower it further.</div>}
              {blockers.map((b) => <p key={b} className="error-text">{b}</p>)}
            </div>
          )}
          <div className="row wizard-nav">
            <button className="btn" onClick={() => (step ? setStep(step - 1) : navigate("/campaigns"))}>{step ? "Back" : "Cancel"}</button>
            <span className="grow" />
            {step < 4 ? <button className="btn primary" disabled={(step === 1 && !approved) || (step === 2 && !messageValid)} onClick={() => void next()}>Continue</button>
              : <button className="btn primary" disabled={blockers.length > 0 || busy} onClick={() => void schedule()}>Schedule</button>}
          </div>
        </section>
        <aside className="wa-preview" aria-label="WhatsApp preview">
          <div className="wa-bubble">
            <div>{text}</div>
            <div className="wa-footer">Reply STOP to opt out</div>
          </div>
          <div className="wa-buttons"><span>Order now</span><span>Stop offers</span></div>
        </aside>
      </div>
    </div>
  );
}
