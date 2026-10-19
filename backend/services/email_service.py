import asyncio
import html as html_lib
import logging
import re
from pathlib import Path
from typing import Optional
from urllib.parse import quote

import resend

from utils.env import get_env

logger = logging.getLogger(__name__)

RESEND_API_KEY = get_env("RESEND_API_KEY")
SENDER_EMAIL = (
    get_env("SENDER_EMAIL")
    or get_env("RESEND_FROM_EMAIL")  # legacy name
    or "LexiSense <onboarding@resend.dev>"
)
APP_URL = get_env("APP_URL", "")
if not APP_URL:
    logger.warning("APP_URL is not set - links in outgoing emails will be relative/broken")

if RESEND_API_KEY:
    resend.api_key = RESEND_API_KEY


_TEMPLATE_DIR = Path(__file__).resolve().parent / "email_templates"
#: Brand account-email template (lexisense-brand/email-templates), copied verbatim.
INVITE_HTML_TEMPLATE = "lexisense-account-invite.html"
INVITE_TEXT_TEMPLATE = "lexisense-account-invite.txt"

#: Developer comments in the brand templates (provenance, SLOT markers, the
#: placeholder legend). Stripped before sending. Outlook conditional comments
#: (``<!--[if ...]>``, ``<!--<![endif]-->``, and the ``<!-->`` that closes a
#: downlevel-revealed ``<!--[if !mso]><!-->``) are kept.
_DEV_COMMENT = re.compile(r"<!--(?!\[if|<!\[endif\]|>).*?-->[ \t]*\n?", re.DOTALL)
_PLACEHOLDER = re.compile(r"\{\{\s*([a-z_]+)\s*\}\}")

#: Sentence-case role labels for emails (not ``role.upper()``).
ROLE_LABELS = {
    "admin": "Admin",
    "manager": "Manager",
    "user": "User",
    "viewer": "Viewer",
}


def _read_template(name: str) -> str:
    return (_TEMPLATE_DIR / name).read_text(encoding="utf-8")


def _fill(template: str, values: dict, *, escape: bool) -> str:
    """Replace ``{{name}}`` placeholders. HTML output escapes every value.

    An unknown placeholder raises ``KeyError`` so a template/code mismatch
    fails loudly in tests instead of shipping literal braces to users.
    """
    def _sub(match):
        value = str(values[match.group(1)])
        return html_lib.escape(value, quote=True) if escape else value

    return _PLACEHOLDER.sub(_sub, template)


# {{inviter_name}} right after a lowercase word ("because {{inviter_name}}")
# is mid-sentence, so its fallback is lowercase. The brand templates stay
# verbatim; this only renames the placeholder at render time.
_MID_SENTENCE_INVITER = re.compile(r"(?<=[a-z,] )\{\{inviter_name\}\}")


def _split_text_template(text: str) -> tuple[str, str, str]:
    """Split a brand ``.txt`` template into ``(subject, preview, body)``.

    The files start with ``Subject:`` and ``Preview:`` header lines, then a
    blank line, then the plain-text body.
    """
    header, _, body = text.partition("\n\n")
    fields = {}
    for line in header.splitlines():
        key, sep, value = line.partition(":")
        if sep:
            fields[key.strip().lower()] = value.strip()
    return fields["subject"], fields.get("preview", ""), body


def _one_line(value) -> str:
    """Collapse whitespace (incl. CR/LF) so a name can't break the subject line."""
    return " ".join(str(value or "").split())


def build_accept_url(token: str, app_url: Optional[str] = None) -> str:
    """``APP_URL`` (no trailing slash) + ``/accept-invite?token=`` + encoded token.

    Matches the frontend route ``/accept-invite`` (``AcceptInvitePage``).
    """
    base = (APP_URL if app_url is None else app_url).rstrip("/")
    return f"{base}/accept-invite?token={quote(token or '', safe='')}"


