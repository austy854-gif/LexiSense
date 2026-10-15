import { useEffect, useState } from 'react';
import { useSearchParams, Link } from 'react-router-dom';
import api from '../api';
import { Button } from '../components/ui/button';
import { Textarea } from '../components/ui/textarea';

// Landing page for the one-click Approve/Reject links in approval emails.
// The GET preview never changes state (so email link scanners cannot approve
// anything); the decision is only made when the user clicks Confirm (POST).
export default function ApprovalRespondPage() {
  const [params] = useSearchParams();
  const token = params.get('token') || '';
  const [preview, setPreview] = useState(null);
  const [error, setError] = useState(null);
  const [result, setResult] = useState(null);
  const [comment, setComment] = useState('');
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (!token) {
      setError('This approval link is missing its token.');
      return;
    }
    api
      .get('/approvals/email-action', { params: { token } })
      .then((res) => setPreview(res.data))
      .catch((err) => setError(err.response?.data?.detail || 'This approval link could not be verified.'));
  }, [token]);

  const confirm = async () => {
    setSubmitting(true);
    try {
      const res = await api.post('/approvals/email-action', { token, comment: comment || null });
      setResult(res.data);
    } catch (err) {
      setError(err.response?.data?.detail || 'Something went wrong. Please try again from LexiSense.');
    } finally {
      setSubmitting(false);
    }
  };

  const isApprove = preview?.action === 'approve';

  return (
    <div className="min-h-screen flex items-center justify-center bg-background p-6">
      <div className="w-full max-w-md rounded-xl border bg-card p-8 shadow-sm" data-testid="approval-respond">
        <h1 className="text-2xl font-bold text-primary mb-6">LexiSense</h1>
        {error && (
          <div data-testid="approval-error">
            <p className="text-destructive font-medium mb-4">{error}</p>
            <Link className="text-primary underline" to="/contracts">Open LexiSense</Link>
          </div>
        )}
        {!error && result && (
          <div data-testid="approval-done">
            <p className="text-lg font-medium mb-4">{result.message}</p>
            <Link className="text-primary underline" to={`/contracts/${result.contractId}`}>View the contract</Link>
          </div>
        )}
        {!error && !result && !preview && <p className="text-muted-foreground">Verifying link…</p>}
        {!error && !result && preview && (
          <div>
            <p className="mb-2">
              {preview.requesterEmail || 'A teammate'} requested your approval for{' '}
              <strong>{preview.contractTitle || 'a contract'}</strong>.
            </p>
            <p className="mb-4 text-sm text-muted-foreground">
              Confirm to <strong>{isApprove ? 'approve' : 'reject'}</strong> this contract. This link can be used once.
            </p>
            <Textarea
              placeholder="Optional comment"
              value={comment}
              onChange={(e) => setComment(e.target.value)}
              className="mb-4"
            />
            <div className="flex gap-3">
              <Button
                onClick={confirm}
                disabled={submitting}
                variant={isApprove ? 'default' : 'destructive'}
                data-testid="approval-confirm"
              >
                {submitting ? 'Working…' : isApprove ? 'Confirm approval' : 'Confirm rejection'}
              </Button>
              <Link className="self-center text-sm underline" to={`/contracts/${preview.contractId}`}>
                View contract
              </Link>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
