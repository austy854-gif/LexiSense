import { useEffect } from 'react';
import { Loader2 } from 'lucide-react';
import { authRedirectTarget } from '../lib/hostRouting';

/**
 * Wraps /login and /register. On the landing host it does a full navigation
 * to the same path on the app host (auth state lives there); elsewhere it
 * renders the page unchanged.
 */
export function HostAuthRedirect({ children }) {
  const target = authRedirectTarget();
  useEffect(() => {
    if (target) window.location.replace(target);
  }, [target]);
  if (!target) return children;
  return (
    <div className="min-h-screen flex items-center justify-center bg-background">
      <Loader2 className="h-8 w-8 animate-spin text-primary" aria-label="Redirecting" />
    </div>
  );
}