def render_invitation_email(data: dict) -> tuple[str, str, str]:
    """Render the team-invite email: ``(subject, html, text)``.

    Copy (Quinn, lexisense-copy/transactional-emails.md) and layout (Jackson,
    lexisense-account-invite.html/.txt) come from the brand templates, which
    are copied verbatim into ``services/email_templates``. Subject and preview
    are the ``Subject:``/``Preview:`` lines of the ``.txt`` file.
    """
    inviter_name = _one_line(data.get("inviter_name"))
    organization_name = _one_line(data.get("organization_name")) or "your team"
    role = (data.get("role") or "user").lower()
    values = {
        # Fallback is "A teammate" at a sentence start (subject, body) and
        # "a teammate" mid-sentence (footer: "because a teammate invited...").
        "inviter_name": inviter_name or "A teammate",
        "inviter_name_mid": inviter_name or "a teammate",
        "organization_name": organization_name,
        "role_label": ROLE_LABELS.get(role, role.capitalize() or "User"),
        "invitee_email": data.get("invitee_email") or "you",
        "accept_url": build_accept_url(data.get("token", "")),
    }
    subject_tpl, _preview_tpl, text_tpl = _split_text_template(_read_template(INVITE_TEXT_TEMPLATE))
    subject = _one_line(_fill(subject_tpl, values, escape=False))
    html_tpl = _DEV_COMMENT.sub("", _read_template(INVITE_HTML_TEMPLATE))
    html_tpl = _MID_SENTENCE_INVITER.sub("{{inviter_name_mid}}", html_tpl)
    text_tpl = _MID_SENTENCE_INVITER.sub("{{inviter_name_mid}}", text_tpl)
    html = _fill(html_tpl, values, escape=True)
    text = _fill(text_tpl, values, escape=False)
    return subject, html, text


