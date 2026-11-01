import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Navigate, Route, Routes, useOutletContext } from "react-router-dom";

import { lazy, type ComponentType } from "react";

import { CallsScreen } from "../features/calls/CallsScreen";
import { ChatsScreen } from "../features/chats/ChatsScreen";
import { MenuScreen } from "../features/menu/MenuScreen";
import { OrdersScreen } from "../features/orders/OrdersScreen";
import { TodayScreen } from "../features/today/TodayScreen";
import { SessionProvider, useSession } from "../lib/session";
import { AlertProvider } from "./alerts";
import { Login } from "./Login";
import { Shell } from "./Shell";
import { ToastProvider } from "./toasts";
import "../styles/app.css";
import "../styles/ops.css";

// Service screens and Today (the owner's landing page) load with the app; owner and platform screens load on first
// visit, so a staff phone downloads less before Live orders shows. The shell's Suspense covers the wait.
function screen<M, K extends keyof M>(load: () => Promise<M>, name: K) {
  return lazy(() => load().then((m) => ({ default: m[name] as ComponentType })));
}
const ops = () => import("../features/ops");
const reports = () => import("../features/reports/ReportsScreen");
const setup = () => import("../features/setup/SetupScreen");
const CampaignsScreen = screen(() => import("../features/campaigns/CampaignsScreen"), "CampaignsScreen");
const CustomersScreen = screen(() => import("../features/customers/CustomersScreen"), "CustomersScreen");
const AnalyticsScreen = screen(reports, "AnalyticsScreen");
const ServiceReportScreen = screen(reports, "ServiceReportScreen");
const QrSheet = screen(setup, "QrSheet");
const SetupScreen = screen(setup, "SetupScreen");
const OpsLayout = screen(ops, "OpsLayout");
const OpsOverview = screen(ops, "OpsOverview");
const OpsLive = screen(ops, "OpsLive");
const OpsRuns = screen(ops, "OpsRuns");
const OpsRunTrace = screen(ops, "OpsRunTrace");
const OpsFailures = screen(ops, "OpsFailures");
const OpsLatency = screen(ops, "OpsLatency");
const OpsEndpoints = screen(ops, "OpsEndpoints");
const OpsDependencies = screen(ops, "OpsDependencies");
const OpsEfficiency = screen(ops, "OpsEfficiency");
const OpsInsights = screen(ops, "OpsInsights");
const OpsLogs = screen(ops, "OpsLogs");

const queryClient = new QueryClient({ defaultOptions: { queries: { retry: 2 } } });

export function useOffline(): boolean {
  return useOutletContext<{ offline: boolean } | undefined>()?.offline ?? false;
}

/** Owners land on Today; everyone else on Live orders (docs/console.md#today). */
function Home() {
  const { role } = useSession();
  return <Navigate to={role === "owner" ? "/today" : "/orders"} replace />;
}

function Gate() {
  const { ready, loadError, retry, session, cafeId } = useSession();
  if (!ready) return <div className="gate"><div className="muted">Loading…</div></div>;
  if (!session && !loadError) return <Login />;
  if (!cafeId && loadError) {
    return (
      <div className="gate">
        <div className="gate-card" role="alert">
          <h1>Can't reach Restro</h1>
          <p className="muted">Check the internet connection. Retrying every few seconds.</p>
          <button className="btn primary big" onClick={retry}>Retry now</button>
        </div>
      </div>
    );
  }
  if (!session) return <Login />;
  if (!cafeId) {
    return (
      <div className="gate">
        <div className="gate-card">
          <h1>No access to any café</h1>
          <p className="muted">Ask the café owner to invite you.</p>
        </div>
      </div>
    );
  }
  return (
    <AlertProvider>
      <Routes>
        <Route element={<Shell />}>
          <Route path="/orders" element={<OrdersScreen />} />
          <Route path="/calls" element={<CallsScreen />} />
          <Route path="/chats/:conversationId?" element={<ChatsScreen />} />
          <Route path="/menu" element={<MenuScreen />} />
          <Route path="/service-report" element={<ServiceReportScreen />} />
          <Route path="/analytics" element={<AnalyticsScreen />} />
          <Route path="/customers/:customerId?" element={<CustomersScreen />} />
          <Route path="/campaigns/:campaignId?" element={<CampaignsScreen />} />
          <Route path="/setup/qr-sheet" element={<QrSheet />} />
          <Route path="/setup/:tab?" element={<SetupScreen />} />
          <Route path="/platform/ops" element={<OpsLayout />}>
            <Route index element={<Navigate to="overview" replace />} />
            <Route path="overview" element={<OpsOverview />} />
            <Route path="live" element={<OpsLive />} />
            <Route path="runs" element={<OpsRuns />} />
            <Route path="runs/:runId" element={<OpsRunTrace />} />
            <Route path="failures" element={<OpsFailures />} />
            <Route path="latency" element={<OpsLatency />} />
            <Route path="endpoints" element={<OpsEndpoints />} />
            <Route path="dependencies" element={<OpsDependencies />} />
            <Route path="efficiency" element={<OpsEfficiency />} />
            <Route path="insights" element={<OpsInsights />} />
            <Route path="logs" element={<OpsLogs />} />
          </Route>
          <Route path="/today" element={<TodayScreen />} />
          <Route path="*" element={<Home />} />
        </Route>
      </Routes>
    </AlertProvider>
  );
}

export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <ToastProvider>
        <SessionProvider>
          <BrowserRouter>
            <Gate />
          </BrowserRouter>
        </SessionProvider>
      </ToastProvider>
    </QueryClientProvider>
  );
}
