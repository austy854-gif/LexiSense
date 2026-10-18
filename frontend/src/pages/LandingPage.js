import { useCallback, useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  ArrowRight,
  CalendarClock,
  FileSearch,
  Flag,
  FolderOpen,
  ListChecks,
  Menu,
  Search,
  UserCheck,
  X,
} from 'lucide-react';

// Public marketing page shown at "/" to logged-out visitors.
// It always renders in light mode: the `lexi-landing` class (index.css)
// re-declares the light design tokens on this subtree, so it does not touch
// ThemeContext or the class on <html> that the app uses for dark mode.

const SIGNUP_PATH = '/register';
const LOGIN_PATH = '/login';

const PAGE_TITLE = 'LexiSense: Contract management for small agencies';
const PAGE_DESCRIPTION =
  "Keep every contract in one place, see what's expiring soon, and let AI pull out the key terms. Built for small teams with no legal department.";

const NAV_LINKS = [
  { href: '#benefits', label: 'Why LexiSense' },
  { href: '#features', label: 'Features' },
  { href: '#how-it-works', label: 'How it works' },
  { href: '#faq', label: 'FAQ' },
];

// Copy: /workspace/lexisense-copy/landing-page-copy.md (hero option A), verbatim.
// Copy rules (brand guide, section 2): never claim OCR or scanned-PDF support
// (text is read from text-based PDF and .txt files only), risk is
// contract-level only (riskLevel; never clause-level), never promise
// automatic reminder or approval emails, and no invented stats, testimonials
// or pricing.
const FEATURES = [
  {
    icon: FolderOpen,
    title: 'All your contracts in one place',
    body: 'Upload your agreements and keep them together, instead of spread across inboxes and drives.',
  },
  {
    icon: Search,
    title: 'Find any contract in seconds',
    body: 'Search by title or counterparty, then filter by status, risk level or expiry date.',
  },
  {
    icon: CalendarClock,
    title: 'Renewals at a glance',
    body: "See which contracts expire in the next 7, 30 or 90 days, so you can deal with them while there's still time.",
  },
  {
    icon: FileSearch,
    title: 'AI contract analysis',
    body: "Upload a contract and LexiSense tells you what's in it: the key terms and the dates that matter.",
  },
  {
    icon: Flag,
    title: 'Know which contracts need a closer look',
    body: 'LexiSense flags higher-risk contracts, so you can see which ones need attention and which are fine as they are.',
  },
  {
    icon: UserCheck,
    title: "Approvals that don't get stuck",
    body: 'Request an approval in LexiSense and see who signed off and when, without a chain of \u201cdid you see this?\u201d messages.',
  },
];

const STEPS = [
  {
    title: 'Upload your contracts',
    body: 'Add your contracts as PDF files.',
  },
  {
    title: "See what's in them",
    body: 'LexiSense pulls out the key terms and flags higher-risk contracts.',
  },
  {
    title: "Stay on top of what's next",
    body: "Check what's expiring soon, find any contract in seconds, and send contracts for approval when they need sign-off.",
  },
];

const FAQS = [
  {
    q: 'Who is LexiSense for?',
    a: 'Small agencies and service firms, roughly 3 to 30 people, that handle contracts without a legal department or a dedicated contracts manager.',
  },
  {
    q: 'Do I need a lawyer or legal background to use it?',
    a: "No. LexiSense explains your contracts in plain language. It isn't legal advice, but it gives you a clear picture of what you've signed, so you know when it's worth asking a lawyer.",
  },
  {
    q: 'What kinds of files can I upload?',
    a: "PDFs with selectable text, like the ones you export from Word or Google Docs or get back from an e-signature tool. Scanned images of paper contracts can't be read.",
  },
  {
    q: 'How does LexiSense help with renewals?',
    a: "LexiSense shows which contracts expire in the next 7, 30 or 90 days, and the Dashboard keeps a count of what's expiring soon, so upcoming renewals and end dates are always in view instead of buried in a PDF.",
  },
  {
    q: 'How do approvals work?',
    a: 'When a contract needs sign-off, you request an approval in LexiSense. The approver reviews it there, and the decision is recorded with the contract, so everyone can see where it stands.',
  },
];

function Logo({ className = 'h-8 w-auto' }) {
  return (
    <img
      src={`${process.env.PUBLIC_URL || ''}/brand/lexisense-lockup-light.svg`}
      alt="LexiSense"
      width="132"
      height="32"
      className={className}
    />
  );
}

const MOBILE_MENU_ID = 'landing-mobile-menu';
// Matches Tailwind's `md` breakpoint, where the inline nav takes over.
const DESKTOP_QUERY = '(min-width: 768px)';

