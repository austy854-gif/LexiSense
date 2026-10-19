/**
 * Empty / error state: illustration + headline + body + optional action.
 * The illustration is decorative here (aria-hidden) because the headline
 * says the same thing; screen readers get the visible text only.
 */
export function EmptyState({
  illustration: Illustration,
  title,
  headingLevel = 3,
  children,
  action,
  size = 'md',
  role,
  className = '',
  ...rest
}) {
  const Heading = `h${headingLevel}`;
  const width = size === 'sm' ? 'w-32' : 'w-48';
  return (
    <div
      className={`flex flex-col items-center px-6 py-12 text-center ${size === 'sm' ? 'py-6' : ''} ${className}`.trim()}
      role={role}
      {...rest}
    >
      {Illustration && <Illustration className={`${width} mb-4`} />}
      <Heading className={`${size === 'sm' ? 'text-base' : 'text-lg'} font-semibold text-foreground`}>{title}</Heading>
      {children && <div className="mt-2 max-w-md space-y-1 text-sm text-muted-foreground">{children}</div>}
      {action && <div className="mt-5">{action}</div>}
    </div>
  );
}
