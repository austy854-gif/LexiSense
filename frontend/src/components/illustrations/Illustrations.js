// Generated from /workspace/lexisense-brand/illustrations/*-themed.svg
// (Jackson's empty-state set) by resolving each file's <style> rules per
// element (same cascade: specificity, then source order) into style props.
// Colours come from the --ls-illo-* CSS variables (illustrations.css) so they
// follow the light/dark theme, and nothing leaks into page-wide CSS when
// several illustrations are on one page.
import { useId } from 'react';
import './illustrations.css';

const FILL = 'var(--ls-illo-fill,currentColor)';
const FILL_OPACITY = 'var(--ls-illo-fill-opacity,.12)';
const LINE = 'var(--ls-illo-line,currentColor)';
const PAPER = 'var(--ls-illo-paper,Canvas)';
const MUTED = 'var(--ls-illo-muted,currentColor)';
const MUTED_OPACITY = 'var(--ls-illo-muted-opacity,.35)';
const ACCENT = 'var(--ls-illo-accent,currentColor)';
const PAPER_STROKE = 'var(--ls-illo-paper,#fff)';

/**
 * Shared wrapper. Decorative by default (aria-hidden), for use next to a
 * visible headline. Pass `title` (and optionally `description`) when the
 * illustration stands alone and must be announced: it then gets role="img"
 * and unique ids for aria-labelledby.
 */
function IllustrationSvg({ title, description, className = '', children, ...rest }) {
  const id = useId();
  const meaningful = Boolean(title);
  const titleId = `${id}-t`;
  const descId = `${id}-d`;
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 240 180"
      width="240"
      height="180"
      className={`ls-illustration ${className}`.trim()}
      focusable="false"
      {...(meaningful
        ? { role: 'img', 'aria-labelledby': description ? `${titleId} ${descId}` : titleId }
        : { 'aria-hidden': 'true' })}
      {...rest}
    >
      {meaningful && <title id={titleId}>{title}</title>}
      {meaningful && description && <desc id={descId}>{description}</desc>}
      <g strokeWidth="5" strokeLinecap="round" strokeLinejoin="round">
        {children}
      </g>
    </svg>
  );
}

/** No contracts yet: An empty page with a plus sign, waiting for the first contract. */
export function NoContractsIllustration(props) {
  return (
    <IllustrationSvg {...props}>
      <ellipse cx="120" cy="160" rx="78" ry="8" style={{ fill: FILL, fillOpacity: FILL_OPACITY, stroke: 'none' }} />
      <rect x="56" y="34" width="96" height="120" rx="10" transform="rotate(-8 104 94)" style={{ fill: FILL, fillOpacity: FILL_OPACITY, stroke: 'none' }} />
      <path d="M84 26H146L170 50V136Q170 146 160 146H84Q74 146 74 136V36Q74 26 84 26Z" style={{ stroke: LINE, fill: PAPER }} />
      <path d="M146 26V44Q146 50 152 50H170" style={{ stroke: LINE, fill: 'none' }} />
      <path d="M90 26h14v22l-7-5-7 5z" style={{ fill: LINE, stroke: 'none' }} />
      <path d="M122 76v32M106 92h32" style={{ stroke: LINE, fill: 'none' }} />
    </IllustrationSvg>
  );
}
NoContractsIllustration.defaultTitle = 'No contracts yet';
NoContractsIllustration.defaultDescription = 'An empty page with a plus sign, waiting for the first contract';

/** No matching contracts: A page with a magnifying glass that found nothing. */
export function NoSearchResultsIllustration(props) {
  return (
    <IllustrationSvg {...props}>
      <ellipse cx="120" cy="160" rx="78" ry="8" style={{ fill: FILL, fillOpacity: FILL_OPACITY, stroke: 'none' }} />
      <path d="M68 24H130L154 48V134Q154 144 144 144H68Q58 144 58 134V34Q58 24 68 24Z" style={{ stroke: LINE, fill: PAPER }} />
      <path d="M130 24V42Q130 48 136 48H154" style={{ stroke: LINE, fill: 'none' }} />
      <path d="M74 24h14v22l-7-5-7 5z" style={{ fill: LINE, stroke: 'none' }} />
      <path d="M74 72h44M74 86h56M74 100h32" style={{ stroke: MUTED, strokeOpacity: MUTED_OPACITY, fill: 'none' }} />
      <circle cx="156" cy="104" r="26" style={{ stroke: LINE, fill: PAPER }} />
      <path d="M175 123l20 20" style={{ stroke: LINE, fill: 'none' }} />
      <path d="M146 104h20" style={{ stroke: MUTED, strokeOpacity: MUTED_OPACITY, fill: 'none' }} />
    </IllustrationSvg>
  );
}
NoSearchResultsIllustration.defaultTitle = 'No matching contracts';
NoSearchResultsIllustration.defaultDescription = 'A page with a magnifying glass that found nothing';

