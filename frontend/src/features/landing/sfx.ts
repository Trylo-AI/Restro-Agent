// Landing-page demo sounds, synthesised with WebAudio (no files). Sounds only play after the visitor taps something,
// because browsers block audio before a gesture. The mute choice is remembered in this browser when storage allows.
export type Sfx = "tap" | "send" | "receive" | "bell" | "alert" | "success";

const KEY = "mehmanawazi.sound";
let ctx: AudioContext | null = null;
let muted = (() => {
  try { return localStorage.getItem(KEY) === "off"; } catch { return false; }
})();

export const isMuted = () => muted;

export function setMuted(m: boolean): void {
  muted = m;
  try { localStorage.setItem(KEY, m ? "off" : "on"); } catch { /* private mode: the choice lasts this visit only */ }
}

/** Creates or resumes the audio context; call from a click handler. Resolves once audio can play (or can't). */
export async function wake(): Promise<void> {
  if (muted) return;
  try {
    const AC = window.AudioContext ?? (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
    if (!AC) return;
    ctx = ctx ?? new AC();
    if (ctx.state === "suspended") await ctx.resume();
  } catch {
    ctx = null;
  }
}

function tone(freq: number, at: number, dur: number, { gain = 0.12, type = "sine" as OscillatorType, to }: { gain?: number; type?: OscillatorType; to?: number } = {}) {
  if (!ctx) return;
  const t = ctx.currentTime + at;
  const osc = ctx.createOscillator();
  const g = ctx.createGain();
  osc.type = type;
  osc.frequency.setValueAtTime(freq, t);
  if (to) osc.frequency.exponentialRampToValueAtTime(to, t + dur);
  g.gain.setValueAtTime(0, t);
  g.gain.linearRampToValueAtTime(gain, t + 0.012);
  g.gain.exponentialRampToValueAtTime(0.0001, t + dur);
  osc.connect(g).connect(ctx.destination);
  osc.start(t);
  osc.stop(t + dur + 0.05);
}

/** Plays a sound if the visitor has interacted, sound is on and the context is running. */
export function play(s: Sfx): void {
  if (muted || !ctx || ctx.state !== "running") return;
  switch (s) {
    case "tap": tone(1200, 0, 0.05, { gain: 0.05, type: "triangle" }); break;
    case "send": tone(520, 0, 0.12, { gain: 0.07, to: 880 }); break;
    case "receive": tone(880, 0, 0.09, { gain: 0.08 }); tone(1320, 0.08, 0.14, { gain: 0.07 }); break;
    case "bell": tone(1568, 0, 0.9, { gain: 0.1 }); tone(2093, 0.01, 0.7, { gain: 0.05 }); break;
    case "alert": tone(660, 0, 0.14, { gain: 0.09, type: "square" }); tone(660, 0.2, 0.14, { gain: 0.09, type: "square" }); break;
    case "success": tone(784, 0, 0.12); tone(988, 0.1, 0.12); tone(1319, 0.2, 0.25); break;
  }
}

/** Wakes audio (this is a gesture) and plays at once. */
export function tapSound(s: Sfx = "tap"): void {
  void wake().then(() => play(s));
}
