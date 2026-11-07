// Public landing page at "/" for signed-out visitors (Mehmanawazi brand guide v1.0). Signed-in users never see it:
// App sends them to their role's home. Everything here is static marketing copy and example data; it reads nothing.
import {
  ArrowRight, BadgePercent, BellRing, Check, ChefHat, ConciergeBell, HeartHandshake, Languages, Mail, Menu, MessageCircle,
  Phone, Quote, RotateCcw, ShieldCheck, Smartphone, TrendingUp, Users, Volume2, VolumeX, X,
} from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";

import { Cloche, CoffeeCup, Logo, Plant } from "./art";
import { isMuted, play, setMuted, tapSound } from "./sfx";
import "./landing.css";

export const BRAND = "Mehmanawazi";
export const CONTACT = {
  email: "team@mehmanawazi.com",
  phone: "+91 99107 72433",
  tel: "tel:+919910772433",
  whatsapp: `https://wa.me/919910772433?text=${encodeURIComponent("Hi Mehmanawazi, I'd like a demo for my café.")}`,
};
const FONT_HREF = "https://fonts.googleapis.com/css2?family=Google+Sans:wght@400;500;600;700&family=Google+Sans+Flex:wght@800;900&family=Google+Sans+Text:wght@400;500;700&display=swap";
const reducedMotion = () => typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches;
const clamp01 = (n: number) => Math.min(1, Math.max(0, n));

/* ---------- Scroll helpers ---------- */

/** Writes the element's scroll progress (0 → 1) to its `--p` CSS variable, without re-rendering. */
function useScrollVar(ref: React.RefObject<HTMLElement | null>, progress: (r: DOMRect, vh: number) => number) {
  const fn = useRef(progress);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (reducedMotion()) { el.style.setProperty("--p", "1"); return; }
    let raf = 0;
    const update = () => { raf = 0; el.style.setProperty("--p", clamp01(fn.current(el.getBoundingClientRect(), window.innerHeight)).toFixed(3)); };
    const onScroll = () => { if (!raf) raf = requestAnimationFrame(update); };
    update();
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onScroll);
    return () => { window.removeEventListener("scroll", onScroll); window.removeEventListener("resize", onScroll); cancelAnimationFrame(raf); };
  }, [ref]);
}

/** True once the element has scrolled into view (always true without IntersectionObserver). */
function useInView<T extends HTMLElement>(threshold = 0.3): [React.RefObject<T>, boolean] {
  const ref = useRef<T>(null);
  const [seen, setSeen] = useState(() => typeof IntersectionObserver !== "function");
  useEffect(() => {
    const el = ref.current;
    if (!el || seen) return;
    const io = new IntersectionObserver(([e]) => { if (e?.isIntersecting) { setSeen(true); io.disconnect(); } }, { threshold });
    io.observe(el);
    return () => io.disconnect();
  }, [seen, threshold]);
  return [ref, seen];
}

function useReveal(root: React.RefObject<HTMLElement | null>) {
  useEffect(() => {
    const el = root.current;
    if (!el || reducedMotion() || typeof IntersectionObserver !== "function") return;
    const targets = [...el.querySelectorAll<HTMLElement>("[data-reveal]")];
    const io = new IntersectionObserver((entries) => {
      for (const e of entries) if (e.isIntersecting) { e.target.classList.remove("lp-pre"); io.unobserve(e.target); }
    }, { rootMargin: "0px 0px -8% 0px" });
    // Only what starts below the fold waits to animate in; anything already on screen stays visible.
    for (const t of targets) {
      if (t.getBoundingClientRect().top > window.innerHeight) { t.classList.add("lp-pre"); io.observe(t); }
    }
    return () => { io.disconnect(); targets.forEach((t) => t.classList.remove("lp-pre")); };
  }, [root]);
}

/** Index of the card nearest the start of a horizontal scroller (phone carousels). `reset` restarts it at 0. */
function useCarouselIndex(ref: React.RefObject<HTMLElement | null>, reset: unknown = null) {
  const [i, setI] = useState(0);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.scrollLeft = 0;
    const on = () => {
      const kids = [...el.children] as HTMLElement[];
      const w = kids[0]?.offsetWidth ?? 1;
      setI(Math.max(0, Math.min(kids.length - 1, Math.round(el.scrollLeft / (w + 12)))));
    };
    on();
    el.addEventListener("scroll", on, { passive: true });
    return () => el.removeEventListener("scroll", on);
  }, [ref, reset]);
  return i;
}

function Dots({ n, i }: { n: number; i: number }) {
  return <div className="lp-dots" aria-hidden="true">{Array.from({ length: n }, (_, j) => <span key={j} className={j === i ? "is-on" : ""} />)}</div>;
}

function Count({ to, prefix = "", run }: { to: number; prefix?: string; run: boolean }) {
  const [n, setN] = useState(() => (reducedMotion() ? to : 0));
  useEffect(() => {
    if (!run || reducedMotion()) { if (run) setN(to); return; }
    const start = performance.now();
    let raf = requestAnimationFrame(function tick(t) {
      const k = clamp01((t - start) / 1400);
      setN(Math.round(to * (1 - Math.pow(1 - k, 3))));
      if (k < 1) raf = requestAnimationFrame(tick);
    });
    return () => cancelAnimationFrame(raf);
  }, [run, to]);
  return <span className="lp-num">{prefix}{n.toLocaleString("en-IN")}</span>;
}

/* ---------- Small pieces ---------- */

function Msg({ from, children, time }: { from: "guest" | "bot"; children: ReactNode; time?: string }) {
  return <div className={`lp-b lp-b-${from}`}>{children}{time && <time>{time}</time>}</div>;
}

