import { lazy, Suspense } from "react";
import { Route, Routes } from "react-router-dom";

import { ProtectedRoute } from "./auth/AuthContext";
import { AppLayout } from "./components/Layout";
import { Loading } from "./components/ui";
import { ForgotPasswordPage, LoginPage, RegisterPage, ResetPasswordPage } from "./pages/Auth";
import { LandingPage } from "./pages/Landing";

const DashboardPage = lazy(() => import("./pages/Dashboard"));
const FundsPage = lazy(() => import("./pages/Funds"));
const FundDetailPage = lazy(() => import("./pages/FundDetail"));
const FundComparePage = lazy(() => import("./pages/FundCompare"));
const RiskPage = lazy(() => import("./pages/Risk"));
const PortfoliosPage = lazy(() => import("./pages/Portfolios"));
const PortfolioDetailPage = lazy(() => import("./pages/PortfolioDetail"));
const AssistantPage = lazy(() => import("./pages/Assistant"));
const DocumentsPage = lazy(() => import("./pages/Documents"));
const DocumentDetailPage = lazy(() => import("./pages/DocumentDetail"));
const MlPage = lazy(() => import("./pages/Ml"));
const WhatIfPage = lazy(() => import("./pages/WhatIf"));
const SettingsPage = lazy(() => import("./pages/Settings"));
const AdminPage = lazy(() => import("./pages/Admin"));

function NotFound() {
  return (
    <div className="py-16">
      <h1>Page not found</h1>
      <p className="mt-2 text-muted">Check the address or use the navigation.</p>
    </div>
  );
}

export default function App() {
  return (
    <Suspense fallback={<Loading />}>
      <Routes>
        <Route path="/" element={<LandingPage />} />
        <Route path="/login" element={<LoginPage />} />
        <Route path="/register" element={<RegisterPage />} />
        <Route path="/forgot-password" element={<ForgotPasswordPage />} />
        <Route path="/reset-password" element={<ResetPasswordPage />} />
        <Route
          element={
            <ProtectedRoute>
              <AppLayout />
            </ProtectedRoute>
          }
        >
          <Route path="/dashboard" element={<DashboardPage />} />
          <Route path="/funds" element={<FundsPage />} />
          <Route path="/funds/compare" element={<FundComparePage />} />
          <Route path="/funds/:fundId" element={<FundDetailPage />} />
          <Route path="/risk" element={<RiskPage />} />
          <Route path="/portfolios" element={<PortfoliosPage />} />
          <Route path="/portfolios/:portfolioId" element={<PortfolioDetailPage />} />
          <Route path="/assistant" element={<AssistantPage />} />
          <Route path="/documents" element={<DocumentsPage />} />
          <Route path="/documents/:documentId" element={<DocumentDetailPage />} />
          <Route path="/ml" element={<MlPage />} />
          <Route path="/what-if" element={<WhatIfPage />} />
          <Route path="/settings" element={<SettingsPage />} />
          <Route
            path="/admin"
            element={
              <ProtectedRoute admin>
                <AdminPage />
              </ProtectedRoute>
            }
          />
        </Route>
        <Route path="*" element={<NotFound />} />
      </Routes>
    </Suspense>
  );
}
