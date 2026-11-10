import { useEffect } from 'react';
import { Navigate } from 'react-router';
import { useAuth } from '../contexts/AuthContext';
import { hasRole } from '../lib/apiHelpers.mjs';
import { authRedirectTarget } from '../lib/hostRouting';
import { Loader2 } from 'lucide-react';

export function ProtectedRoute({ children, allowedRoles }) {
  const { user, loading } = useAuth();
  // On the landing host, protected pages live on the app host: do a full
  // navigation there (same path + query + hash) before any auth checks.
  const target = authRedirectTarget();
  useEffect(() => {
    if (target) window.location.replace(target);
  }, [target]);

  if (target) {
    return (
      <div role="status" className="min-h-screen flex items-center justify-center bg-background">
        <Loader2 className="h-8 w-8 animate-spin text-primary" aria-hidden="true" />
        <span className="sr-only">Redirecting…</span>
      </div>
    );
  }

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