function Buttons({ items }: { items: string[] }) {
  return <div className="lp-qr">{items.map((t) => <span key={t}>{t}</span>)}</div>;
}

function Tag({ tone = "gray", children }: { tone?: "gray" | "red" | "green" | "amber" | "dark"; children: ReactNode }) {
  return <span className={`lp-tag lp-tag-${tone}`}>{children}</span>;
}

/** A fixed QR-like pattern (decorative, not scannable): three finder squares plus seeded noise. */
const QR_N = 21;
const QR_CELLS: boolean[] = (() => {
  let seed = 7;
  const rnd = () => ((seed = (seed * 1103515245 + 12345) % 2147483648) / 2147483648);
  const finder = (x: number, y: number) => {
    for (const [fx, fy] of [[0, 0], [QR_N - 7, 0], [0, QR_N - 7]] as const) {
      const dx = x - fx, dy = y - fy;
      if (dx >= -1 && dx <= 7 && dy >= -1 && dy <= 7) {
        if (dx < 0 || dy < 0 || dx > 6 || dy > 6) return false;
        return dx === 0 || dy === 0 || dx === 6 || dy === 6 || (dx >= 2 && dx <= 4 && dy >= 2 && dy <= 4);
      }
    }
    return null;
  };
  return Array.from({ length: QR_N * QR_N }, (_, i) => finder(i % QR_N, Math.floor(i / QR_N)) ?? rnd() > 0.52);
})();

function FakeQr({ size = 72 }: { size?: number }) {
  return (
    <svg className="lp-fakeqr" width={size} height={size} viewBox={`-1 -1 ${QR_N + 2} ${QR_N + 2}`} aria-hidden="true">
      <rect x="-1" y="-1" width={QR_N + 2} height={QR_N + 2} fill="#fff" />
      {QR_CELLS.map((on, i) => on && <rect key={i} x={i % QR_N} y={Math.floor(i / QR_N)} width="1.02" height="1.02" fill="#111111" />)}
    </svg>
  );
}

/* ---------- Nav ---------- */

/**
 * "Log in" target. Inside the console (no prop) it is the console's own /login route. The standalone public site
 * (frontend/site, GitHub Pages) passes the console's address, or null to hide the link when there is none yet.
 */
type LoginHref = string | null | undefined;
function LoginLink({ href, className, children }: { href: LoginHref; className?: string; children: ReactNode }) {
  if (href === null) return null;
  if (href) return <a href={href} className={className}>{children}</a>;
  return <Link to="/login" className={className}>{children}</Link>;
}

const SECTIONS: [string, string][] = [["#problems", "Problems"], ["#how", "How it works"], ["#demo", "Try it"], ["#contact", "Contact"]];

function Nav({ loginHref }: { loginHref: LoginHref }) {
  const [open, setOpen] = useState(false);
  const [muted, setM] = useState(isMuted);
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);
  function toggleSound() {
    const next = !muted;
    setMuted(next);
    setM(next);
    if (!next) tapSound("success");
  }
  return (
    <header className={`lp-nav ${open ? "is-open" : ""}`}>
      <div className="lp-wrap lp-nav-in">
        <a className="lp-logo" href="#top" onClick={() => setOpen(false)}><Logo size={30} />{BRAND}</a>
        <nav className="lp-nav-links" aria-label="Sections">
          {SECTIONS.map(([h, l]) => <a key={h} href={h}>{l}</a>)}
        </nav>
        <div className="lp-nav-cta">
          <button type="button" className="lp-icon-btn" onClick={toggleSound} aria-pressed={!muted} aria-label={muted ? "Turn demo sounds on" : "Turn demo sounds off"} title={muted ? "Sound off" : "Sound on"}>
            {muted ? <VolumeX size={18} /> : <Volume2 size={18} />}
          </button>
          <LoginLink href={loginHref} className="lp-btn lp-btn-text lp-hide-sm">Log in</LoginLink>
          <a href={CONTACT.whatsapp} target="_blank" rel="noopener noreferrer" className="lp-btn lp-btn-accent lp-btn-sm lp-hide-sm">Book a demo</a>
          <button type="button" className="lp-icon-btn lp-show-sm" onClick={() => { tapSound(); setOpen((o) => !o); }} aria-expanded={open} aria-controls="lp-menu" aria-label={open ? "Close menu" : "Open menu"}>
            {open ? <X size={20} /> : <Menu size={20} />}
          </button>
        </div>
      </div>
      <div className="lp-menu" id="lp-menu" hidden={!open}>
        <nav className="lp-wrap" aria-label="Menu">
          {SECTIONS.map(([h, l]) => <a key={h} href={h} onClick={() => setOpen(false)}>{l}<ArrowRight size={18} /></a>)}
          <div className="lp-menu-cta">
            <a href={CONTACT.whatsapp} target="_blank" rel="noopener noreferrer" className="lp-btn lp-btn-accent"><MessageCircle size={18} /> Book a demo</a>
            <LoginLink href={loginHref} className="lp-btn lp-btn-ghost">Log in</LoginLink>
          </div>
        </nav>
      </div>
    </header>
  );
}

/** Phone-only bar after the hero: the two things a phone visitor wants. */
function MobileBar() {
  const [show, setShow] = useState(false);
  useEffect(() => {
    const on = () => setShow(window.scrollY > window.innerHeight * 0.8);
    on();
    window.addEventListener("scroll", on, { passive: true });
    return () => window.removeEventListener("scroll", on);
  }, []);
  return (
    <div className={`lp-mbar ${show ? "is-on" : ""}`} aria-hidden={!show}>
      <a href={CONTACT.whatsapp} target="_blank" rel="noopener noreferrer" className="lp-btn lp-btn-accent" tabIndex={show ? 0 : -1}><MessageCircle size={18} /> Book a demo</a>
      <a href={CONTACT.tel} className="lp-btn lp-btn-ghost lp-mbar-call" aria-label={`Call ${CONTACT.phone}`} tabIndex={show ? 0 : -1}><Phone size={18} /></a>
    </div>
  );
}

