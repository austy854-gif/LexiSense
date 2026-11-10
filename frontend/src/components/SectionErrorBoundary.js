import { Component } from 'react';
import { useLocation } from 'react-router-dom';
import * as Sentry from '@sentry/react';
import { AlertTriangle } from 'lucide-react';

/** Send a caught render error to Sentry, tagged with the boundary scope. */
function reportError(error, info, scope) {
  try {
    Sentry.captureException(error, {
      tags: { boundary: scope },
      extra: { componentStack: info && info.componentStack },
    });
  } catch {
    /* reporting must never throw from a boundary */
  }
}

/**
 * Contains a render error to one part of the page (LX-ENG-KT-01), so a single
 * bad panel shows a small fallback instead of the app-wide ErrorFallback.
 * Changing `resetKey` (e.g. the contract id or route) clears the error.
 */
export class SectionErrorBoundary extends Component {
  /** Start with no error. */
  constructor(props) {
    super(props);
    this.state = { error: null };
    this.reset = this.reset.bind(this);
  }

  /** Switch to the fallback when a child throws. */
  static getDerivedStateFromError(error) {
    return { error };
  }

  /** Report the error; the fallback is already showing. */
  componentDidCatch(error, info) {
    reportError(error, info, this.props.scope || 'section');
  }

  /** Clear the error when `resetKey` changes (new contract or route). */
  componentDidUpdate(prevProps) {
    if (this.state.error && prevProps.resetKey !== this.props.resetKey) {
      this.reset();
    }
  }

  /** Clear the error and re-render the children. */
  reset() {
    this.setState({ error: null });
  }

  /** Children, or the fallback after an error. */
  render() {
    if (!this.state.error) return this.props.children;
    if (typeof this.props.fallback === 'function') return this.props.fallback({ reset: this.reset });
    if (this.props.fallback) return this.props.fallback;
    return <SectionFallback onRetry={this.reset} />;
  }
}

/** Small inline fallback for one panel. Text only, no server or stack details. */
export function SectionFallback({
  title = "This section couldn't be displayed",
  message = 'Something in this part of the page failed to load. The rest of the page still works.',
  onRetry,
  testId = 'section-error-fallback',
}) {
  return (
    <div
      role="alert"
      data-testid={testId}
      className="flex items-start gap-3 p-4 rounded-lg border border-destructive/30 bg-destructive/5"
    >
      <AlertTriangle className="h-5 w-5 text-destructive mt-0.5 shrink-0" aria-hidden="true" />
      <div>
        <p className="font-medium">{title}</p>
        <p className="text-sm text-muted-foreground mt-1">{message}</p>
        {onRetry && (
          <button
            type="button"
            onClick={onRetry}
            className="mt-2 text-sm font-medium text-primary underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded"
          >
            Try again
          </button>
        )}
      </div>
    </div>
  );
}

/**
 * Route-level boundary: a page that throws shows a contained fallback inside
 * the router (toasts, navigation and the URL keep working), and navigating to
 * another route clears it. Must be rendered inside the Router.
 */
export function RouteErrorBoundary({ children }) {
  const location = useLocation();
  return (
    <SectionErrorBoundary
      scope="route"
      resetKey={location.pathname}
      fallback={({ reset }) => (
        <main className="flex min-h-screen items-center justify-center bg-background p-4" data-testid="route-error-fallback">
          <div className="max-w-md w-full">
            <SectionFallback
              testId="route-error-fallback-panel"
              title="This page couldn't be displayed"
              message="Something on this page failed to load. Try again, or go back to the dashboard. If it keeps happening, email support@lexisense.net."
              onRetry={reset}
            />
            <div className="mt-4 text-center">
              <a href="/" className="text-sm font-medium text-primary hover:underline">Go to dashboard</a>
            </div>
          </div>
        </main>
      )}
    >
      {children}
    </SectionErrorBoundary>
  );
}
