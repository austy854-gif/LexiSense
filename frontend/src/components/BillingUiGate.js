import { Navigate } from 'react-router-dom';
import { Loader2 } from 'lucide-react';
import { useBillingUiEnabled } from '../lib/billingConfig';

/** Renders billing UI only when GET /billing/config says BILLING_UI_ENABLED is on. */
export function BillingUiGate({ children }) {
  const enabled = useBillingUiEnabled();
  if (enabled === null) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-background">
        <Loader2 className="h-8 w-8 animate-spin text-primary" />
      </div>
    );
  }
  return enabled ? children : <Navigate to="/" replace />;
}
