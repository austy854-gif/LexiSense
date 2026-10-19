import { SomethingWentWrongIllustration } from './illustrations/Illustrations';
import { EmptyState } from './EmptyState';

/**
 * Top-level error boundary fallback (Sentry.ErrorBoundary in index.js).
 * Rendered outside the router/theme providers, so it uses plain elements.
 * The support@ line from the README is omitted until that mailbox exists.
 */
export function ErrorFallback({ resetError }) {
  return (
    <main className="flex min-h-screen items-center justify-center bg-background p-4">
      <EmptyState
        illustration={SomethingWentWrongIllustration}
        title="Something went wrong"
        headingLevel={1}
        role="alert"
        action={
          <button
            type="button"
            onClick={() => (resetError ? resetError() : window.location.reload())}
            className="inline-flex items-center justify-center rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground hover:bg-primary/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
          >
            Try again
          </button>
        }
      >
        <p>We couldn't load this just now. Try again in a moment.</p>
      </EmptyState>
    </main>
  );
}