const focusRing = 'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring';

function SiteHeader() {
  const [menuOpen, setMenuOpen] = useState(false);
  const toggleRef = useRef(null);

  const closeMenu = useCallback(({ restoreFocus = false } = {}) => {
    setMenuOpen(false);
    if (restoreFocus) toggleRef.current?.focus();
  }, []);

  // Escape closes the menu and returns focus to the toggle; growing past the
  // md breakpoint closes it so it can't linger behind the desktop nav.
  useEffect(() => {
    if (!menuOpen) return undefined;
    const onKeyDown = (event) => {
      if (event.key === 'Escape') closeMenu({ restoreFocus: true });
    };
    const mql = window.matchMedia ? window.matchMedia(DESKTOP_QUERY) : null;
    const onChange = (event) => {
      if (event.matches) closeMenu();
    };
    document.addEventListener('keydown', onKeyDown);
    mql?.addEventListener?.('change', onChange);
    return () => {
      document.removeEventListener('keydown', onKeyDown);
      mql?.removeEventListener?.('change', onChange);
    };
  }, [menuOpen, closeMenu]);

  return (
    <header className="sticky top-0 z-40 border-b border-border bg-background/95 backdrop-blur supports-[backdrop-filter]:bg-background/80">
      <div className="mx-auto flex h-16 max-w-6xl items-center justify-between gap-3 px-4 sm:px-6">
        <a href="#top" className={`flex shrink-0 items-center rounded-md ${focusRing}`} onClick={() => closeMenu()}>
          <Logo className="h-7 w-auto sm:h-8" />
        </a>
        <nav aria-label="Main" className="hidden md:block">
          <ul className="flex items-center gap-7 text-sm font-medium text-muted-foreground">
            {NAV_LINKS.map((link) => (
              <li key={link.href}>
                <a href={link.href} className={`rounded transition-colors hover:text-foreground ${focusRing}`}>
                  {link.label}
                </a>
              </li>
            ))}
          </ul>
        </nav>
        <div className="flex items-center gap-2 sm:gap-4">
          <Link
            to={LOGIN_PATH}
            className={`hidden rounded-md px-2 py-2 text-sm font-semibold text-foreground hover:text-primary md:inline-flex ${focusRing}`}
          >
            Log in
          </Link>
          <Link
            to={SIGNUP_PATH}
            className={`hidden items-center rounded-md bg-primary px-3.5 py-2 text-sm font-semibold text-primary-foreground hover:bg-[hsl(216_85%_29%)] min-[380px]:inline-flex ${focusRing} focus-visible:ring-offset-2`}
          >
            Try LexiSense free
          </Link>
          <button
            ref={toggleRef}
            type="button"
            className={`inline-flex h-10 w-10 items-center justify-center rounded-md border border-[hsl(var(--input))] bg-card text-foreground hover:bg-accent md:hidden ${focusRing} focus-visible:ring-offset-2`}
            aria-expanded={menuOpen}
            aria-controls={MOBILE_MENU_ID}
            aria-label={menuOpen ? 'Close menu' : 'Open menu'}
            onClick={() => setMenuOpen((open) => !open)}
          >
            {menuOpen ? <X className="h-5 w-5" aria-hidden="true" /> : <Menu className="h-5 w-5" aria-hidden="true" />}
          </button>
        </div>
      </div>

      {/* Mobile menu (below 768px). `hidden` keeps it out of the tab order and
          the accessibility tree while closed. */}
      <nav
        id={MOBILE_MENU_ID}
        aria-label="Main"
        hidden={!menuOpen}
        className="border-t border-border bg-background md:hidden"
      >
        <ul className="mx-auto max-w-6xl space-y-1 px-4 py-3 sm:px-6">
          {NAV_LINKS.map((link) => (
            <li key={link.href}>
              <a
                href={link.href}
                onClick={() => closeMenu()}
                className={`block rounded-md px-3 py-3 text-base font-medium text-foreground hover:bg-accent ${focusRing}`}
              >
                {link.label}
              </a>
            </li>
          ))}
          <li className="border-t border-border pt-2">
            <Link
              to={LOGIN_PATH}
              onClick={() => closeMenu()}
              className={`block rounded-md px-3 py-3 text-base font-semibold text-foreground hover:bg-accent ${focusRing}`}
            >
              Log in
            </Link>
          </li>
          <li>
            <Link
              to={SIGNUP_PATH}
              onClick={() => closeMenu()}
              className={`block rounded-md bg-primary px-3 py-3 text-center text-base font-semibold text-primary-foreground hover:bg-[hsl(216_85%_29%)] ${focusRing} focus-visible:ring-offset-2`}
            >
              Try LexiSense free
            </Link>
          </li>
        </ul>
      </nav>
    </header>
  );
}

