import { BrandLockup } from './BrandLockup';
import { Card, CardContent, CardDescription, CardHeader } from './ui/card';

/**
 * Shared light-default auth chrome (LX-PG-LOGIN / INVITE / APPROVE).
 * Uses theme background + card — never the Steady Blue dark slate gradient.
 * Title is an h1 (CardTitle is a div) so auth/token pages keep a page heading.
 */
export function AuthShell({ title, description, children, testId, showLockup = true }) {
  return (
    <div className="min-h-screen flex items-center justify-center bg-background p-4" data-testid={testId}>
      <Card className="w-full max-w-md border-border shadow-sm">
        <CardHeader className="text-center pb-2">
          {showLockup && (
            <div className="mb-4 flex justify-center">
              <BrandLockup className="h-10 w-auto" />
            </div>
          )}
          {title && <h1 className="text-2xl font-bold leading-none tracking-tight">{title}</h1>}
          {description && <CardDescription className="mt-1.5">{description}</CardDescription>}
        </CardHeader>
        <CardContent>{children}</CardContent>
      </Card>
    </div>
  );
}
