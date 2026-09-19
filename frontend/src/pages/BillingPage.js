import { useState, useEffect } from 'react';
import { Layout } from '../components/Layout';
import { billingAPI } from '../api';
import { useAuth } from '../contexts/AuthContext';
import { Button } from '../components/ui/button';
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '../components/ui/card';
import { Badge } from '../components/ui/badge';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '../components/ui/dialog';
import { Skeleton } from '../components/ui/skeleton';
import { Separator } from '../components/ui/separator';
import {
  CreditCard,
  Crown,
  Clock,
  CheckCircle,
  XCircle,
  Loader2,
  ArrowRight,
  Shield,
  Users,
  FileText,
  Bell,
  Download,
  Lock,
  HelpCircle,
} from 'lucide-react';
import { toast } from 'sonner';

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

const FeatureRow = ({ icon: Icon, children }) => (
  <div className="flex items-start gap-3">
    <Icon className="h-5 w-5 text-muted-foreground shrink-0 mt-0.5" />
    <span className="text-sm text-muted-foreground">{children}</span>
  </div>
);

export default function BillingPage() {
  const { user } = useAuth();
  const [subscription, setSubscription] = useState(null);
  const [plans, setPlans] = useState(null);
  const [loading, setLoading] = useState(true);
  const [checkoutLoading, setCheckoutLoading] = useState(false);
  const [portalLoading, setPortalLoading] = useState(false);

  const isAdmin = user?.role === 'admin';

  useEffect(() => {
    fetchBillingData();
  }, []);

  const fetchBillingData = async () => {
    try {
      const [subRes, plansRes] = await Promise.all([
        billingAPI.getSubscription(),
        billingAPI.getPlans(),
      ]);
      setSubscription(subRes.data);
      setPlans(plansRes.data.plans);
    } catch (error) {
      toast.error('Failed to load billing information');
      console.error(error);
    } finally {
      setLoading(false);
    }
  };

  const handleCheckout = async (priceId) => {
    if (!isAdmin) return;
    setCheckoutLoading(true);
    try {
      const successUrl = `${window.location.origin}/billing?success=true`;
      const cancelUrl = `${window.location.origin}/billing?canceled=true`;
      const res = await billingAPI.createCheckout(priceId, successUrl, cancelUrl);
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

  const trialDaysLeft = subscription?.trialEndDate ? getTrialDaysLeft(subscription.trialEndDate) : 0;
  const isTrial = subscription?.subscriptionTier === 'trial';
  const isTeam = subscription?.subscriptionTier === 'team';

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
              Current Plan
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
                    <TierBadge tier={subscription?.subscriptionTier} />
                    <StatusBadge status={subscription?.billingStatus} />
                  </div>
                  <p className="text-sm text-muted-foreground mt-1">
                    {isTrial ? (
                      trialDaysLeft > 0
                        ? `${trialDaysLeft} day${trialDaysLeft !== 1 ? 's' : ''} left in trial`
                        : 'Trial ended'
                    ) : isTeam ? (
                      subscription?.currentPeriodEnd
                        ? `Renews on ${formatDate(subscription.currentPeriodEnd)}`
                        : 'Active subscription'
                    ) : (
                      'No active subscription'
                    )}
                  </p>
                </div>
              </div>
              
              {isTrial && trialDaysLeft > 0 && (
                <div className="text-right">
                  <p className="text-sm text-muted-foreground">
                    {subscription?.trialContractsUsed || 0} / {subscription?.trialContractsLimit || 3} contracts used
                  </p>
                  <div className="w-48 h-2 bg-muted rounded-full overflow-hidden mt-1">
                    <div
                      className="h-full bg-primary transition-all"
                      style={{
                        width: `${((subscription?.trialContractsUsed || 0) / (subscription?.trialContractsLimit || 3)) * 100}%`
                      }}
                    />
                  </div>
                </div>
              )}

              {isTeam && subscription?.cancelAtPeriodEnd && (
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

            <div className="flex flex-wrap gap-3 pt-2">
              {isTrial && (
                <>
                  <Button 
                    onClick={() => handleCheckout(plans?.find(p => p.id === 'team')?.priceId)}
                    disabled={checkoutLoading}
                    className="gap-2"
                  >
                    <Crown className="h-4 w-4" />
                    Upgrade to Team Plan
                  </Button>
                </>
              )}
              {isTeam && (
                <>
                  <Button 
                    variant="outline"
                    onClick={handlePortal}
                    disabled={portalLoading}
                    className="gap-2"
                  >
                    <CreditCard className="h-4 w-4" />
                    Manage Subscription
                  </Button>
                  {subscription?.cancelAtPeriodEnd && (
                    <Button 
                      variant="outline"
                      onClick={handlePortal}
                      disabled={portalLoading}
                      className="gap-2 bg-green-500/10 text-green-500 border-green-500/20 hover:bg-green-500/20"
                    >
                      <ArrowRight className="h-4 w-4" />
                      Resume Subscription
                    </Button>
                  )}
                </>
              )}
              {subscription?.subscriptionTier === 'cancelled' && (
                <Button 
                  onClick={() => handleCheckout(plans?.find(p => p.id === 'team')?.priceId)}
                  disabled={checkoutLoading}
                  className="gap-2"
                >
                  <Crown className="h-4 w-4" />
                  Reactivate Team Plan
                </Button>
              )}
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
              {plans?.map((plan) => (
                <div
                  key={plan.id}
                  className={`relative p-6 border rounded-xl ${
                    plan.id === 'team' ? 'border-primary/30 bg-primary/5' : 'border-muted'
                  } ${subscription?.subscriptionTier === plan.id ? 'ring-2 ring-primary' : ''}`}
                >
                  {plan.id === 'team' && (
                    <div className="absolute -top-3 left-1/2 -translate-x-1/2">
                      <Badge className="bg-primary text-primary-foreground px-3 py-1">
                        Most Popular
                      </Badge>
                    </div>
                  )}
                  
                  <div className="text-center mb-6">
                    <div className="flex items-baseline justify-center gap-1">
                      <span className="text-4xl font-bold">${plan.price}</span>
                      <span className="text-muted-foreground">/{plan.interval}</span>
                    </div>
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

                  <Button
                    className="w-full gap-2"
                    variant={subscription?.subscriptionTier === plan.id ? 'outline' : 'default'}
                    disabled={subscription?.subscriptionTier === plan.id || checkoutLoading}
                    onClick={() => plan.priceId && handleCheckout(plan.priceId)}
                  >
                    {subscription?.subscriptionTier === plan.id ? (
                      <>
                        <CheckCircle className="h-4 w-4" />
                        Current Plan
                      </>
                    ) : plan.cta === 'Start Free Trial' ? (
                      <>
                        <Clock className="h-4 w-4" />
                        {plan.cta}
                      </>
                    ) : (
                      <>
                        <Crown className="h-4 w-4" />
                        {plan.cta}
                      </>
                    )}
                  </Button>
                </div>
              ))}
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
                    <td className="py-3 text-center text-muted-foreground">3 per trial</td>
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
                  When your 7-day trial ends or you reach the 3-contract limit, you'll need to upgrade to the Team plan to continue using LexiSense. Your contracts and data will be preserved.
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
                  No, the Team plan is a flat $49/month for your entire team (up to 10 members). No per-seat fees.
                </p>
              </details>
              <details className="group border rounded-lg p-4">
                <summary className="flex items-center gap-2 cursor-pointer font-medium">
                  <HelpCircle className="h-4 w-4 text-muted-foreground" />
                  How do I update my payment method?
                </summary>
                <p className="mt-2 text-muted-foreground">
                  Click "Manage Subscription" to open the Stripe Billing Portal where you can update payment methods, view invoices, and manage your subscription.
                </p>
              </details>
            </div>
          </CardContent>
        </Card>
      </div>
    </Layout>
  );
}