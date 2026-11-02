# Product scope

LexiSense v1 is a document-based contract workspace for a small team.

In scope:

- Accounts, organisations, and roles (admin, manager, user, viewer)
- PDF and plain-text upload, stored privately, downloadable by the same organisation
- Extracted text shown on the contract, plus Q&A over that text
- AI summary, risks, and dates. Long files are analysed in sections. If the model did not see the whole file, the contract says so. Only real `YYYY-MM-DD` dates are saved onto the contract
- Approval workflow, audit log, alerts, templates, analytics export
- Team invites and email password reset
- Trial limits, Stripe Team billing, a grace window after a failed payment, then read-only

Not in v1: OCR, in-app redlines, custom e-sign, SSO, and the agentic intake/playbook stack (the code is still in the tree, behind flags, and is not mounted unless those flags are turned on).
