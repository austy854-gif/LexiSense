import { useAuth } from '../contexts/AuthContext';
import { Loader2 } from 'lucide-react';

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

  return user ? dashboard : landing;
}