function PrimaryCta({ children, className = '' }) {
  return (
    <Link
      to={SIGNUP_PATH}
      className={`inline-flex items-center justify-center gap-2 rounded-md bg-primary px-5 py-3 text-base font-semibold text-primary-foreground shadow-sm transition-colors hover:bg-[hsl(216_85%_29%)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 ${className}`}
    >
      {children}
    </Link>
  );
}

function HeroIllustration() {
  // Decorative product sketch. No real data, numbers, or customer names.
  const rows = [
    { status: 'Approved', tone: 'bg-[hsl(152_55%_30%/0.12)] text-[hsl(152_55%_26%)]' },
    { status: 'In review', tone: 'bg-accent text-accent-foreground' },
    { status: 'Draft', tone: 'bg-muted text-muted-foreground' },
  ];
  return (
    <div aria-hidden="true" className="relative mx-auto w-full max-w-md">
      <div className="rounded-2xl border border-border bg-card p-5 shadow-[0_20px_50px_-20px_rgba(13,74,165,0.35)]">
        <div className="flex items-center justify-between">
          <div className="h-3 w-28 rounded-full bg-foreground/80" />
          <div className="flex items-center gap-2 rounded-full border border-[hsl(var(--input))] px-3 py-1.5">
            <Search className="h-3.5 w-3.5 text-muted-foreground" />
            <div className="h-2 w-16 rounded-full bg-muted-foreground/30" />
          </div>
        </div>

        <div className="mt-5 grid grid-cols-3 gap-2">
          {['7 days', '30 days', '90 days'].map((label, i) => (
            <div
              key={label}
              className={`rounded-lg px-3 py-3 ${i === 0 ? 'bg-[hsl(var(--highlight))] text-[hsl(var(--highlight-foreground))]' : 'bg-muted text-foreground'}`}
            >
              <CalendarClock className="h-4 w-4" />
              <div className="mt-2 text-xs font-semibold">Next {label}</div>
            </div>
          ))}
        </div>

        <ul className="mt-5 space-y-2.5">
          {rows.map((row) => (
            <li key={row.status} className="flex items-center gap-3 rounded-lg border border-border px-3 py-3">
              <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md bg-accent">
                <FolderOpen className="h-4 w-4 text-primary" />
              </div>
              <div className="flex-1 space-y-1.5">
                <div className="h-2.5 w-3/4 rounded-full bg-foreground/70" />
                <div className="h-2 w-1/2 rounded-full bg-muted-foreground/30" />
              </div>
              <span className={`rounded-full px-2.5 py-1 text-xs font-semibold ${row.tone}`}>{row.status}</span>
            </li>
          ))}
        </ul>
      </div>

      <div className="absolute -bottom-6 -left-4 hidden w-56 rounded-xl border border-border bg-card p-4 shadow-lg sm:block">
        <div className="flex items-center gap-2 text-sm font-semibold text-foreground">
          <ListChecks className="h-4 w-4 text-primary" />
          Needs a closer look
        </div>
        <div className="mt-3 space-y-1.5">
          <div className="h-2 w-full rounded-full bg-muted-foreground/25" />
          <div className="h-2 w-5/6 rounded-full bg-muted-foreground/25" />
          <div className="h-2 w-2/3 rounded-full bg-muted-foreground/25" />
        </div>
      </div>
    </div>
  );
}