/* ---------- Hero ---------- */

const HERO: { from: "guest" | "bot"; ticket?: string; body: ReactNode; time: string }[] = [
  { from: "guest", time: "7:42", body: "hi! kuch cold aur light chahiye?" },
  { from: "bot", time: "7:42", body: (<>Two cold, light picks on today:
    <div className="lp-picks">
      <div className="lp-pick"><b>Lemon Mint Cooler</b><span>₹150</span></div>
      <div className="lp-pick"><b>Cold Brew Tonic</b><span>₹210</span></div>
    </div></>) },
  { from: "guest", time: "7:43", body: "1 cold brew tonic + avocado toast, no chilli flakes" },
  { from: "bot", time: "7:43", body: (<>Your order:
    <div className="lp-sum">
      <div><span>1 × Cold Brew Tonic</span><span>₹210</span></div>
      <div><span>1 × Avocado Toast</span><span>₹320</span></div>
      <div className="lp-sum-note"><span>no chilli flakes</span></div>
      <div className="lp-sum-total"><span>Total</span><span>₹530</span></div>
    </div>
    <Buttons items={["Confirm", "Change"]} /></>) },
  { from: "guest", time: "7:43", ticket: "NEW", body: "Confirm" },
  { from: "bot", time: "7:44", ticket: "ACCEPTED", body: "Order #1041 accepted. Ready in ~12 min." },
  { from: "bot", time: "7:55", ticket: "READY", body: "Ready! On its way to table 7." },
];

function HeroPhone() {
  const [shown, setShown] = useState(() => (reducedMotion() ? HERO.length : 0));
  const [typing, setTyping] = useState(false);
  const [run, setRun] = useState(0);
  const chat = useRef<HTMLDivElement>(null);
  const stage = useRef<HTMLDivElement>(null);
  useScrollVar(stage, (r, vh) => (vh - r.top) / (vh * 0.75));

  useEffect(() => {
    if (shown >= HERO.length) return;
    const next = HERO[shown]!;
    const lead = shown === 0 ? 700 : next.from === "bot" ? 500 : 1100;
    const t1 = window.setTimeout(() => setTyping(next.from === "bot"), lead);
    const t2 = window.setTimeout(() => {
      setTyping(false);
      setShown((n) => n + 1);
      play(next.from === "bot" ? "receive" : "send"); // silent until the visitor has tapped something
    }, lead + (next.from === "bot" ? 900 : 0));
    return () => { window.clearTimeout(t1); window.clearTimeout(t2); };
  }, [shown, run]);

  useEffect(() => {
    const c = chat.current;
    if (!c) return;
    if (typeof c.scrollTo === "function") c.scrollTo({ top: c.scrollHeight, behavior: reducedMotion() ? "auto" : "smooth" });
    else c.scrollTop = c.scrollHeight;
  }, [shown, typing]);

  const status = [...HERO.slice(0, shown)].reverse().find((m) => m.ticket)?.ticket;
  const done = shown >= HERO.length;

  return (
    <div className="lp-stage" ref={stage}>
      <CoffeeCup className="lp-art lp-art-cup" />
      <Plant className="lp-art lp-art-plant" />
      <div className="lp-stage-tilt">
        <div className="lp-phone" aria-label="Example WhatsApp order at table 7">
          <div className="lp-phone-head">
            <span className="lp-avatar">KC</span>
            <div><strong>Kettle &amp; Crumb</strong><small>Table 7 · online</small></div>
          </div>
          <div className="lp-chat" ref={chat} aria-live="polite">
            {HERO.slice(0, shown).map((m, i) => <Msg key={`${run}-${i}`} from={m.from} time={m.time}>{m.body}</Msg>)}
            {typing && <div className="lp-typing" aria-label="Typing"><i /><i /><i /></div>}
          </div>
        </div>
        <div className={`lp-ticket lp-float-r ${status ? "is-on" : ""}`} aria-hidden={!status}>
          <div className="lp-ticket-label">Kitchen sees</div>
          <div className="lp-ticket-top"><b>T7 · #1041</b><span className="lp-status" data-s={status ?? "NEW"}>{status ?? "NEW"}</span></div>
          <div className="lp-ticket-row"><span>1 × Cold Brew Tonic</span><span>210</span></div>
          <div className="lp-ticket-row"><span>1 × Avocado Toast</span><span>320</span></div>
          <div className="lp-ticket-note">no chilli flakes</div>
        </div>
        <div className={`lp-float-l ${done ? "is-on" : ""}`}>
          <div className="lp-mini-card"><Tag tone="green">Owner sees</Tag><b>Rohan · 11th visit</b><small>Regular · usually less sugar</small></div>
          <button type="button" className="lp-replay" onClick={() => { tapSound(); setShown(0); setRun((r) => r + 1); }} disabled={!done}>
            <RotateCcw size={14} /> Replay with sound
          </button>
        </div>
      </div>
    </div>
  );
}

/* ---------- Statement that lights up as you scroll ---------- */