/** Something went wrong: A page with a wobbly line and a small alert badge. */
export function SomethingWentWrongIllustration(props) {
  return (
    <IllustrationSvg {...props}>
      <ellipse cx="120" cy="160" rx="78" ry="8" style={{ fill: FILL, fillOpacity: FILL_OPACITY, stroke: 'none' }} />
      <path d="M82 24H144L168 48V134Q168 144 158 144H82Q72 144 72 134V34Q72 24 82 24Z" style={{ stroke: LINE, fill: PAPER }} />
      <path d="M144 24V42Q144 48 150 48H168" style={{ stroke: LINE, fill: 'none' }} />
      <path d="M88 24h14v22l-7-5-7 5z" style={{ fill: LINE, stroke: 'none' }} />
      <path d="M88 72h40M88 86h52" style={{ stroke: MUTED, strokeOpacity: MUTED_OPACITY, fill: 'none' }} />
      <path d="M88 112q8-10 16 0t16 0 16 0" style={{ stroke: LINE, fill: 'none' }} />
      <circle cx="168" cy="44" r="18" style={{ fill: ACCENT, stroke: 'none' }} />
      <path d="M168 35v11M168 53v1" style={{ stroke: PAPER_STROKE, fill: 'none' }} />
    </IllustrationSvg>
  );
}
SomethingWentWrongIllustration.defaultTitle = 'Something went wrong';
SomethingWentWrongIllustration.defaultDescription = 'A page with a wobbly line and a small alert badge';

/** Upload failed: A page with an upload arrow and a small cross badge. */
export function UploadFailedIllustration(props) {
  return (
    <IllustrationSvg {...props}>
      <ellipse cx="120" cy="160" rx="78" ry="8" style={{ fill: FILL, fillOpacity: FILL_OPACITY, stroke: 'none' }} />
      <path d="M82 24H144L168 48V134Q168 144 158 144H82Q72 144 72 134V34Q72 24 82 24Z" style={{ stroke: LINE, fill: PAPER }} />
      <path d="M144 24V42Q144 48 150 48H168" style={{ stroke: LINE, fill: 'none' }} />
      <path d="M88 24h14v22l-7-5-7 5z" style={{ fill: LINE, stroke: 'none' }} />
      <path d="M120 116V72M104 88l16-16 16 16" style={{ stroke: LINE, fill: 'none' }} />
      <circle cx="168" cy="132" r="18" style={{ fill: ACCENT, stroke: 'none' }} />
      <path d="M161 125l14 14M175 125l-14 14" style={{ stroke: PAPER_STROKE, fill: 'none' }} />
    </IllustrationSvg>
  );
}
UploadFailedIllustration.defaultTitle = 'Upload failed';
UploadFailedIllustration.defaultDescription = 'A page with an upload arrow and a small cross badge';

/** Nothing expiring soon: A calendar page with a check mark: all clear. */
export function NothingExpiringIllustration(props) {
  return (
    <IllustrationSvg {...props}>
      <ellipse cx="120" cy="160" rx="78" ry="8" style={{ fill: FILL, fillOpacity: FILL_OPACITY, stroke: 'none' }} />
      <rect x="62" y="34" width="116" height="112" rx="12" style={{ stroke: LINE, fill: PAPER }} />
      <path d="M62 46a12 12 0 0 1 12-12h92a12 12 0 0 1 12 12v18H62z" style={{ fill: FILL, fillOpacity: FILL_OPACITY, stroke: 'none' }} />
      <path d="M62 64h116M92 24v20M148 24v20" style={{ stroke: LINE, fill: 'none' }} />
      <path d="M98 104l16 16 30-32" style={{ stroke: LINE, fill: 'none' }} />
    </IllustrationSvg>
  );
}
NothingExpiringIllustration.defaultTitle = 'Nothing expiring soon';
NothingExpiringIllustration.defaultDescription = 'A calendar page with a check mark: all clear';
