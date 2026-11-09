import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter, Routes, Route, Link } from 'react-router-dom';
import { RouteErrorBoundary, SectionErrorBoundary } from './SectionErrorBoundary';

jest.mock('@sentry/react', () => ({ captureException: jest.fn() }));

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

/** Always throws while rendering. */
function Boom() {
  throw new Error('render exploded: secret internals');
}

let container;
let root;
let consoleError;
beforeEach(() => {
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
  consoleError = jest.spyOn(console, 'error').mockImplementation(() => {});
});
afterEach(() => {
  act(() => root.unmount());
  container.remove();
  consoleError.mockRestore();
});

test('a throwing child is contained to its section; siblings still render', () => {
  act(() => {
    root.render(
      <div>
        <p data-testid="sibling">Still here</p>
        <SectionErrorBoundary scope="test">
          <Boom />
        </SectionErrorBoundary>
      </div>,
    );
  });
  expect(container.querySelector('[data-testid="sibling"]').textContent).toBe('Still here');
  const fallback = container.querySelector('[data-testid="section-error-fallback"]');
  expect(fallback).not.toBeNull();
  expect(fallback.getAttribute('role')).toBe('alert');
  // The error text is never shown to users.
  expect(container.textContent).not.toContain('secret internals');
  const Sentry = require('@sentry/react');
  expect(Sentry.captureException).toHaveBeenCalledTimes(1);
});

test('changing resetKey clears the error', () => {
  let shouldThrow = true;
  /** Throws until `shouldThrow` is cleared. */
  function Maybe() {
    if (shouldThrow) throw new Error('x');
    return <p data-testid="ok">ok</p>;
  }
  act(() => {
    root.render(<SectionErrorBoundary resetKey="a"><Maybe /></SectionErrorBoundary>);
  });
  expect(container.querySelector('[data-testid="section-error-fallback"]')).not.toBeNull();
  shouldThrow = false;
  act(() => {
    root.render(<SectionErrorBoundary resetKey="b"><Maybe /></SectionErrorBoundary>);
  });
  expect(container.querySelector('[data-testid="ok"]')).not.toBeNull();
});

test('route boundary contains a crashing page and recovers on navigation', () => {
  act(() => {
    root.render(
      <MemoryRouter initialEntries={['/broken']}>
        <nav><Link to="/fine" data-testid="nav-fine">fine</Link></nav>
        <RouteErrorBoundary>
          <Routes>
            <Route path="/broken" element={<Boom />} />
            <Route path="/fine" element={<p data-testid="fine-page">Fine page</p>} />
          </Routes>
        </RouteErrorBoundary>
      </MemoryRouter>,
    );
  });
  expect(container.querySelector('[data-testid="route-error-fallback"]')).not.toBeNull();
  // Content outside the boundary (nav, toasts) survives.
  expect(container.querySelector('[data-testid="nav-fine"]')).not.toBeNull();
  act(() => {
    container.querySelector('[data-testid="nav-fine"]').dispatchEvent(
      new MouseEvent('click', { bubbles: true, cancelable: true, button: 0 }),
    );
  });
  expect(container.querySelector('[data-testid="route-error-fallback"]')).toBeNull();
  expect(container.querySelector('[data-testid="fine-page"]')).not.toBeNull();
});
