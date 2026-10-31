import { MutationCache, QueryCache, QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode, useState } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Link, Route, Routes } from "react-router";
import AccessGate from "./components/AccessGate";
import Layout from "./components/Layout";
import { ToastProvider } from "./components/toast";
import { EmptyState } from "./components/ui";
import "./index.css";
import { ApiError, getAccessCode } from "./lib/api";
import ClaimPage from "./pages/ClaimPage";
import Metrics from "./pages/Metrics";
import NewResearch from "./pages/NewResearch";
import RunPage from "./pages/RunPage";
import Runs from "./pages/Runs";

function isAccessError(error: unknown) {
  return error instanceof ApiError && error.code === "access_code_required";
}

function NotFound() {
  return <EmptyState title="Page not found" action={<Link to="/" className="text-accent hover:underline">Start new research</Link>} />;
}

function App() {
  const [locked, setLocked] = useState(false);
  const [client] = useState(() => {
    const onError = (error: unknown) => { if (isAccessError(error)) setLocked(true); };
    return new QueryClient({
      queryCache: new QueryCache({ onError }),
      mutationCache: new MutationCache({ onError }),
      defaultOptions: {
        queries: {
          staleTime: 5_000,
          retry: (count, error) => !(error instanceof ApiError && error.status >= 400 && error.status < 500) && count < 2,
        },
      },
    });
  });

  if (locked) {
    return <AccessGate invalid={!!getAccessCode()} onUnlock={() => { setLocked(false); client.resetQueries(); }} />;
  }
  return (
    <QueryClientProvider client={client}>
      <ToastProvider>
        <BrowserRouter>
          <Routes>
            <Route element={<Layout />}>
              <Route index element={<NewResearch />} />
              <Route path="runs" element={<Runs />} />
              <Route path="runs/:id" element={<RunPage />} />
              <Route path="claims/:id" element={<ClaimPage />} />
              <Route path="metrics" element={<Metrics />} />
              <Route path="*" element={<NotFound />} />
            </Route>
          </Routes>
        </BrowserRouter>
      </ToastProvider>
    </QueryClientProvider>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
