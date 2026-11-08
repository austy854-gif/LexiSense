import { useState } from 'react';
import { Link } from 'react-router-dom';
import { authAPI } from '../api';
import { Button } from '../components/ui/button';
import { Input } from '../components/ui/input';
import { Label } from '../components/ui/label';
import { Loader2 } from 'lucide-react';
import { AuthShell } from '../components/AuthShell';
import { toast } from 'sonner';
import { getErrorInfo } from '../lib/apiHelpers.mjs';

export default function ForgotPasswordPage() {
  const [email, setEmail] = useState('');
  const [loading, setLoading] = useState(false);
  const [sent, setSent] = useState(false);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setLoading(true);
    try {
      const response = await authAPI.forgotPassword(email);
      setSent(true);
      toast.success(response.data?.message || 'Check your email for a reset link.');
    } catch (error) {
      toast.error(getErrorInfo(error).message || 'Could not send a reset link');
    } finally {
      setLoading(false);
    }
  };

  return (
    <AuthShell
      title="Reset your password"
      description={sent
        ? 'If an account exists for that email, a reset link is on its way.'
        : 'We will email you a link that expires in one hour.'}
      testId="forgot-password-page"
    >
          {!sent && (
            <form onSubmit={handleSubmit} className="space-y-4">
              <div className="space-y-2">
                <Label htmlFor="email">Email</Label>
                <Input
                  id="email"
                  type="email"
                  placeholder="name@company.com"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  required
                  data-testid="forgot-email"
                />
              </div>
              <Button type="submit" className="w-full" disabled={loading} data-testid="forgot-submit">
                {loading ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
                Send reset link
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
