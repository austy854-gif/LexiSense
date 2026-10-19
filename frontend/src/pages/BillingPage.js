import { useState, useEffect } from 'react';
import { Layout } from '../components/Layout';
import { billingAPI } from '../api';
import { useAuth } from '../contexts/AuthContext';
import { Button } from '../components/ui/button';
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '../components/ui/card';
import { Badge } from '../components/ui/badge';
import { Skeleton } from '../components/ui/skeleton';
import {
  CreditCard,
  Crown,
  Clock,
  CheckCircle,
  XCircle,
  Loader2,
  ArrowRight,
  Lock,
  HelpCircle,
} from 'lucide-react';
import { toast } from 'sonner';
import {
  CTA_CHECKOUT,
  CTA_CONTACT,
  CTA_PORTAL,
  accessStateOf,
  formatPrice,
  getPlanCardState,
  getPrimaryAction,
  intervalLabel,
} from '../lib/billingCta';

const StatusBadge = ({ status }) => {
  const configs = {
    active: { color: 'bg-green-500/10 text-green-500', icon: CheckCircle, label: 'Active' },
    trialing: { color: 'bg-blue-500/10 text-blue-500', icon: Clock, label: 'Trialing' },
    past_due: { color: 'bg-orange-500/10 text-orange-500', icon: Clock, label: 'Past Due' },
    cancelled: { color: 'bg-gray-500/10 text-gray-500', icon: XCircle, label: 'Cancelled' },
    incomplete: { color: 'bg-yellow-500/10 text-yellow-500', icon: Clock, label: 'Incomplete' },
  };
  const config = configs[status] || configs.incomplete;
  const Icon = config.icon;
  return (
    <Badge variant="secondary" className={config.color}>
      <Icon className="h-3 w-3 mr-1" />
      {config.label}
    </Badge>
  );
};

const TierBadge = ({ tier }) => {
  const configs = {
    trial: { color: 'bg-gray-500/10 text-gray-500', icon: Clock, label: 'Free Trial' },
    team: { color: 'bg-purple-500/10 text-purple-500', icon: Crown, label: 'Team Plan' },
    cancelled: { color: 'bg-gray-500/10 text-gray-500', icon: XCircle, label: 'Cancelled' },
  };
  const config = configs[tier] || configs.trial;
  const Icon = config.icon;
  return (
    <Badge variant="outline" className={config.color}>
      <Icon className="h-3 w-3 mr-1" />
      {config.label}
    </Badge>
  );
};

function PlanPrice({ plan, interval }) {
  const price = plan.prices?.[interval] || (plan.price !== null && plan.price !== undefined
    ? { amount: plan.price, currency: plan.currency, interval: plan.interval }
    : null);
  if (!price) return null;
  return (
    <div className="flex items-baseline justify-center gap-1" data-testid={`plan-price-${plan.id}`}>
      <span className="text-4xl font-bold">{formatPrice(price.amount, price.currency)}</span>
      <span className="text-muted-foreground">/{intervalLabel(price.interval)}</span>
    </div>
  );
}

