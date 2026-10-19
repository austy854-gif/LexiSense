import { useLayoutEffect } from 'react';
import { useLocation } from 'react-router-dom';

export const SITE_URL = 'https://lexisense.net';

// Only the public landing page is meant to be indexed. Everything else
// (/login, /register, /approvals/respond and every signed-in app route) gets
// <meta name="robots" content="noindex"> and no canonical link.
const INDEXABLE_PATHS = { '/': `${SITE_URL}/` };

function upsertHeadTag(selector, create) {
  let el = document.head.querySelector(selector);
  if (!el) {
    el = create();
    document.head.appendChild(el);
  }
  return el;
}

export function applyRouteHead(pathname) {
  const canonicalHref = INDEXABLE_PATHS[pathname];
  const existingCanonical = document.head.querySelector('link[rel="canonical"]');
  const existingRobots = document.head.querySelector('meta[name="robots"]');

  if (canonicalHref) {
    const link = upsertHeadTag('link[rel="canonical"]', () => {
      const l = document.createElement('link');
      l.setAttribute('rel', 'canonical');
      return l;
    });
    link.setAttribute('href', canonicalHref);
    existingRobots?.remove();
  } else {
    existingCanonical?.remove();
    const robots = upsertHeadTag('meta[name="robots"]', () => {
      const m = document.createElement('meta');
      m.setAttribute('name', 'robots');
      return m;
    });
    robots.setAttribute('content', 'noindex');
  }
}

/** Keeps the canonical link and robots meta in sync with the current route. */
export function RouteHead() {
  const { pathname } = useLocation();
  useLayoutEffect(() => {
    applyRouteHead(pathname);
  }, [pathname]);
  return null;
}
