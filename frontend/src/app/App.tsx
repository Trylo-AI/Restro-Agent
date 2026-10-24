import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Navigate, Route, Routes, useOutletContext } from "react-router-dom";

import { CallsScreen } from "../features/calls/CallsScreen";
import { ChatsScreen } from "../features/chats/ChatsScreen";
import { MenuScreen } from "../features/menu/MenuScreen";
import { OrdersScreen } from "../features/orders/OrdersScreen";
import { SessionProvider, useSession } from "../lib/session";
import { AlertProvider } from "./alerts";
import { Login } from "./Login";
import { Shell } from "./Shell";
import { ToastProvider } from "./toasts";
import "../styles/app.css";

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
