import { useAuth } from '../contexts/AuthContext';
import { Navigate } from 'react-router-dom';
import { Loader2 } from 'lucide-react';
import { getHostRole } from '../lib/hostRouting';

/**
 * "/" route: signed-in users get their dashboard (exactly as before), logged-out
 * visitors get the public landing page. Rendering in place instead of redirecting
 * keeps every existing navigate('/') (after login/register, role fallback in
 * ProtectedRoute, the catch-all route) working unchanged.
 */
export function HomeRoute({ landing, dashboard }) {
  const { user, loading } = useAuth();

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-background">
        <Loader2 className="h-8 w-8 animate-spin text-primary" aria-label="Loading" />
      </div>
    );
  }

  const role = getHostRole();
  // The landing host is marketing-only: a session left in this origin's
  // storage from before the domain split must not render the app here.
  if (role === 'landing') return landing;
  if (user) return dashboard;
  // On the dedicated app host there is no marketing page: send visitors to log in.
  if (role === 'app') return <Navigate to="/login" replace />;
  return landing;
}