def get_email_template(template_type: str, data: dict) -> tuple[str, str]:
    """Generate email subject and HTML content based on template type."""
    
    if template_type == "invitation":
        subject, html, _text = render_invitation_email(data)

    elif template_type == "expiration_alert":
        days_remaining = data.get('days_remaining', 0)
        urgency_color = "#ef4444" if days_remaining <= 7 else "#f59e0b" if days_remaining <= 14 else "#22c55e"
        subject = f"⚠️ Contract Expiring Soon: {data.get('contract_title', 'Contract')}"
        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
        </head>
        <body style="margin: 0; padding: 0; font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #0f1318;">
            <table width="100%" cellpadding="0" cellspacing="0" style="background-color: #0f1318; padding: 40px 20px;">
                <tr>
                    <td align="center">
                        <table width="600" cellpadding="0" cellspacing="0" style="background-color: #1a1f26; border-radius: 12px; overflow: hidden;">
                            <tr>
                                <td style="padding: 40px 40px 30px; text-align: center; border-bottom: 1px solid #2a3038;">
                                    <h1 style="margin: 0; color: #2563eb; font-size: 28px; font-weight: 700;">⚖️ LexiSense</h1>
                                </td>
                            </tr>
                            <tr>
                                <td style="padding: 40px;">
                                    <div style="text-align: center; margin-bottom: 30px;">
                                        <span style="display: inline-block; padding: 8px 20px; background-color: {urgency_color}20; color: {urgency_color}; font-size: 14px; font-weight: 600; border-radius: 20px; border: 1px solid {urgency_color}40;">
                                            ⏰ {days_remaining} days remaining
                                        </span>
                                    </div>
                                    <h2 style="margin: 0 0 20px; color: #f3f4f6; font-size: 20px; font-weight: 600;">Contract Expiration Alert</h2>
                                    <table width="100%" style="background-color: #242a33; border-radius: 8px; margin-bottom: 25px;" cellpadding="20" cellspacing="0">
                                        <tr>
                                            <td>
                                                <p style="margin: 0 0 10px; color: #9ca3af; font-size: 13px;">CONTRACT</p>
                                                <p style="margin: 0 0 15px; color: #f3f4f6; font-size: 18px; font-weight: 600;">{data.get('contract_title', 'Untitled Contract')}</p>
                                                <p style="margin: 0 0 5px; color: #9ca3af; font-size: 13px;">COUNTERPARTY</p>
                                                <p style="margin: 0 0 15px; color: #d1d5db; font-size: 15px;">{data.get('counterparty', 'Not specified')}</p>
                                                <p style="margin: 0 0 5px; color: #9ca3af; font-size: 13px;">EXPIRY DATE</p>
                                                <p style="margin: 0; color: {urgency_color}; font-size: 15px; font-weight: 600;">{data.get('expiry_date', 'Unknown')}</p>
                                            </td>
                                        </tr>
                                    </table>
                                    <table width="100%" cellpadding="0" cellspacing="0">
                                        <tr>
                                            <td align="center">
                                                <a href="{APP_URL}/contracts/{data.get('contract_id', '')}" 
                                                   style="display: inline-block; padding: 14px 40px; background-color: #2563eb; color: #ffffff; text-decoration: none; font-size: 16px; font-weight: 600; border-radius: 8px;">
                                                    Review Contract
                                                </a>
                                            </td>
                                        </tr>
                                    </table>
                                </td>
                            </tr>
                            <tr>
                                <td style="padding: 25px 40px; background-color: #141920; text-align: center;">
                                    <p style="margin: 0; color: #6b7280; font-size: 12px;">
                                        You received this alert because you have contracts expiring soon.
                                    </p>
                                </td>
                            </tr>
                        </table>
                    </td>
                </tr>
            </table>
        </body>
        </html>
        """
    else:
        subject = "LexiSense Notification"
        html = f"<p>{data.get('message', 'You have a notification from LexiSense.')}</p>"
    
    return subject, html


async def send_email(
    to_email: str,
    template_type: str,
    data: dict
) -> Optional[dict]:
    """Send an email using Resend API.

    Logging is deliberately metadata-only (template name, recipient *domain*,
    provider message id): ``data``, the subject, the HTML body and any links
    can carry bearer secrets such as invitation tokens and must never be
    logged -- including on the missing-key and error paths.
    """
    domain = _recipient_domain(to_email)
    if not RESEND_API_KEY:
        logger.warning(
            "RESEND_API_KEY not configured; email not sent (template=%s recipient_domain=%s)",
            template_type, domain,
        )
        return {"status": "logged", "message": "Email logged (API key not configured)"}
    
    try:
        text_content = None
        if template_type == "invitation":
            subject, html_content, text_content = render_invitation_email(data)
        else:
            subject, html_content = get_email_template(template_type, data)

        params = {
            "from": SENDER_EMAIL,
            "to": [to_email],
            "subject": subject,
            "html": html_content
        }
        if text_content:
            params["text"] = text_content
        
        # Run sync SDK in thread to keep FastAPI non-blocking
        email = await asyncio.to_thread(resend.Emails.send, params)
        
        logger.info(
            "Email sent (template=%s recipient_domain=%s message_id=%s)",
            template_type, domain, email.get("id"),
        )
        return {
            "status": "success",
            "message": f"Email sent to ***@{domain}",
            "email_id": email.get("id")
        }
        
    except Exception as e:
        # The provider/exception text can echo the request (recipient, links),
        # so only the exception type is logged or returned.
        logger.error(
            "Failed to send email (template=%s recipient_domain=%s error_type=%s)",
            template_type, domain, type(e).__name__,
        )
        return {
            "status": "error",
            "message": "Email delivery failed"
        }


async def send_invitation_email(
    to_email: str,
    inviter_name: str,
    organization_name: str,
    role: str,
    token: str
) -> Optional[dict]:
    """Send a team invitation email."""
    return await send_email(
        to_email=to_email,
        template_type="invitation",
        data={
            "inviter_name": inviter_name,
            "organization_name": organization_name,
            "role": role,
            "token": token,
            "invitee_email": to_email,
        }
    )


async def send_expiration_alert(
    to_email: str,
    contract_id: str,
    contract_title: str,
    counterparty: str,
    expiry_date: str,
    days_remaining: int
) -> Optional[dict]:
    """Send a contract expiration alert email."""
    return await send_email(
        to_email=to_email,
        template_type="expiration_alert",
        data={
            "contract_id": contract_id,
            "contract_title": contract_title,
            "counterparty": counterparty,
            "expiry_date": expiry_date,
            "days_remaining": days_remaining
        }
    )


async def send_raw_email(to_email: str, subject: str, html: str, tags: Optional[list] = None,
                         idempotency_key: Optional[str] = None) -> dict:
    """Send a pre-rendered email. Unlike :func:`send_email` this RAISES on a
    provider error so callers (e.g. a Celery task) can retry; it returns
    ``{"status": "logged"}`` without sending when RESEND_API_KEY is unset."""
    if not RESEND_API_KEY:
        logger.warning(
            "RESEND_API_KEY not configured; email not sent (template=%s recipient_domain=%s)",
            "raw", _recipient_domain(to_email),
        )
        return {"status": "logged", "email_id": None}
    params = {"from": SENDER_EMAIL, "to": [to_email], "subject": subject, "html": html}
    if tags:
        params["tags"] = tags
    options = {"idempotency_key": idempotency_key} if idempotency_key else None
    email = await asyncio.to_thread(resend.Emails.send, params, options)
    return {"status": "sent", "email_id": (email or {}).get("id")}


def _recipient_domain(address) -> str:
    """Return only the domain of an address (never the local part) for logs."""
    _, sep, domain = str(address or "").rpartition("@")
    return domain.strip().lower() if sep and domain.strip() else "unknown"
