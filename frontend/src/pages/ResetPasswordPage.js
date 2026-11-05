import { useEffect, useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { authAPI } from '../api';
import { Button } from '../components/ui/button';
import { Input } from '../components/ui/input';
import { Label } from '../components/ui/label';
import { Loader2 } from 'lucide-react';
import { AuthShell } from '../components/AuthShell';
import { toast } from 'sonner';
import { getErrorInfo } from '../lib/apiHelpers.mjs';

export default function ResetPasswordPage() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const [token] = useState(() => params.get('token') || '');
  const [password, setPassword] = useState('');
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (token && window.location.search.includes('token=')) {
      window.history.replaceState({}, '', '/reset-password');
    }
  }, [token]);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setLoading(true);
    try {
      const response = await authAPI.resetPassword(token, password);
      toast.success(response.data?.message || 'Password updated. Sign in with your new password.');
      navigate('/login');
    } catch (error) {
      toast.error(getErrorInfo(error).message || 'That reset link is invalid or expired');
    } finally {
      setLoading(false);
    }
  };

  return (
    <AuthShell
      title="Choose a new password"
      description="At least 8 characters. This signs every other session out."
      testId="reset-password-page"
    >
          {!token ? (
            <p className="text-sm text-muted-foreground text-center">
              This reset link is incomplete. Request a new one from the sign-in page.
            </p>
          ) : (
            <form onSubmit={handleSubmit} className="space-y-4">
              <div className="space-y-2">
                <Label htmlFor="password">New password</Label>
                <Input
                  id="password"
                  type="password"
                  value={password}
                  minLength={8}
                  maxLength={72}
                  onChange={(e) => setPassword(e.target.value)}
                  required
                  data-testid="reset-password"
                />
              </div>
              <Button type="submit" className="w-full" disabled={loading} data-testid="reset-submit">
                {loading ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
                Update password
              </Button>
            </form>
          )}
          <div className="mt-6 text-center text-sm">
            <Link to="/login" className="text-primary hover:underline font-medium">
              Back to sign in
            </Link>
          </div>
        </AuthShell>
  );
}
