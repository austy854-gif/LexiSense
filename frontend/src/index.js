import React from "react";
import ReactDOM from "react-dom/client";
import "@/index.css";
import App from "@/App";

// Initialize Sentry
import * as Sentry from "@sentry/react";
import { BrowserTracing } from "@sentry/browser";

const sentryDsn = process.env.REACT_APP_SENTRY_DSN;
if (sentryDsn && !sentryDsn.startsWith("your-")) {
  Sentry.init({
    dsn: sentryDsn,
    integrations: [
      Sentry.browserTracingIntegration(),
      Sentry.reactComponentAnnotationIntegration(),
    ],
    tracesSampleRate: 0.1,
    profilesSampleRate: 0.1,
    environment: process.env.REACT_APP_ENVIRONMENT || "development",
    release: process.env.REACT_APP_RELEASE_VERSION || "2.0.0",
    beforeSend(event) {
      // Filter out non-error events in development
      if (process.env.NODE_ENV === "development" && event.level !== "error") {
        return null;
      }
      return event;
    },
  });
}

const root = ReactDOM.createRoot(document.getElementById("root"));
root.render(
  <React.StrictMode>
    <Sentry.ErrorBoundary fallback={<p>Something went wrong</p>}>
      <App />
    </Sentry.ErrorBoundary>
  </React.StrictMode>
);
