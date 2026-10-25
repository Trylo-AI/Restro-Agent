import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Navigate, Route, Routes, useOutletContext } from "react-router-dom";

import { CallsScreen } from "../features/calls/CallsScreen";
import { ChatsScreen } from "../features/chats/ChatsScreen";
import { MenuScreen } from "../features/menu/MenuScreen";
import { OpsFailures } from "../features/ops/Failures";
import { OpsInsights, OpsLogs } from "../features/ops/Insights";
import { OpsLatency } from "../features/ops/Latency";
import { OpsLive } from "../features/ops/Live";
import { OpsLayout } from "../features/ops/OpsLayout";
import { OpsOverview } from "../features/ops/Overview";
import { OpsRuns, OpsRunTrace } from "../features/ops/Runs";
import { OpsDependencies, OpsEfficiency, OpsEndpoints } from "../features/ops/Tables";
import { OrdersScreen } from "../features/orders/OrdersScreen";
import { QrSheet, SetupScreen } from "../features/setup/SetupScreen";
import { SessionProvider, useSession } from "../lib/session";
import { AlertProvider } from "./alerts";
import { Login } from "./Login";
import { Shell } from "./Shell";
import { ToastProvider } from "./toasts";
import "../styles/app.css";
import "../styles/ops.css";

const queryClient = new QueryClient({ defaultOptions: { queries: { retry: 2 } } });

export function useOffline(): boolean {
  return useOutletContext<{ offline: boolean } | undefined>()?.offline ?? false;
}

function Gate() {
  const { ready, session, cafeId } = useSession();
  if (!ready) return <div className="gate"><div className="muted">Loading…</div></div>;
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
          <Route path="*" element={<Navigate to="/orders" replace />} />
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