export default function BillingPage() {
  const { user } = useAuth();
  const [subscription, setSubscription] = useState(null);
  const [plans, setPlans] = useState(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(false);
  const [checkoutLoading, setCheckoutLoading] = useState(false);
  const [portalLoading, setPortalLoading] = useState(false);
  const [billingInterval, setBillingInterval] = useState('monthly');

  const isAdmin = user?.role === 'admin';

  useEffect(() => {
    fetchBillingData();
  }, []);

  const fetchBillingData = async () => {
    setLoading(true);
    setLoadError(false);
    try {
      const [subRes, plansRes] = await Promise.all([
        billingAPI.getSubscription(),
        billingAPI.getPlans(),
      ]);
      setSubscription(subRes.data);
      setPlans(plansRes.data.plans);
    } catch (error) {
      setLoadError(true);
      console.error(error);
    } finally {
      setLoading(false);
    }
  };

  const handleCheckout = async () => {
    if (!isAdmin) return;
    setCheckoutLoading(true);
    try {
      const successUrl = `${window.location.origin}/billing?success=true`;
      const cancelUrl = `${window.location.origin}/billing?canceled=true`;
      const res = await billingAPI.createCheckout({ planId: 'team', interval: billingInterval }, successUrl, cancelUrl);
      window.location.href = res.data.url;
    } catch (error) {
      toast.error(error.response?.data?.detail || 'Failed to start checkout');
    } finally {
      setCheckoutLoading(false);
    }
  };

  const handlePortal = async () => {
    if (!isAdmin) return;
    setPortalLoading(true);
    try {
      const returnUrl = `${window.location.origin}/billing`;
      const res = await billingAPI.createPortal(returnUrl);
      window.location.href = res.data.url;
    } catch (error) {
      toast.error(error.response?.data?.detail || 'Failed to open billing portal');
    } finally {
      setPortalLoading(false);
    }
  };

  const formatDate = (dateStr) => {
    if (!dateStr) return '-';
    return new Date(dateStr).toLocaleDateString();
  };

  const getTrialDaysLeft = (trialEndDate) => {
    if (!trialEndDate) return 0;
    const end = new Date(trialEndDate);
    const now = new Date();
    const diff = end - now;
    return Math.max(0, Math.ceil(diff / (1000 * 60 * 60 * 24)));
  };

  if (loading) {
    return (
      <Layout>
        <div className="space-y-6">
          <Skeleton className="h-8 w-48" />
          <div className="grid gap-6 md:grid-cols-2 lg:grid-cols-3">
            <Skeleton className="h-64" />
            <Skeleton className="h-64" />
          </div>
        </div>
      </Layout>
    );
  }

  if (loadError || !subscription) {
    return (
      <Layout>
        <Card data-testid="billing-load-error">
          <CardHeader>
            <CardTitle>We couldn't load your billing information</CardTitle>
            <CardDescription>Please check your connection and try again.</CardDescription>
          </CardHeader>
          <CardContent>
            <Button onClick={fetchBillingData} className="gap-2">
              <ArrowRight className="h-4 w-4" />
              Try again
            </Button>
          </CardContent>
        </Card>
      </Layout>
    );
  }

  const accessState = accessStateOf(subscription);
  const trialPlan = plans?.find((p) => p.id === 'trial');
  const teamPlan = plans?.find((p) => p.id === 'team');
  const intervals = teamPlan?.intervals || [];
  const canChooseInterval = intervals.length > 1;
  const checkoutAvailable = intervals.includes(billingInterval);
  const trialDaysLeft = subscription.trialEndDate ? getTrialDaysLeft(subscription.trialEndDate) : 0;
  const isTrial = accessState === 'trial';
  const isTeam = subscription.subscriptionTier === 'team';
  const trialLimit = subscription.trialContractsLimit ?? trialPlan?.uploadLimit ?? null;
  const trialUsed = subscription.trialContractsUsed ?? 0;
  const primary = getPrimaryAction(subscription, { isAdmin });

  const statusLine = {
    trial: `${trialDaysLeft} day${trialDaysLeft !== 1 ? 's' : ''} left in trial`,
    trial_expired: 'Trial ended',
    paid: subscription.currentPeriodEnd ? `Renews on ${formatDate(subscription.currentPeriodEnd)}` : 'Active subscription',
    past_due_grace: 'Payment failed',
    past_due: 'Payment failed: account is read-only',
    cancelled: 'No active subscription',
    inactive: 'Subscription not active',
    unverified_plan: 'Subscription needs attention',
  }[accessState] || 'No active subscription';

  const renderAction = (action, { testId, full = false } = {}) => {
    if (action.kind === CTA_CHECKOUT) {
      return (
        <Button
          onClick={handleCheckout}
          disabled={checkoutLoading || !checkoutAvailable}
          className={`gap-2${full ? ' w-full' : ''}`}
          data-testid={testId}
        >
          {checkoutLoading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Crown className="h-4 w-4" />}
          {action.label}
        </Button>
      );
    }
    if (action.kind === CTA_PORTAL) {
      return (
        <Button
          variant="outline"
          onClick={handlePortal}
          disabled={portalLoading}
          className={`gap-2${full ? ' w-full' : ''}`}
          data-testid={testId}
        >
          {portalLoading ? <Loader2 className="h-4 w-4 animate-spin" /> : <CreditCard className="h-4 w-4" />}
          {action.label}
        </Button>
      );
    }
    if (action.kind === CTA_CONTACT) {
      return (
        <p className="text-sm text-muted-foreground" data-testid={testId}>
          {action.description || 'Please contact support about your subscription.'}
        </p>
      );
    }
    return action.description ? (
      <p className="text-sm text-muted-foreground" data-testid={testId}>{action.description}</p>
    ) : null;
  };

  return (
    <Layout>
      <div className="space-y-6" data-testid="billing-page">
        {/* Header */}
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
          <div>
            <h1 className="text-3xl font-bold">Billing & Subscription</h1>
            <p className="text-muted-foreground mt-1">
              Manage your plan, payment method, and view billing history
            </p>
          </div>
        </div>

        {/* Current Subscription Status */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <CreditCard className="h-5 w-5" />
              Your subscription
            </CardTitle>
            <CardDescription>Your organization's current subscription status</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 p-4 bg-muted/50 rounded-lg">
              <div className="flex items-center gap-4">
                <div className="p-3 bg-background border rounded-lg">
                  {isTeam ? (
                    <Crown className="h-8 w-8 text-purple-500" />
                  ) : (
                    <Clock className="h-8 w-8 text-gray-500" />
                  )}
                </div>
                <div>
                  <div className="flex items-center gap-2">
                    <TierBadge tier={subscription.subscriptionTier} />
                    <StatusBadge status={subscription.billingStatus} />
                  </div>
                  <p className="text-sm text-muted-foreground mt-1" data-testid="billing-status-line">
                    {statusLine}
                  </p>
                </div>
              </div>

              {isTrial && trialLimit !== null && (
                <div className="text-right" data-testid="trial-usage">
                  <p className="text-sm text-muted-foreground">
                    {trialUsed} / {trialLimit} contracts used
                  </p>
                  <div className="w-48 h-2 bg-muted rounded-full overflow-hidden mt-1">
                    <div
                      className="h-full bg-primary transition-all"
                      style={{ width: `${trialLimit > 0 ? Math.min(100, (trialUsed / trialLimit) * 100) : 100}%` }}
                    />
                  </div>
                </div>
              )}

              {isTeam && subscription.cancelAtPeriodEnd && (
                <div className="flex items-center gap-2 text-orange-500">
                  <XCircle className="h-4 w-4" />
                  <span className="text-sm">Cancels at period end</span>
                </div>
              )}
            </div>

            {isTrial && trialDaysLeft > 0 && (
              <div className="p-4 border border-yellow-500/20 bg-yellow-500/5 rounded-lg">
                <div className="flex items-center gap-3">
                  <Clock className="h-5 w-5 text-yellow-500" />
                  <div>
                    <p className="font-medium text-yellow-800">Free Trial Active</p>
                    <p className="text-sm text-yellow-700">
                      Your trial ends in {trialDaysLeft} day{trialDaysLeft !== 1 ? 's' : ''}. Upgrade to Team plan for unlimited contracts and full features.
                    </p>
                  </div>
                </div>
              </div>
            )}

            {(accessState === 'past_due_grace' || accessState === 'past_due') && (
              <div className="p-4 border border-orange-500/20 bg-orange-500/5 rounded-lg" data-testid="past-due-notice">
                <div className="flex items-center gap-3">
                  <Lock className="h-5 w-5 text-orange-500" />
                  <p className="text-sm text-orange-700">
                    {accessState === 'past_due'
                      ? 'Your last payment failed, so your account is read-only. You can still view and download your contracts. Update your payment method to restore uploads, AI analysis, chat and invitations.'
                      : subscription.warning || 'Your last payment failed. Please update your payment method.'}
                  </p>
                </div>
              </div>
            )}

            {canChooseInterval && primary.kind === CTA_CHECKOUT && (
              <div className="flex gap-2" role="group" aria-label="Billing interval" data-testid="interval-toggle">
                {intervals.map((value) => (
                  <Button
                    key={value}
                    size="sm"
                    variant={billingInterval === value ? 'default' : 'outline'}
                    aria-pressed={billingInterval === value}
                    onClick={() => setBillingInterval(value)}
                  >
                    {value === 'annual' ? 'Billed annually' : 'Billed monthly'}
                  </Button>
                ))}
              </div>
            )}

            <div className="flex flex-wrap gap-3 pt-2">
              {renderAction(primary, { testId: 'billing-primary-action' })}
            </div>
          </CardContent>
        </Card>

        {/* Pricing Plans */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Crown className="h-5 w-5" />
              Available Plans
            </CardTitle>
            <CardDescription>Choose the plan that fits your team</CardDescription>
          </CardHeader>
          <CardContent>
            <div className="grid gap-6 md:grid-cols-2 lg:grid-cols-3">
              {plans?.map((plan) => {
                const card = getPlanCardState(plan, subscription, { isAdmin });
                if (!card.show) return null;
                const isCurrent = card.label && card.label.startsWith('Current plan');
                return (
                  <div
                    key={plan.id}
                    data-testid={`plan-card-${plan.id}`}
                    className={`relative p-6 border rounded-xl ${
                      plan.id === 'team' ? 'border-primary/30 bg-primary/5' : 'border-muted'
                    } ${isCurrent ? 'ring-2 ring-primary' : ''}`}
                  >
                    {plan.id === 'team' && (
                      <div className="absolute -top-3 left-1/2 -translate-x-1/2">
                        <Badge className="bg-primary text-primary-foreground px-3 py-1">
                          Most Popular
                        </Badge>
                      </div>
                    )}

                    <div className="text-center mb-6">
                      <PlanPrice plan={plan} interval={plan.id === 'team' ? billingInterval : 'monthly'} />
                      <p className="text-sm text-muted-foreground mt-1">{plan.name}</p>
                    </div>

                    <ul className="space-y-3 mb-6">
                      {plan.features.map((feature, idx) => (
                        <li key={idx} className="flex items-start gap-2 text-sm">
                          <CheckCircle className="h-4 w-4 text-green-500 shrink-0 mt-0.5" />
                          <span>{feature}</span>
                        </li>
                      ))}
                    </ul>

                    {card.action
                      ? renderAction(card.action, { testId: `plan-action-${plan.id}`, full: true })
                      : card.label && (
                        <div
                          className="flex items-center justify-center gap-2 text-sm font-medium text-muted-foreground"
                          data-testid={`plan-label-${plan.id}`}
                        >
                          {isCurrent ? <CheckCircle className="h-4 w-4" /> : <Clock className="h-4 w-4" />}
                          {card.label}
                        </div>
                      )}
                  </div>
                );
              })}
            </div>
          </CardContent>
        </Card>

        {/* Feature Comparison */}
        <Card>
          <CardHeader>
            <CardTitle>Feature Comparison</CardTitle>
            <CardDescription>See what's included in each tier</CardDescription>
          </CardHeader>
          <CardContent>
            <div className="overflow-x-auto">
              <table className="w-full text-left">
                <thead>
                  <tr className="border-b text-sm text-muted-foreground">
                    <th className="pb-3 font-medium w-64">Feature</th>
                    <th className="pb-3 font-medium text-center">Free Trial</th>
                    <th className="pb-3 font-medium text-center">Team Plan</th>
                  </tr>
                </thead>
                <tbody className="text-sm divide-y">
                  <tr>
                    <td className="py-3 font-medium">Contract uploads</td>
                    <td className="py-3 text-center text-muted-foreground">
                      {trialPlan?.uploadLimit != null ? `${trialPlan.uploadLimit} per trial` : 'Limited'}
                    </td>
                    <td className="py-3 text-center text-green-500">
                      <CheckCircle className="h-4 w-4 mx-auto" />
                    </td>
                  </tr>
                  <tr>
                    <td className="py-3 font-medium">AI contract analysis</td>
                    <td className="py-3 text-center text-green-500">
                      <CheckCircle className="h-4 w-4 mx-auto" />
                    </td>
                    <td className="py-3 text-center text-green-500">
                      <CheckCircle className="h-4 w-4 mx-auto" />
                    </td>
                  </tr>
                  <tr>
                    <td className="py-3 font-medium">Contract repository</td>
                    <td className="py-3 text-center text-green-500">
                      <CheckCircle className="h-4 w-4 mx-auto" />
                    </td>
                    <td className="py-3 text-center text-green-500">
                      <CheckCircle className="h-4 w-4 mx-auto" />
                    </td>
                  </tr>
                  <tr>
                    <td className="py-3 font-medium">Renewal alerts</td>
                    <td className="py-3 text-center text-green-500">
                      <CheckCircle className="h-4 w-4 mx-auto" />
                    </td>
                    <td className="py-3 text-center text-green-500">
                      <CheckCircle className="h-4 w-4 mx-auto" />
                    </td>
                  </tr>
                  <tr>
                    <td className="py-3 font-medium">Dashboard & analytics</td>
                    <td className="py-3 text-center text-muted-foreground">Basic</td>
                    <td className="py-3 text-center text-green-500">
                      <CheckCircle className="h-4 w-4 mx-auto" />
                    </td>
                  </tr>
                  <tr>
                    <td className="py-3 font-medium">Team collaboration</td>
                    <td className="py-3 text-center text-muted-foreground">—</td>
                    <td className="py-3 text-center text-green-500">
                      <CheckCircle className="h-4 w-4 mx-auto" />
                    </td>
                  </tr>
                  <tr>
                    <td className="py-3 font-medium">Role-based access control</td>
                    <td className="py-3 text-center text-muted-foreground">—</td>
                    <td className="py-3 text-center text-green-500">
                      <CheckCircle className="h-4 w-4 mx-auto" />
                    </td>
                  </tr>
                  <tr>
                    <td className="py-3 font-medium">Approval workflows</td>
                    <td className="py-3 text-center text-muted-foreground">—</td>
                    <td className="py-3 text-center text-green-500">
                      <CheckCircle className="h-4 w-4 mx-auto" />
                    </td>
                  </tr>
                  <tr>
                    <td className="py-3 font-medium">Contract templates library</td>
                    <td className="py-3 text-center text-muted-foreground">—</td>
                    <td className="py-3 text-center text-green-500">
                      <CheckCircle className="h-4 w-4 mx-auto" />
                    </td>
                  </tr>
                  <tr>
                    <td className="py-3 font-medium">PDF export</td>
                    <td className="py-3 text-center text-muted-foreground">—</td>
                    <td className="py-3 text-center text-green-500">
                      <CheckCircle className="h-4 w-4 mx-auto" />
                    </td>
                  </tr>
                  <tr>
                    <td className="py-3 font-medium">Audit logs</td>
                    <td className="py-3 text-center text-muted-foreground">—</td>
                    <td className="py-3 text-center text-green-500">
                      <CheckCircle className="h-4 w-4 mx-auto" />
                    </td>
                  </tr>
                  <tr>
                    <td className="py-3 font-medium">Priority support</td>
                    <td className="py-3 text-center text-muted-foreground">—</td>
                    <td className="py-3 text-center text-green-500">
                      <CheckCircle className="h-4 w-4 mx-auto" />
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          </CardContent>
        </Card>

        {/* FAQ */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <HelpCircle className="h-5 w-5" />
              Frequently Asked Questions
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="space-y-4 text-sm">
              <details className="group border rounded-lg p-4">
                <summary className="flex items-center gap-2 cursor-pointer font-medium">
                  <HelpCircle className="h-4 w-4 text-muted-foreground" />
                  What happens when my trial ends?
                </summary>
                <p className="mt-2 text-muted-foreground">
                  {trialPlan
                    ? `When your ${trialPlan.trialDays}-day trial ends or you reach the ${trialPlan.uploadLimit}-contract limit, you'll need to subscribe to the Team plan to keep uploading and analyzing contracts.`
                    : "When your trial ends or you reach the trial's contract limit, you'll need to subscribe to the Team plan to keep uploading and analyzing contracts."}{' '}
                  Your contracts and data are preserved, and you can still view and download them.
                </p>
              </details>
              <details className="group border rounded-lg p-4">
                <summary className="flex items-center gap-2 cursor-pointer font-medium">
                  <HelpCircle className="h-4 w-4 text-muted-foreground" />
                  Can I cancel my subscription anytime?
                </summary>
                <p className="mt-2 text-muted-foreground">
                  Yes, you can cancel anytime from the billing portal. Your subscription will remain active until the end of the current billing period, and you won't be charged again.
                </p>
              </details>
              <details className="group border rounded-lg p-4">
                <summary className="flex items-center gap-2 cursor-pointer font-medium">
                  <HelpCircle className="h-4 w-4 text-muted-foreground" />
                  What payment methods do you accept?
                </summary>
                <p className="mt-2 text-muted-foreground">
                  We accept all major credit cards (Visa, Mastercard, American Express) and digital wallets (Apple Pay, Google Pay) through Stripe.
                </p>
              </details>
              <details className="group border rounded-lg p-4">
                <summary className="flex items-center gap-2 cursor-pointer font-medium">
                  <HelpCircle className="h-4 w-4 text-muted-foreground" />
                  Is there a per-seat pricing?
                </summary>
                <p className="mt-2 text-muted-foreground">
                  {teamPlan && teamPlan.price != null
                    ? `No, the Team plan is a flat ${formatPrice(teamPlan.price, teamPlan.currency)}/${intervalLabel(teamPlan.interval)} for your entire team (${teamPlan.memberLimit ? `up to ${teamPlan.memberLimit} members` : 'unlimited members'}). No per-seat fees.`
                    : 'No, the Team plan is a flat price for your entire team. No per-seat fees.'}
                </p>
              </details>
              <details className="group border rounded-lg p-4">
                <summary className="flex items-center gap-2 cursor-pointer font-medium">
                  <HelpCircle className="h-4 w-4 text-muted-foreground" />
                  How do I update my payment method?
                </summary>
                <p className="mt-2 text-muted-foreground">
                  Click "Manage subscription" (or "Update payment method" if a payment failed) to open the Stripe Billing Portal where you can update payment methods, view invoices, and manage your subscription.
                </p>
              </details>
            </div>
          </CardContent>
        </Card>
      </div>
    </Layout>
  );
}