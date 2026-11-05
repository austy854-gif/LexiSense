import { BrandLockup } from './BrandLockup';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from './ui/card';

/**
 * Shared light-default auth chrome (LX-PG-LOGIN / INVITE / APPROVE).
 * Uses theme background + card — never the Steady Blue dark slate gradient.
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
          {title && <CardTitle className="text-2xl font-bold">{title}</CardTitle>}
          {description && <CardDescription>{description}</CardDescription>}
        </CardHeader>
        <CardContent>{children}</CardContent>
      </Card>
    </div>
  );
}
