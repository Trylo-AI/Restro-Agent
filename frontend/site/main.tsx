// The public website: the landing page on its own, without the console (no Supabase, no login, no API).
// Built by `npm run build:landing` into dist-landing/ and deployed to GitHub Pages (.github/workflows/deploy-landing.yml).
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { Landing } from "../src/features/landing/Landing";
import "../src/styles/tokens.css";

// "Log in" points at the deployed console; with no console address set, the link is hidden.
const consoleUrl = String(import.meta.env.VITE_CONSOLE_URL ?? "").trim().replace(/\/+$/, "");

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <Landing loginHref={consoleUrl ? `${consoleUrl}/login` : null} />
  </StrictMode>,
);