export default function LandingPage() {
  useEffect(() => {
    const previousTitle = document.title;
    document.title = PAGE_TITLE;
    let meta = document.querySelector('meta[name="description"]');
    const created = !meta;
    if (!meta) {
      meta = document.createElement('meta');
      meta.setAttribute('name', 'description');
      document.head.appendChild(meta);
    }
    const previousDescription = meta.getAttribute('content');
    meta.setAttribute('content', PAGE_DESCRIPTION);
    return () => {
      document.title = previousTitle;
      if (created) meta.remove();
      else if (previousDescription !== null) meta.setAttribute('content', previousDescription);
    };
  }, []);

  return (
    <div className="lexi-landing min-h-screen bg-background font-sans text-foreground antialiased">
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-50 focus:rounded-md focus:bg-card focus:px-4 focus:py-2 focus:text-primary focus:shadow"
      >
        Skip to content
      </a>

      <SiteHeader />

      <main id="main">
        {/* Hero */}
        <section id="top" aria-labelledby="hero-heading" className="overflow-hidden">
          <div className="mx-auto grid max-w-6xl items-center gap-14 px-4 pb-20 pt-14 sm:px-6 md:pt-20 lg:grid-cols-[1.1fr_1fr] lg:gap-12 lg:pb-28">
            <div>
              <h1
                id="hero-heading"
                className="text-4xl font-bold leading-[1.1] tracking-[-0.02em] text-foreground sm:text-5xl lg:text-[3.5rem]"
              >
                See every renewal coming. Stop digging for contracts.
              </h1>
              <p className="mt-6 max-w-xl text-lg leading-relaxed text-muted-foreground">
                LexiSense keeps your contracts in one place, reads them for you, and shows what&rsquo;s expiring in the
                next 7, 30 or 90 days. Built for small agencies and service firms without a legal department.
              </p>
              <div className="mt-8 flex flex-col gap-3 sm:flex-row sm:items-center">
                <PrimaryCta>
                  Try LexiSense free
                  <ArrowRight className="h-4 w-4" aria-hidden="true" />
                </PrimaryCta>
                <a
                  href="#how-it-works"
                  className="inline-flex items-center justify-center rounded-md border border-[hsl(var(--input))] bg-card px-5 py-3 text-base font-semibold text-foreground transition-colors hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
                >
                  See how it works
                </a>
              </div>
            </div>
            <HeroIllustration />
          </div>
        </section>

        {/* Problem / benefit */}
        <section id="benefits" aria-labelledby="benefits-heading" className="border-y border-border bg-card">
          <div className="mx-auto grid max-w-6xl gap-10 px-4 py-20 sm:px-6 lg:grid-cols-[1fr_1.3fr] lg:gap-16">
            <h2 id="benefits-heading" className="text-3xl font-bold tracking-[-0.01em] sm:text-4xl">
              Contracts shouldn&rsquo;t live in one person&rsquo;s head
            </h2>
            <div className="space-y-5 text-lg leading-relaxed text-muted-foreground">
              <p>
                Most small firms keep contracts the same way: some in inboxes, some in a shared drive, and a few PDFs saved to
                someone&rsquo;s desktop. It works until a vendor agreement rolls over for another year, or
                someone spends half an hour hunting for the signed version of a client contract.
              </p>
              <p>
                Nobody&rsquo;s being careless. You just don&rsquo;t have a contracts manager, and everyone&rsquo;s
                already doing five jobs.
              </p>
              <p className="text-foreground">
                LexiSense gives you a simple system instead. Every contract goes in one place. LexiSense reads each one
                and tells you what&rsquo;s in it. And you can see what&rsquo;s expiring soon at a glance, so renewals
                stop sneaking up on you.
              </p>
            </div>
          </div>
        </section>

        {/* Features */}
        <section id="features" aria-labelledby="features-heading">
          <div className="mx-auto max-w-6xl px-4 py-20 sm:px-6">
            <div className="max-w-2xl">
              <h2 id="features-heading" className="text-3xl font-bold tracking-[-0.01em] sm:text-4xl">
                What LexiSense does
              </h2>
              <p className="mt-4 text-lg leading-relaxed text-muted-foreground">
                The basics, done well. No long setup and no consultants.
              </p>
            </div>
            <ul className="mt-12 grid gap-6 sm:grid-cols-2 lg:grid-cols-3">
              {FEATURES.map(({ icon: Icon, title, body }) => (
                <li key={title} className="rounded-xl border border-border bg-card p-6 shadow-sm">
                  <div className="flex h-11 w-11 items-center justify-center rounded-lg bg-accent">
                    <Icon className="h-5 w-5 text-primary" aria-hidden="true" />
                  </div>
                  <h3 className="mt-5 text-lg font-semibold text-foreground">{title}</h3>
                  <p className="mt-2 leading-relaxed text-muted-foreground">{body}</p>
                </li>
              ))}
            </ul>
          </div>
        </section>

        {/* How it works */}
        <section id="how-it-works" aria-labelledby="how-heading" className="border-y border-border bg-card">
          <div className="mx-auto max-w-6xl px-4 py-20 sm:px-6">
            <h2 id="how-heading" className="text-3xl font-bold tracking-[-0.01em] sm:text-4xl">
              Get started in 3 steps
            </h2>
            <ol className="mt-12 grid gap-6 md:grid-cols-3">
              {STEPS.map((step, i) => (
                <li key={step.title} className="relative rounded-xl border border-border bg-background p-6">
                  <span
                    className="flex h-10 w-10 items-center justify-center rounded-full bg-primary text-base font-bold text-primary-foreground tabular-nums"
                    aria-hidden="true"
                  >
                    {i + 1}
                  </span>
                  <h3 className="mt-5 text-lg font-semibold text-foreground">
                    <span className="sr-only">Step {i + 1}: </span>
                    {step.title}
                  </h3>
                  <p className="mt-2 leading-relaxed text-muted-foreground">{step.body}</p>
                </li>
              ))}
            </ol>
          </div>
        </section>

        {/* FAQ */}
        <section id="faq" aria-labelledby="faq-heading">
          <div className="mx-auto max-w-3xl px-4 py-20 sm:px-6">
            <h2 id="faq-heading" className="text-3xl font-bold tracking-[-0.01em] sm:text-4xl">
              Questions you might have
            </h2>
            <div className="mt-10 divide-y divide-border rounded-xl border border-border bg-card">
              {FAQS.map((item) => (
                <details key={item.q} className="group px-6 py-5">
                  <summary className="flex cursor-pointer list-none items-center justify-between gap-4 rounded text-left text-base font-semibold text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring [&::-webkit-details-marker]:hidden">
                    <h3 className="text-base font-semibold">{item.q}</h3>
                    <span
                      aria-hidden="true"
                      className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-accent text-primary transition-transform group-open:rotate-45"
                    >
                      +
                    </span>
                  </summary>
                  <p className="mt-3 leading-relaxed text-muted-foreground">{item.a}</p>
                </details>
              ))}
            </div>
          </div>
        </section>

        {/* Final CTA */}
        <section aria-labelledby="cta-heading" className="px-4 pb-20 sm:px-6">
          <div className="mx-auto max-w-6xl rounded-2xl bg-primary px-6 py-14 text-center text-primary-foreground sm:px-12">
            <h2 id="cta-heading" className="text-3xl font-bold tracking-[-0.01em] sm:text-4xl">
              Get your contracts out of the shared drive
            </h2>
            <p className="mx-auto mt-4 max-w-xl text-lg leading-relaxed text-white/90">
              Upload a few contracts and see what LexiSense finds.
            </p>
            <div className="mt-8 flex flex-col items-center justify-center gap-3 sm:flex-row">
              <Link
                to={SIGNUP_PATH}
                className="inline-flex items-center justify-center gap-2 rounded-md bg-white px-5 py-3 text-base font-semibold text-primary shadow-sm transition-colors hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white focus-visible:ring-offset-2 focus-visible:ring-offset-primary"
              >
                Try LexiSense free
                <ArrowRight className="h-4 w-4" aria-hidden="true" />
              </Link>
              <a
                href="#how-it-works"
                className="inline-flex items-center justify-center rounded-md border border-white/80 px-5 py-3 text-base font-semibold text-white transition-colors hover:bg-white/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white focus-visible:ring-offset-2 focus-visible:ring-offset-primary"
              >
                See how it works
              </a>
            </div>
          </div>
        </section>
      </main>

      {/* Footer */}
      <footer className="border-t border-border bg-muted">
        <div className="mx-auto flex max-w-6xl flex-col gap-6 px-4 py-10 sm:px-6 md:flex-row md:items-center md:justify-between">
          <div>
            <Logo className="h-7 w-auto" />
          </div>
          <nav aria-label="Footer">
            <ul className="flex flex-wrap items-center gap-x-6 gap-y-2 text-sm font-medium text-muted-foreground">
              <li>
                <a href="#features" className={`rounded hover:text-foreground ${focusRing}`}>Features</a>
              </li>
              <li>
                <a href="#faq" className={`rounded hover:text-foreground ${focusRing}`}>FAQ</a>
              </li>
              <li>
                <Link to={LOGIN_PATH} className={`rounded hover:text-foreground ${focusRing}`}>Log in</Link>
              </li>
              <li>
                <Link to={SIGNUP_PATH} className={`rounded hover:text-foreground ${focusRing}`}>Sign up</Link>
              </li>
            </ul>
          </nav>
        </div>
        <div className="border-t border-border">
          <div className="mx-auto flex max-w-6xl flex-col gap-1 px-4 py-5 text-sm text-muted-foreground sm:flex-row sm:justify-between sm:px-6">
            <p>&copy; {new Date().getFullYear()} LexiSense</p>
            <p>
              <a href="https://lexisense.net" className={`rounded underline-offset-2 hover:text-foreground hover:underline ${focusRing}`}>
                lexisense.net
              </a>
            </p>
          </div>
        </div>
      </footer>
    </div>
  );
}
