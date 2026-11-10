import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { Toaster } from 'sonner';
import { AuthProvider } from './contexts/AuthContext';
import { ThemeProvider } from './contexts/ThemeContext';
import { ProtectedRoute } from './components/ProtectedRoute';
import { HomeRoute } from './components/HomeRoute';
import { RouteHead } from './components/RouteHead';
import { HostAuthRedirect } from './components/HostAuthRedirect';
import { RouteErrorBoundary } from './components/SectionErrorBoundary';

import LandingPage from './pages/LandingPage';
import LoginPage from './pages/LoginPage';
import RegisterPage from './pages/RegisterPage';
import ForgotPasswordPage from './pages/ForgotPasswordPage';
import ResetPasswordPage from './pages/ResetPasswordPage';
import DashboardPage from './pages/DashboardPage';
import ContractsPage from './pages/ContractsPage';
import ContractDetailPage from './pages/ContractDetailPage';
import TeamPage from './pages/TeamPage';
import AlertsPage from './pages/AlertsPage';
import AnalyticsPage from './pages/AnalyticsPage';
import TemplatesPage from './pages/TemplatesPage';
import AuditLogPage from './pages/AuditLogPage';
// Agentic subsystem is stripped for launch. Backend code kept, UI removed.
// import AgenticDashboardPage from './pages/AgenticDashboardPage';
import BillingPage from './pages/BillingPage';
import { BillingUiGate } from './components/BillingUiGate';
import ApprovalRespondPage from './pages/ApprovalRespondPage';
import AcceptInvitePage from './pages/AcceptInvitePage';

import './App.css';

function App() {
  return (
    <ThemeProvider>
      <AuthProvider>
        <BrowserRouter>
          <Toaster position="top-right" richColors closeButton />
          {/* Canonical on '/', robots noindex everywhere else */}
          <RouteHead />
          {/* LX-ENG-KT-01: a page that throws is contained here, not app-wide */}
          <RouteErrorBoundary>
          <Routes>
            {/* Public routes */}
            <Route path="/login" element={<HostAuthRedirect><LoginPage /></HostAuthRedirect>} />
            <Route path="/register" element={<HostAuthRedirect><RegisterPage /></HostAuthRedirect>} />
            <Route path="/signup" element={<Navigate to="/register" replace />} />
            <Route path="/forgot-password" element={<HostAuthRedirect><ForgotPasswordPage /></HostAuthRedirect>} />
            <Route path="/reset-password" element={<HostAuthRedirect><ResetPasswordPage /></HostAuthRedirect>} />
            {/* Approval email links (token is the credential; no login needed) */}
            <Route path="/approvals/respond" element={<ApprovalRespondPage />} />
            {/* Team invite email links (?token=...); works signed in or out */}
            <Route path="/accept-invite" element={<HostAuthRedirect><AcceptInvitePage /></HostAuthRedirect>} />

            {/* Home: marketing landing page for logged-out visitors,
                the dashboard for signed-in users (same URL as before). */}
            <Route
              path="/"
              element={<HomeRoute landing={<LandingPage />} dashboard={<DashboardPage />} />}
            />

            {/* Protected routes */}
            <Route
              path="/contracts"
              element={
                <ProtectedRoute>
                  <ContractsPage />
                </ProtectedRoute>
              }
            />
            <Route
              path="/contracts/:id"
              element={
                <ProtectedRoute>
                  <ContractDetailPage />
                </ProtectedRoute>
              }
            />
            <Route
              path="/templates"
              element={
                <ProtectedRoute>
                  <TemplatesPage />
                </ProtectedRoute>
              }
            />
            <Route
              path="/analytics"
              element={
                <ProtectedRoute>
                  <AnalyticsPage />
                </ProtectedRoute>
              }
            />
            <Route
              path="/alerts"
              element={
                <ProtectedRoute>
                  <AlertsPage />
                </ProtectedRoute>
              }
            />
            <Route
              path="/team"
              element={
                <ProtectedRoute>
                  <TeamPage />
                </ProtectedRoute>
              }
            />
            <Route
              path="/audit"
              element={
                <ProtectedRoute allowedRoles={['admin', 'manager']}>
                  <AuditLogPage />
                </ProtectedRoute>
              }
            />
            <Route
              path="/billing"
              element={
                <ProtectedRoute allowedRoles={['admin']}>
                  <BillingUiGate>
                    <BillingPage />
                  </BillingUiGate>
                </ProtectedRoute>
              }
            />

            {/* Fallback */}
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
          </RouteErrorBoundary>
        </BrowserRouter>
      </AuthProvider>
    </ThemeProvider>
  );
}

export default App;