function Statement() {
  const ref = useRef<HTMLDivElement>(null);
  useScrollVar(ref, (r, vh) => (vh * 0.85 - r.top) / (r.height + vh * 0.25));
  const words = "Guests order without waiting. Staff never miss a table. You know who comes back.".split(" ");
  return (
    <section className="lp-statement" aria-label="What we do">
      <div className="lp-wrap" ref={ref} style={{ "--n": words.length } as React.CSSProperties}>
        <p>{words.map((w, i) => <span key={i} style={{ "--i": i } as React.CSSProperties}>{w} </span>)}</p>
      </div>
    </section>
  );
}

/* ---------- Problems: each one a short line and a tiny example ---------- */

type Item = { pain: string; fix: string; ex: ReactNode };
const PROBLEMS: { id: string; tab: string; items: Item[] }[] = [
  {
    id: "choosing", tab: "Choosing", items: [
      { pain: "Guests don't know what to try", fix: "Instant picks from today's menu",
        ex: <><Msg from="guest">What's good here?</Msg><Msg from="bot">Try the <b>Cold Brew Tonic</b> ₹210 or <b>Peri Peri Sandwich</b> ₹220</Msg></> },
      { pain: "Staff explain dishes all night", fix: "Answers ingredients and options",
        ex: <><Msg from="guest">What's in the paneer sandwich?</Msg><Msg from="bot">Paneer, peri peri mayo, onion. We can skip the onion.</Msg></> },
      { pain: "No guidance in the rush", fix: "Every table answered at once",
        ex: <div className="lp-ex-tables">{["T3", "T7", "T9", "T12", "T15", "T18"].map((t) => <span key={t}>{t}<i>✓</i></span>)}<small>6 tables helped together</small></div> },
    ],
  },
  {
    id: "ordering", tab: "Ordering", items: [
      { pain: "Waiting for staff to take the order", fix: "Scan and order from the table",
        ex: <div className="lp-ex-row"><FakeQr size={64} /><ArrowRight size={16} /><Msg from="guest">Hi! Table 4</Msg></div> },
      { pain: "Wrong dish or customisation", fix: "Guest confirms every note",
        ex: <Msg from="bot"><div className="lp-sum lp-sum-tight"><div><span>2 × Cold Coffee</span><span>₹360</span></div><div className="lp-sum-note"><span>1 × less sugar</span></div></div><Buttons items={["Confirm"]} /></Msg> },
      { pain: "Language barriers", fix: "Any language, one ticket",
        ex: <div className="lp-ex-langs"><span>Two cold coffees</span><span>दो कोल्ड कॉफ़ी</span><span>2 cold coffee</span><ArrowRight size={16} /><b>2 × Cold Coffee</b></div> },
      { pain: "Carrying orders to the kitchen", fix: "Straight to the console",
        ex: <div className="lp-ex-ticket"><b>T12 · #1042</b><Tag tone="red">NEW</Tag><span>2 × Cold Coffee</span><span>1 × Paneer Sandwich</span></div> },
      { pain: "“Where's my order?”", fix: "Updates sent automatically",
        ex: <><Msg from="bot">Accepted ✓ Ready in ~12 min</Msg><Msg from="bot">Ready! On its way to table 12.</Msg></> },
    ],
  },
  {
    id: "table", tab: "At the table", items: [
      { pain: "Can't catch a waiter", fix: "One-tap Call waiter",
        ex: <div className="lp-ex-row"><span className="lp-ex-call"><BellRing size={15} /> Call waiter</span><ArrowRight size={16} /><Tag tone="red">T4 · staff notified</Tag></div> },
      { pain: "Late food, no explanation", fix: "Delay message, automatically",
        ex: <Msg from="bot">Running 5 min behind. New time: ~17 min.</Msg> },
      { pain: "Incorrect or missing items", fix: "One ticket, guest to kitchen",
        ex: <ul className="lp-ex-check"><li>Cold Brew Tonic</li><li>Avocado Toast <em>no chilli</em></li></ul> },
      { pain: "Sold out, found out too late", fix: "One tap hides it everywhere",
        ex: <ul className="lp-ex-menu"><li><span>Chocolate Brownie</span><Tag>Sold out</Tag></li><li><span>Cold Coffee</span><Tag tone="green">Available</Tag></li></ul> },
      { pain: "Which table needs help?", fix: "Live queue with timers",
        ex: <ul className="lp-ex-menu"><li><span><b>T4</b> · Call waiter</span><Tag tone="red">0:42</Tag></li><li><span><b>T9</b> · Bill</span><Tag tone="amber">0:12</Tag></li></ul> },
      { pain: "No easy way to complain", fix: "Goes straight to a manager",
        ex: <><Msg from="guest">My coffee was cold</Msg><Tag tone="dark">Manager notified</Tag></> },
    ],
  },
  {
    id: "return", tab: "Coming back", items: [
      { pain: "Who hasn't returned in a while?", fix: "Lapsed: no order in 30 days",
        ex: <ul className="lp-ex-menu"><li><span>Meera K.</span><Tag>34 days</Tag></li><li><span>Arjun S.</span><Tag>41 days</Tag></li></ul> },
      { pain: "Which regulars stopped coming?", fix: "Sort lapsed by past visits",
        ex: <ul className="lp-ex-menu"><li><span>Farah N. · 9 visits</span><Tag tone="amber">47 days</Tag></li><li><span>Meera K. · 6 visits</span><Tag tone="amber">34 days</Tag></li></ul> },
      { pain: "How do first-timers come back?", fix: "Ask after a good meal",
        ex: <Msg from="bot">Want offers from us?<Buttons items={["Yes", "No thanks"]} /></Msg> },
      { pain: "Know regulars without discounts?", fix: "Their profile on every order",
        ex: <div className="lp-ex-profile"><span className="lp-avatar">RM</span><div><b>Rohan · 11 visits</b><small>Usual: cold coffee, less sugar</small></div></div> },
      { pain: "Offers that feel relevant?", fix: "Send to the right group",
        ex: <div className="lp-ex-pills"><Tag tone="dark">Lapsed · 148</Tag><Tag>Regulars · 96</Tag><Tag>All · 612</Tag></div> },
      { pain: "Who responds to offers?", fix: "Every guest, step by step",
        ex: <div className="lp-ex-pills"><Tag tone="green">Read ✓</Tag><Tag tone="green">Replied ✓</Tag><Tag tone="green">Came back ✓</Tag><Tag tone="green">Redeemed ✓</Tag></div> },
      { pain: "Did the campaign bring sales?", fix: "Visits and spend per offer",
        ex: <div className="lp-ex-big"><b>26</b><span>redeemed</span><b>₹18,460</b><span>spent</span></div> },
      { pain: "Stay in touch after they leave?", fix: "On their WhatsApp, with consent",
        ex: <Msg from="bot">20% off this weekend · <span className="lp-num">BACK20-7KQ</span><Buttons items={["Order now", "Stop offers"]} /></Msg> },
    ],
  },
];

function Problems() {
  const [active, setActive] = useState(0);
  const tabs = useRef<(HTMLButtonElement | null)[]>([]);
  const list = useRef<HTMLDivElement>(null);
  const group = PROBLEMS[active]!;
  const at = useCarouselIndex(list, active);
  function pick(n: number) { tapSound(); setActive(n); }
  function onKey(e: React.KeyboardEvent, i: number) {
    const d = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
    if (!d) return;
    e.preventDefault();
    const n = (i + d + PROBLEMS.length) % PROBLEMS.length;
    pick(n);
    tabs.current[n]?.focus();
  }
  return (
    <section className="lp-sec" id="problems">
      <div className="lp-wrap">
        <header className="lp-head" data-reveal>
          <p className="lp-eyebrow">Problems we solve</p>
          <h2>Small moments. Big difference.</h2>
        </header>
        <div className="lp-tabs" role="tablist" aria-label="Problem areas" data-reveal>
          {PROBLEMS.map((p, i) => (
            <button key={p.id} ref={(el) => { tabs.current[i] = el; }} role="tab" id={`lp-tab-${p.id}`} aria-controls={`lp-panel-${p.id}`}
              aria-selected={i === active} tabIndex={i === active ? 0 : -1} onClick={() => pick(i)} onKeyDown={(e) => onKey(e, i)}>
              {p.tab}<span className="lp-tab-n">{p.items.length}</span>
            </button>
          ))}
        </div>
        <div className="lp-problems lp-swipe" ref={list} role="tabpanel" id={`lp-panel-${group.id}`} aria-labelledby={`lp-tab-${group.id}`} key={group.id}>
          {group.items.map((it, i) => (
            <article className="lp-problem" key={it.pain} style={{ animationDelay: `${i * 60}ms` }}>
              <div className="lp-problem-ex">{it.ex}</div>
              <p className={`lp-problem-pain ${it.pain.endsWith("?") ? "is-q" : ""}`}>{it.pain}</p>
              <p className="lp-problem-fix">{it.fix}</p>
            </article>
          ))}
        </div>
        <div className="lp-swipe-meta"><Dots n={group.items.length} i={at} /><span>Swipe</span></div>
      </div>
    </section>
  );
}

/* ---------- How it works: pinned phone on desktop, swipe cards on phones ---------- */

const STEPS: { title: string; body: string; note: string; screen: ReactNode }[] = [
  { title: "Scan", body: "QR on the table opens WhatsApp.", note: "No app to install",
    screen: <div className="lp-scr lp-scr-scan"><div className="lp-tent"><FakeQr size={110} /><b>Table 7</b><small>Scan to order</small></div><Msg from="guest">Hi! Table 7</Msg></div> },
  { title: "Order", body: "Chat, check, confirm.", note: "Prices from your menu",
    screen: <div className="lp-scr"><Msg from="guest">2 cold coffee, ek mein sugar kam</Msg><Msg from="bot">Your order:<div className="lp-sum"><div><span>2 × Cold Coffee</span><span>₹360</span></div><div className="lp-sum-note"><span>1 × less sugar</span></div><div className="lp-sum-total"><span>Total</span><span>₹360</span></div></div><Buttons items={["Confirm", "Change"]} /></Msg></div> },
  { title: "Serve", body: "Staff see it. Guests get updates.", note: "Kitchen sees it at once",
    screen: <div className="lp-scr lp-scr-console"><div className="lp-scr-bar">Live orders</div>
      <div className="lp-ex-ticket"><b>T7 · #1043</b><Tag tone="green">READY</Tag><span>2 × Cold Coffee</span><em>1 × less sugar</em></div>
      <div className="lp-ex-ticket"><b>T3 · #1044</b><Tag tone="amber">ACCEPTED</Tag><span>1 × Masala Chai</span></div>
      <div className="lp-ex-ticket"><b>T12 · #1045</b><Tag tone="red">NEW</Tag><span>1 × Paneer Sandwich</span></div></div> },
  { title: "Come back", body: "Offers to guests who said yes.", note: "Only guests who said yes",
    screen: <div className="lp-scr"><Msg from="bot">We miss you! 20% off this weekend.<br /><span className="lp-num">BACK20-7KQ</span><Buttons items={["Order now", "Stop offers"]} /></Msg><Tag tone="green">Redeemed · ₹248 off</Tag></div> },
];

function HowItWorks() {
  const [step, setStep] = useState(0);
  const refs = useRef<(HTMLDivElement | null)[]>([]);
  const strip = useRef<HTMLDivElement>(null);
  const how = useRef<HTMLDivElement>(null);
  const at = useCarouselIndex(strip);
  useScrollVar(how, (r, vh) => (vh * 0.5 - r.top) / r.height);
  useEffect(() => { play("tap"); }, [step]); // a soft tick per step, only once the visitor has tapped something
  useEffect(() => {
    if (typeof IntersectionObserver !== "function") return;
    const io = new IntersectionObserver((entries) => {
      for (const e of entries) if (e.isIntersecting) setStep(Number((e.target as HTMLElement).dataset.i));
    }, { rootMargin: "-45% 0px -45% 0px" });
    refs.current.forEach((r) => r && io.observe(r));
    return () => io.disconnect();
  }, []);
  return (
    <section className="lp-sec lp-sec-gray" id="how">
      <div className="lp-wrap">
        <header className="lp-head" data-reveal>
          <p className="lp-eyebrow">How it works</p>
          <h2>Four steps. No app.</h2>
        </header>

        <div className="lp-how" ref={how}>
          <div className="lp-how-steps">
            <div className="lp-how-rail" aria-hidden="true"><i /></div>
            {STEPS.map((s, i) => (
              <div key={s.title} ref={(el) => { refs.current[i] = el; }} data-i={i}
                className={`lp-how-step ${i === step ? "is-on" : ""} ${i < step ? "is-done" : ""}`}>
                <span className="lp-how-dot" aria-hidden="true">{i < step ? <Check size={14} strokeWidth={3} /> : i + 1}</span>
                <span className="lp-how-n">Step {i + 1} of {STEPS.length}</span>
                <h3>{s.title}</h3>
                <p>{s.body}</p>
              </div>
            ))}
          </div>
          <div className="lp-how-pin" aria-hidden="true">
            <div className="lp-how-stage" data-step={step}>
              <span className="lp-how-big" key={`n${step}`}>{step + 1}</span>
              <div className="lp-phone lp-phone-sm">
                <div className="lp-phone-head"><span className="lp-avatar">KC</span><div><strong>Kettle &amp; Crumb</strong><small>{STEPS[step]!.title}</small></div></div>
                <div className="lp-chat lp-chat-auto lp-screen" key={step}>{STEPS[step]!.screen}</div>
              </div>
              <div className="lp-how-note" key={`c${step}`}><Check size={16} strokeWidth={3} />{STEPS[step]!.note}</div>
            </div>
          </div>
        </div>

        <div className="lp-how-strip lp-swipe" ref={strip}>
          {STEPS.map((s, i) => (
            <div className="lp-how-card" key={s.title}>
              <div className="lp-how-card-head"><span className="lp-how-n">{String(i + 1).padStart(2, "0")}</span><h3>{s.title}</h3><p>{s.body}</p></div>
              <div className="lp-how-card-screen">{s.screen}</div>
            </div>
          ))}
        </div>
        <div className="lp-swipe-meta lp-how-meta"><Dots n={STEPS.length} i={at} /><span>Swipe</span></div>
      </div>
    </section>
  );
}

/* ---------- Live demos (with sound) ---------- */

const MOODS: Record<string, { q: string; a: string; picks: [string, string][]; human?: boolean }> = {
  "Something light": { q: "Something light", a: "Two light ones on today:", picks: [["Lemon Mint Cooler", "₹150"], ["Greek Yogurt Bowl", "₹240"]] },
  "Kuch spicy?": { q: "Kuch spicy hai?", a: "Haan! These have a kick:", picks: [["Peri Peri Paneer Sandwich", "₹220"], ["Chilli Garlic Noodles", "₹260"]] },
  "Like last time": { q: "Like last time?", a: "You had a Cold Coffee, less sugar. Try:", picks: [["Vanilla Cold Coffee", "₹200"], ["Cold Brew Tonic", "₹210"]] },
  "Nut-free?": { q: "Is the brownie nut-free?", a: "Not sure, so I've asked the staff. They'll reply here.", picks: [], human: true },
};

function RecommendDemo() {
  const [mood, setMood] = useState("Something light");
  const m = MOODS[mood]!;
  function ask(k: string) {
    tapSound("send");
    setMood(k);
    window.setTimeout(() => play(MOODS[k]!.human ? "alert" : "receive"), 320);
  }
  return (
    <article className="lp-card lp-card-wide" data-reveal>
      <div className="lp-card-text">
        <p className="lp-eyebrow">Recommend</p>
        <h3>Tap a question.</h3>
        <div className="lp-chips" role="group" aria-label="Ask the assistant">
          {Object.keys(MOODS).map((k) => (
            <button key={k} type="button" className="lp-chip" aria-pressed={k === mood} onClick={() => ask(k)}>{k}</button>
          ))}
        </div>
        <p className="lp-fine">Only dishes available today. Prices from your menu.</p>
      </div>
      <div className="lp-mini" aria-live="polite" key={mood}>
        <Msg from="guest">{m.q}</Msg>
        <Msg from="bot">{m.a}
          {m.picks.length > 0 && <div className="lp-picks">{m.picks.map(([n, p]) => <div className="lp-pick" key={n}><b>{n}</b><span>{p}</span></div>)}</div>}
          {m.human && <div className="lp-handoff">Sent to staff · Table 3</div>}
        </Msg>
      </div>
    </article>
  );
}

const LANGS = {
  English: "2 cold coffees, one less sugar, and a paneer sandwich, no onion",
  "हिंदी": "दो कोल्ड कॉफ़ी, एक में चीनी कम, और एक पनीर सैंडविच बिना प्याज़",
  Hinglish: "2 cold coffee, ek mein sugar kam, aur ek paneer sandwich bina onion",
} as const;

function LanguageDemo() {
  const [lang, setLang] = useState<keyof typeof LANGS>("Hinglish");
  return (
    <article className="lp-card" data-reveal>
      <p className="lp-eyebrow">Any language</p>
      <h3>Switch it. Same ticket.</h3>
      <div className="lp-seg" role="group" aria-label="Guest's language">
        {(Object.keys(LANGS) as (keyof typeof LANGS)[]).map((k) => (
          <button key={k} type="button" aria-pressed={k === lang} onClick={() => { tapSound("send"); setLang(k); }}>{k}</button>
        ))}
      </div>
      <div className="lp-mini lp-mini-flat" key={lang}><Msg from="guest">{LANGS[lang]}</Msg></div>
      <div className="lp-ticket is-on lp-ticket-static">
        <div className="lp-ticket-top"><b>T12 · #1042</b><span className="lp-status" data-s="NEW">NEW</span></div>
        <div className="lp-ticket-row"><span>2 × Cold Coffee</span><span>360</span></div>
        <div className="lp-ticket-note">1 × less sugar</div>
        <div className="lp-ticket-row"><span>1 × Paneer Sandwich</span><span>220</span></div>
        <div className="lp-ticket-note">no onion</div>
      </div>
    </article>
  );
}

const SPEED = 15; // demo clock: 1 real second = 15 seconds

function CallDemo() {
  const [started, setStarted] = useState<number | null>(null);
  const [handled, setHandled] = useState(false);
  const [now, setNow] = useState(0);
  const lastLevel = useRef(0);
  useEffect(() => {
    if (started === null || handled) return;
    const id = window.setInterval(() => setNow(Date.now()), 200);
    return () => window.clearInterval(id);
  }, [started, handled]);
  const secs = started === null ? 0 : Math.min(599, Math.floor(((now || started) - started) / 1000 * SPEED));
  const level = secs >= 180 ? 2 : secs >= 60 ? 1 : 0;
  useEffect(() => {
    if (started === null || handled) return;
    if (level > lastLevel.current) play("alert");
    lastLevel.current = level;
  }, [level, started, handled]);
  const who = handled ? "Handled by Ravi" : ["Shift manager notified", "60 s · sent to managers", "Still waiting · owner alerted"][level];
  return (
    <article className="lp-card" data-reveal>
      <p className="lp-eyebrow">Call waiter</p>
      <h3>Tap it. Watch it escalate.</h3>
      {started === null || handled ? (
        <button type="button" className="lp-call" onClick={() => { tapSound("bell"); lastLevel.current = 0; setStarted(Date.now()); setNow(Date.now()); setHandled(false); }}>
          <BellRing size={18} /> {handled ? "Call again" : "Call waiter"}
        </button>
      ) : (
        <button type="button" className="lp-call lp-call-ok" onClick={() => { tapSound("success"); setHandled(true); }}>I'm on it</button>
      )}
      <div className={`lp-alert ${started !== null && !handled ? "is-live" : ""} ${handled ? "is-done" : ""}`}>
        <span className="lp-alert-t">T4</span>
        <div><b>{started === null ? "No one waiting" : "Call waiter"}</b><small>{started === null ? "Tap the button above." : who}</small></div>
        <span className="lp-alert-clock">{started === null ? "–" : `${Math.floor(secs / 60)}:${String(secs % 60).padStart(2, "0")}`}</span>
      </div>
      <div className="lp-ladder">
        {["Staff", "Managers · 60 s", "Owner · +2 min"].map((l, i) => (
          <span key={l} className={started !== null && !handled && level >= i ? "is-on" : ""}>{l}</span>
        ))}
      </div>
      <p className="lp-fine">Demo clock runs 15× faster.</p>
    </article>
  );
}

/* ---------- Owner results ---------- */

function OwnerResults() {
  const [ref, seen] = useInView<HTMLDivElement>(0.35);
  const rows: [string, number][] = [["Sent", 148], ["Read", 117], ["Came back", 31], ["Redeemed", 26]];
  return (
    <section className="lp-sec">
      <div className="lp-wrap lp-owner">
        <div className="lp-owner-text" data-reveal>
          <p className="lp-eyebrow">For owners</p>
          <h2>See what every offer earned.</h2>
          <p className="lp-lede">One code per guest. Every redemption counted.</p>
        </div>
        <div className={`lp-funnel ${seen ? "is-in" : ""}`} ref={ref} aria-label="Example campaign results">
          <div className="lp-funnel-head"><b>Weekend comeback</b><Tag>Example</Tag></div>
          {rows.map(([l, n]) => (
            <div className={`lp-fn ${l === "Redeemed" ? "is-hl" : ""}`} key={l}>
              <span>{l}</span><span className="lp-fn-bar"><i style={{ width: `${(n / 148) * 100}%` }} /></span><Count to={n} run={seen} />
            </div>
          ))}
          <div className="lp-stats">
            <div><b><Count to={18460} prefix="₹" run={seen} /></b><span>spent by returning guests</span></div>
            <div><b><Count to={31} run={seen} /></b><span>extra visits</span></div>
          </div>
        </div>
      </div>
    </section>
  );
}

/* ---------- Benefits, stories, contact ---------- */

const BENEFITS: { icon: typeof ChefHat; title: string }[] = [
  { icon: ChefHat, title: "Orders right first time" },
  { icon: Users, title: "More tables, same team" },
  { icon: Smartphone, title: "No app, no hardware" },
  { icon: Languages, title: "English, Hindi, Hinglish" },
  { icon: HeartHandshake, title: "A person in one tap" },
  { icon: ShieldCheck, title: "Prices only from your menu" },
  { icon: BadgePercent, title: "Offers only with consent" },
  { icon: TrendingUp, title: "Proof of what works" },
];

// Illustrative until pilot cafés give real quotes; the section says so on the page.
const TESTIMONIALS: { quote: string; who: string }[] = [
  { quote: "Saturday orders reach the kitchen exactly as typed.", who: "Café owner · Bengaluru" },
  { quote: "The assistant handles “what's spicy?”. We handle the tables.", who: "Floor manager · Pune" },
  { quote: "I know who came back, and what they spent.", who: "Café owner · Delhi" },
];

function Stories() {
  const strip = useRef<HTMLDivElement>(null);
  const at = useCarouselIndex(strip);
  return (
    <section className="lp-sec lp-sec-gray" id="stories">
      <div className="lp-wrap">
        <header className="lp-head" data-reveal>
          <p className="lp-eyebrow">Stories</p>
          <h2>What cafés say.</h2>
          <p className="lp-fine">Illustrative quotes.</p>
        </header>
        <div className="lp-quotes lp-swipe" ref={strip}>
          {TESTIMONIALS.map((t) => (
            <figure className="lp-quote" key={t.quote}>
              <Quote size={20} />
              <blockquote>{t.quote}</blockquote>
              <figcaption>{t.who}</figcaption>
            </figure>
          ))}
        </div>
        <div className="lp-swipe-meta"><Dots n={TESTIMONIALS.length} i={at} /><span>Swipe</span></div>
      </div>
    </section>
  );
}

/* ---------- Page ---------- */

export function Landing({ loginHref }: { loginHref?: string | null }) {
  const root = useRef<HTMLDivElement>(null);
  useReveal(root);

  useEffect(() => {
    const prev = document.title;
    document.title = `${BRAND} · Hospitality, on WhatsApp`;
    if (!document.querySelector(`link[href="${FONT_HREF}"]`)) {
      document.head.appendChild(Object.assign(document.createElement("link"), { rel: "stylesheet", href: FONT_HREF }));
    }
    return () => { document.title = prev; };
  }, []);

  return (
    <div className="lp" ref={root}>
      <Nav loginHref={loginHref} />

      <main id="top">
        <section className="lp-hero">
          <div className="lp-wrap lp-hero-in">
            <p className="lp-pill"><span className="lp-dot" />Restaurant automation on WhatsApp</p>
            <h1>Every table served.<br /><span>Every guest back.</span></h1>
            <p className="lp-lede lp-hero-lede">Turn every visitor into a valued guest.</p>
            <div className="lp-cta-row">
              <a href={CONTACT.whatsapp} target="_blank" rel="noopener noreferrer" className="lp-btn lp-btn-accent">Book a demo <ArrowRight size={18} /></a>
              <a href="#how" className="lp-btn lp-btn-ghost">See how it works</a>
            </div>
            <ul className="lp-features" aria-label="What it does">
              <li><span><MessageCircle size={22} /></span>Order instantly</li>
              <li><span><ConciergeBell size={22} /></span>Call a waiter</li>
              <li><span><TrendingUp size={22} /></span>Bring guests back</li>
            </ul>
          </div>
          <HeroPhone />
        </section>

        <Statement />
        <Problems />
        <HowItWorks />

        <section className="lp-sec" id="demo">
          <div className="lp-wrap">
            <header className="lp-head" data-reveal>
              <p className="lp-eyebrow">Try it</p>
              <h2>Go on, tap around.</h2>
              <p className="lp-fine">Best with sound on.</p>
            </header>
            <div className="lp-bento">
              <RecommendDemo />
              <LanguageDemo />
              <CallDemo />
            </div>
          </div>
        </section>

        <section className="lp-sec lp-sec-gray" id="benefits">
          <div className="lp-wrap">
            <header className="lp-head" data-reveal>
              <p className="lp-eyebrow">Benefits</p>
              <h2>Less running. More regulars.</h2>
            </header>
            <div className="lp-benefits">
              {BENEFITS.map(({ icon: Icon, title }, i) => (
                <div className="lp-benefit" key={title} data-reveal style={{ transitionDelay: `${(i % 4) * 60}ms` }}>
                  <span className="lp-benefit-icon"><Icon size={22} strokeWidth={1.75} /></span>
                  <h3>{title}</h3>
                </div>
              ))}
            </div>
          </div>
        </section>

        <OwnerResults />
        <Stories />

        <section className="lp-cta" id="contact">
          <div className="lp-wrap" data-reveal>
            <div className="lp-cta-box">
              <Cloche className="lp-cta-art" />
              <Logo size={48} />
              <h2>Hospitality,<br /><span>on WhatsApp.</span></h2>
              <p className="lp-lede">See {BRAND} on your own menu.</p>
              <div className="lp-cta-row">
                <a href={CONTACT.whatsapp} target="_blank" rel="noopener noreferrer" className="lp-btn lp-btn-accent"><MessageCircle size={18} /> WhatsApp us</a>
                <a href={CONTACT.tel} className="lp-btn lp-btn-outline-white"><Phone size={17} /> {CONTACT.phone}</a>
                <a href={`mailto:${CONTACT.email}`} className="lp-btn lp-btn-outline-white"><Mail size={17} /> {CONTACT.email}</a>
              </div>
              <div className="lp-cta-foot">
                <LoginLink href={loginHref}>Café staff? Log in</LoginLink>
                <a href="privacy.html">Privacy</a>
                <a href="terms.html">Terms</a>
                <span>© {BRAND}</span>
              </div>
            </div>
          </div>
        </section>
      </main>

      <MobileBar />
    </div>
  );
}
