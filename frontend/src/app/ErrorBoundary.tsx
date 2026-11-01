// Render errors never blank the console (docs/console.md#shell). The root boundary offers a reload; the screen
// boundary keeps the shell, nav and alerts running while one screen is down, and resets on navigation.
import { Component, type ErrorInfo, type ReactNode } from "react";

const RELOADED_AT = "restro.chunkReloadAt";

/** A lazy screen's file is gone (a deploy replaced it) or didn't download: a fresh page load fixes it. */
export function isChunkError(e: unknown): boolean {
  const msg = e instanceof Error ? e.message : String(e);
  return /dynamically imported module|Importing a module script failed|error loading dynamically imported|Unable to preload CSS/i.test(msg);
}

/** Reloads once per minute at most, so a real outage can't loop. Returns false when it didn't reload. */
export function reloadForNewVersion(): boolean {
  try {
    const last = Number(sessionStorage.getItem(RELOADED_AT) ?? 0);
    if (Date.now() - last < 60_000) return false;
    sessionStorage.setItem(RELOADED_AT, String(Date.now()));
  } catch {
    return false;
  }
  window.location.reload();
  return true;
}

interface Props {
  children: ReactNode;
  /** "app": whole-page fallback; "screen": fallback inside the shell's content area. */
  scope: "app" | "screen";
}

export class ErrorBoundary extends Component<Props, { error: Error | null }> {
  state: { error: Error | null } = { error: null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error(`console ${this.props.scope} error`, error, info.componentStack);
    if (isChunkError(error)) reloadForNewVersion();
  }

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;
    const reload = () => window.location.reload();
    if (this.props.scope === "screen") {
      return (
        <div className="empty" role="alert">
          <p><strong>This screen hit a problem and stopped.</strong></p>
          <p className="muted">Alerts and the other screens still work.</p>
          <div className="row center">
            <button className="btn small" onClick={() => this.setState({ error: null })}>Try again</button>
            <button className="btn small ghost" onClick={reload}>Reload</button>
          </div>
        </div>
      );
    }
    return (
      <div className="gate">
        <div className="gate-card" role="alert">
          <h1>Something went wrong</h1>
          <p className="muted">The console hit an error. Reloading brings back live orders and alerts.</p>
          <button className="btn primary big" onClick={reload}>Reload</button>
        </div>
      </div>
    );
  }
}
