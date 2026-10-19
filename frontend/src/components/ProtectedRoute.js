import { Navigate } from 'react-router-dom';
import { useAuth } from '../contexts/AuthContext';
import { hasRole } from '../lib/apiHelpers.mjs';
import { useBillingUiEnabled } from '../lib/billingConfig';
import { Loader2 } from 'lucide-react';

export function ProtectedRoute({ children, allowedRoles }) {
  const { user, loading } = useAuth();

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-background">
        <Loader2 className="h-8 w-8 animate-spin text-primary" />
      </div>
    );
  }

  if (!user) {
    return <Navigate to="/login" replace />;
  }

  if (allowedRoles && !hasRole(user.role, allowedRoles)) {
    return <Navigate to="/" replace />;
  }

  return children;
}

/** Renders children only while BILLING_UI_ENABLED is on; otherwise redirects home. */
export function BillingUiRoute({ children }) {
  const enabled = useBillingUiEnabled();
  if (enabled === null) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-background">
        <Loader2 className="h-8 w-8 animate-spin text-primary" />
      </div>
    );
  }
  if (!enabled) {
    return <Navigate to="/" replace />;
  }
  return children;
}
