import { useEffect, useState } from 'react';
import { billingAPI } from '../api';

// BILLING_UI_ENABLED is decided by the backend at runtime (GET /billing/config),
// so it can be flipped without rebuilding the frontend. Until the answer
// arrives -- and if the request fails -- billing UI stays hidden: off is the
// safe default while pricing isn't final.
let pending = null;

export function loadBillingUiEnabled() {
  if (!pending) {
    pending = billingAPI
      .getConfig()
      .then((res) => res?.data?.billingUiEnabled === true)
      .catch(() => {
        pending = null; // retry on the next mount
        return false;
      });
  }
  return pending;
}

export function resetBillingConfigCache() {
  pending = null;
}

/** `null` while loading, then `true` / `false`. */
export function useBillingUiEnabled() {
  const [enabled, setEnabled] = useState(null);
  useEffect(() => {
    let live = true;
    loadBillingUiEnabled().then((value) => {
      if (live) setEnabled(value);
    });
    return () => {
      live = false;
    };
  }, []);
  return enabled;
}

/** Nav entries that belong to the billing UI (hidden unless the flag is on). */
export const BILLING_PATHS = ['/billing'];

export function isBillingPath(path) {
  return BILLING_PATHS.includes(path);
}
