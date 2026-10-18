import { useEffect, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { teamAPI } from '../api';
import { useAuth } from '../contexts/AuthContext';
import { getErrorInfo } from '../lib/apiHelpers.mjs';
import {
  INVITE_COPY,
  buildAcceptPayload,
  classifyInviteError,
  inviteViewState,
  readInviteToken,
  roleLabel,
  validateInvitePassword,
} from '../lib/inviteFlow.mjs';
import { Button } from '../components/ui/button';
import { Input } from '../components/ui/input';
import { Label } from '../components/ui/label';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '../components/ui/card';
import { Loader2, Eye, EyeOff, UserPlus, AlertCircle } from 'lucide-react';
import { toast } from 'sonner';

// Landing page for the "Accept invite" button in team invite emails
// (backend/services/email_service.py builds `${APP_URL}/accept-invite?token=...`).
// Public route: the token is the credential. It is read once, then removed
// from the address bar so it doesn't linger in history or leak via Referer.
// Opening the link never changes anything; the invite is only claimed when
// the form is submitted.

function Shell({ children }) {
  return (
    <div className="min-h-screen flex items-center justify-center bg-gradient-to-br from-slate-900 via-slate-800 to-slate-900 p-4">
      <Card className="w-full max-w-md relative bg-card/95 backdrop-blur border-border/50 shadow-2xl" data-testid="accept-invite-page">
        {children}
      </Card>
    </div>
  );
}

function Message({ state, title, body, children }) {
  return (
    <Shell>
      <CardHeader className="text-center pb-2">
        <div className="mx-auto mb-4 p-3 bg-muted rounded-xl w-fit">
          <AlertCircle className="h-8 w-8 text-muted-foreground" />
        </div>
        <CardTitle className="text-2xl font-bold" data-testid={`accept-invite-${state}`}>{title}</CardTitle>
        <CardDescription>{body}</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-2">{children}</CardContent>
    </Shell>
  );
}

export default function AcceptInvitePage() {
  const { user, loading: authLoading, login, logout } = useAuth();
  const navigate = useNavigate();

  const [token] = useState(() => readInviteToken(window.location.search));
  const [preview, setPreview] = useState(null);
  const [errorKind, setErrorKind] = useState(null);
  const [form, setForm] = useState({ firstName: '', lastName: '', password: '', confirmPassword: '' });
  const [showPassword, setShowPassword] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    const meta = document.createElement('meta');
    meta.name = 'referrer';
    meta.content = 'no-referrer';
    document.head.appendChild(meta);
    if (window.location.search) {
      window.history.replaceState(null, '', window.location.pathname);
    }
    return () => meta.remove();
  }, []);

  useEffect(() => {
    if (!token) return;
    let cancelled = false;
    teamAPI
      .previewInvite(token)
      .then((res) => !cancelled && setPreview(res.data))
      .catch((err) => !cancelled && setErrorKind(classifyInviteError(err)));
    return () => {
      cancelled = true;
    };
  }, [token]);

  const state = inviteViewState({ token, authLoading, user, preview, errorKind });
  const existingAccount = Boolean(preview?.accountExists);
  const leavingOtherTeam = existingAccount && Boolean(preview?.accountInOtherTeam);

  const set = (field) => (e) => setForm({ ...form, [field]: e.target.value });

  const handleSubmit = async (e) => {
    e.preventDefault();
    const problem = validateInvitePassword(form.password, form.confirmPassword, { existingAccount });
    if (problem) {
      toast.error(problem);
      return;
    }
    setSubmitting(true);
    let accepted;
    try {
      const res = await teamAPI.acceptInvite(buildAcceptPayload(token, form));
      accepted = res.data;
    } catch (err) {
      const kind = classifyInviteError(err);
      if (kind === 'invalid' || kind === 'expired') {
        setErrorKind(kind);
      } else if (kind === 'forbidden' || kind === 'used') {
        // 403: wrong password / switched-off account. 409 on accept: the
        // server refused the move (sole admin of another team). Show its text.
        toast.error(getErrorInfo(err).message);
      } else if (kind === 'rate_limited') {
        toast.error(INVITE_COPY.rateLimitedToast);
      } else if (kind === 'validation') {
        toast.error(getErrorInfo(err).message);
      } else {
        toast.error(INVITE_COPY.acceptFailed);
      }
      setSubmitting(false);
      return;
    }

    // Account created: sign straight in with the password just chosen.
    try {
      await login(accepted?.email || preview.email, form.password);
      toast.success(
        accepted?.alreadyMember
          ? INVITE_COPY.alreadyMemberToast
          : accepted?.existingAccount
            ? INVITE_COPY.joinedToast
            : INVITE_COPY.successToast,
      );
      navigate('/', { replace: true });
    } catch {
      toast.success(INVITE_COPY.successSignInToast);
      navigate('/login', { replace: true });
    } finally {
      setSubmitting(false);
    }
  };

  if (state === 'loading') {
    return (
      <div className="min-h-screen flex items-center justify-center bg-background" data-testid="accept-invite-loading">
        <Loader2 className="h-8 w-8 animate-spin text-primary" />
      </div>
    );
  }

  const signInButton = (
    <Button asChild className="w-full">
      <Link to="/login">{INVITE_COPY.signIn}</Link>
    </Button>
  );
  const dashboardButton = (
    <Button asChild className="w-full">
      <Link to="/">{INVITE_COPY.goToDashboard}</Link>
    </Button>
  );

  if (state === 'missing') {
    return <Message state={state} title={INVITE_COPY.missingTitle} body={INVITE_COPY.missingBody}>{user ? dashboardButton : signInButton}</Message>;
  }
  if (state === 'invalid') {
    return <Message state={state} title={INVITE_COPY.invalidTitle} body={INVITE_COPY.invalidBody}>{user ? dashboardButton : signInButton}</Message>;
  }
  if (state === 'expired') {
    return <Message state={state} title={INVITE_COPY.expiredTitle} body={INVITE_COPY.expiredBody}>{user ? dashboardButton : signInButton}</Message>;
  }
  if (state === 'used') {
    return <Message state={state} title={INVITE_COPY.usedTitle} body={INVITE_COPY.usedBody}>{user ? dashboardButton : signInButton}</Message>;
  }
  if (state === 'rate_limited') {
    return <Message state={state} title={INVITE_COPY.rateLimitedTitle} body={INVITE_COPY.rateLimitedBody} />;
  }
  if (state === 'error') {
    return <Message state={state} title={INVITE_COPY.errorTitle} body={INVITE_COPY.errorBody} />;
  }
  if (state === 'signed_in_other') {
    return (
      <Message
        state={state}
        title={INVITE_COPY.signedInOtherTitle}
        body={`You're signed in as ${user.email}, but this invite is for ${preview.email}. Sign out to accept it.`}
      >
        <Button className="w-full" onClick={logout} data-testid="accept-invite-sign-out">
          {INVITE_COPY.signOutAndAccept}
        </Button>
        <Button asChild variant="outline" className="w-full">
          <Link to="/">{INVITE_COPY.goToDashboard}</Link>
        </Button>
      </Message>
    );
  }

  // state === 'form'
  const team = preview.organizationName || 'your team';
  return (
    <Shell>
      <CardHeader className="text-center pb-2">
        <div className="mx-auto mb-4 p-3 bg-primary rounded-xl w-fit">
          <UserPlus className="h-8 w-8 text-primary-foreground" />
        </div>
        <CardTitle className="text-2xl font-bold" data-testid="accept-invite-form-title">{INVITE_COPY.formTitle}</CardTitle>
        <CardDescription>
          {preview.inviterName ? `${preview.inviterName} invited you to join ` : 'You were invited to join '}
          <strong>{team}</strong> on LexiSense as {roleLabel(preview.role) === 'Admin' ? 'an' : 'a'} {roleLabel(preview.role)}.
          {existingAccount ? (
            <span className="block mt-2" data-testid="accept-invite-existing-account">{INVITE_COPY.existingAccountIntro}</span>
          ) : (
            ' Create your account to get started.'
          )}
        </CardDescription>
      </CardHeader>
      <CardContent>
        <form onSubmit={handleSubmit} className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="invite-email">Email</Label>
            <Input id="invite-email" type="email" value={preview.email} readOnly disabled data-testid="accept-invite-email" />
          </div>
          {!existingAccount && (
          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-2">
              <Label htmlFor="invite-first-name">First name</Label>
              <Input id="invite-first-name" placeholder="John" value={form.firstName} onChange={set('firstName')} autoComplete="given-name" />
            </div>
            <div className="space-y-2">
              <Label htmlFor="invite-last-name">Last name</Label>
              <Input id="invite-last-name" placeholder="Doe" value={form.lastName} onChange={set('lastName')} autoComplete="family-name" />
            </div>
          </div>
          )}
          {leavingOtherTeam && (
            <p className="text-sm text-muted-foreground" data-testid="accept-invite-leave-team-warning">
              {INVITE_COPY.leaveTeamWarning}
            </p>
          )}
          <div className="space-y-2">
            <Label htmlFor="invite-password">Password</Label>
            <div className="relative">
              <Input
                id="invite-password"
                type={showPassword ? 'text' : 'password'}
                placeholder={existingAccount ? 'Your LexiSense password' : 'At least 8 characters'}
                value={form.password}
                onChange={set('password')}
                autoComplete={existingAccount ? 'current-password' : 'new-password'}
                required
                data-testid="accept-invite-password"
              />
              <Button
                type="button"
                variant="ghost"
                size="icon"
                className="absolute right-0 top-0 h-full px-3 hover:bg-transparent"
                onClick={() => setShowPassword(!showPassword)}
                aria-label={showPassword ? 'Hide password' : 'Show password'}
              >
                {showPassword ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
              </Button>
            </div>
          </div>
          {!existingAccount && (
          <div className="space-y-2">
            <Label htmlFor="invite-confirm-password">Confirm password</Label>
            <Input
              id="invite-confirm-password"
              type={showPassword ? 'text' : 'password'}
              placeholder="Confirm your password"
              value={form.confirmPassword}
              onChange={set('confirmPassword')}
              autoComplete="new-password"
              required
              data-testid="accept-invite-confirm-password"
            />
          </div>
          )}
          <Button type="submit" className="w-full" disabled={submitting} data-testid="accept-invite-submit">
            {submitting ? (
              <>
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                {existingAccount ? INVITE_COPY.submittingExisting : INVITE_COPY.submitting}
              </>
            ) : (
              INVITE_COPY.submit
            )}
          </Button>
        </form>
      </CardContent>
    </Shell>
  );
}
