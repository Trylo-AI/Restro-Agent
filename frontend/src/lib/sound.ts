// Alert sounds synthesised with WebAudio (no asset files). Browsers need one user gesture to unlock audio:
// the alerts gate's button calls unlock().
import type { Sound } from "./urgency";

let ctx: AudioContext | null = null;
let unlocked = false;

export function soundEnabled(): boolean {
  return unlocked && ctx?.state === "running";
}

export async function unlock(): Promise<boolean> {
  try {
    ctx = ctx ?? new AudioContext();
    if (ctx.state !== "running") await ctx.resume();
    unlocked = ctx.state === "running";
    if (unlocked) play("chime");
    return unlocked;
  } catch {
    return false;
  }
}

function tone(freq: number, start: number, duration: number, gain = 0.2): void {
  if (!ctx) return;
  const osc = ctx.createOscillator();
  const g = ctx.createGain();
  osc.type = "sine";
  osc.frequency.value = freq;
  g.gain.setValueAtTime(0, ctx.currentTime + start);
  g.gain.linearRampToValueAtTime(gain, ctx.currentTime + start + 0.02);
  g.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + start + duration);
  osc.connect(g).connect(ctx.destination);
  osc.start(ctx.currentTime + start);
  osc.stop(ctx.currentTime + start + duration + 0.05);
}

export function play(sound: Sound): void {
  if (!ctx || ctx.state !== "running") return;
  if (sound === "chime") {
    tone(880, 0, 0.35, 0.15);
  } else if (sound === "two_note") {
    tone(660, 0, 0.25);
    tone(880, 0.28, 0.3);
  } else {
    for (let i = 0; i < 3; i++) {
      tone(988, i * 0.45, 0.2, 0.3);
      tone(740, i * 0.45 + 0.2, 0.2, 0.3);
    }
    if ("vibrate" in navigator) navigator.vibrate?.([300, 150, 300]);
  }
}

export async function notifyPermission(): Promise<NotificationPermission | "unsupported"> {
  if (!("Notification" in window)) return "unsupported";
  if (Notification.permission === "default") return Notification.requestPermission();
  return Notification.permission;
}

/** Ring events while the tab is hidden become browser notifications; critical ones stay until tapped. */
export function notify(title: string, body: string, critical: boolean): void {
  if (!("Notification" in window) || Notification.permission !== "granted" || document.visibilityState === "visible") return;
  const n = new Notification(title, { body, requireInteraction: critical, tag: title });
  n.onclick = () => {
    window.focus();
    n.close();
  };
}
