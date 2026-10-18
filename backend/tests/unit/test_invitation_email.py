"""Rendering tests for the team-invite email (services.email_service)."""
import pytest

from services import email_service
from services.email_service import build_accept_url, render_invitation_email

DATA = {
    "inviter_name": "Ada Lovelace",
    "organization_name": "Acme Studio",
    "role": "manager",
    "token": "abc-123_XYZ",
    "invitee_email": "new@example.com",
}


@pytest.fixture(autouse=True)
def _app_url(monkeypatch):
    monkeypatch.setattr(email_service, "APP_URL", "https://app.lexisense.net/")


class TestAcceptUrl:
    def test_strips_trailing_slash_and_uses_accept_invite_route(self):
        assert build_accept_url("tok") == "https://app.lexisense.net/accept-invite?token=tok"

    def test_url_encodes_the_token(self):
        url = build_accept_url("a+b/c=d&e f")
        assert url == "https://app.lexisense.net/accept-invite?token=a%2Bb%2Fc%3Dd%26e%20f"

    def test_strips_multiple_trailing_slashes(self):
        assert build_accept_url("t", app_url="http://x//") == "http://x/accept-invite?token=t"

    def test_url_safe_secrets_token_is_unchanged(self):
        # secrets.token_urlsafe only emits [A-Za-z0-9_-]
        assert build_accept_url("Ab_9-z").endswith("?token=Ab_9-z")


class TestInviteCopy:
    def test_subject_and_copy(self):
        subject, html, text = render_invitation_email(DATA)
        assert subject == "Ada Lovelace invited you to LexiSense"
        assert "You're invited" in html
        assert "Accept invite" in html
        assert "Join Acme Studio and see your team's contracts in one place." in html
        assert "where your team keeps its contracts in one place" in html
        assert "This invite expires in 7 days." in html
        assert "invited new@example.com to LexiSense" in html

    def test_role_label_is_sentence_case(self):
        _, html, text = render_invitation_email(DATA)
        assert ">Manager<" in html
        assert "MANAGER" not in html
        assert "Your role:   Manager" in text

    def test_accept_url_in_html_and_text(self):
        _, html, text = render_invitation_email(DATA)
        url = "https://app.lexisense.net/accept-invite?token=abc-123_XYZ"
        assert f'href="{url}"' in html
        assert f"Accept invite: {url}" in text

    def test_old_template_artifacts_are_gone(self):
        _, html, text = render_invitation_email(DATA)
        for banned in ("⚖", "Contract Lifecycle Management", "#a78bfa",
                       "You're Invited!", "Accept Invitation", "PENDING LAUNCH"):
            assert banned not in html
            assert banned not in text

    def test_account_email_has_no_unsubscribe_rows(self):
        _, html, text = render_invitation_email(DATA)
        for banned in ("nsubscribe", "notification_settings", "Manage email notifications"):
            assert banned not in html
            assert banned not in text

    def test_light_by_default_with_dark_mode_support(self):
        _, html, _ = render_invitation_email(DATA)
        assert 'name="color-scheme" content="light dark"' in html
        assert "background-color:#FCFAF8" in html
        assert "prefers-color-scheme: dark" in html

    def test_no_unfilled_placeholders(self):
        _, html, text = render_invitation_email(DATA)
        assert "{{" not in html and "}}" not in html
        assert "{{" not in text


class TestEscaping:
    def test_inviter_and_org_names_are_html_escaped(self):
        data = dict(DATA, inviter_name='<script>alert(1)</script>',
                    organization_name='Tom & "Jerry" <Co>')
        _, html, text = render_invitation_email(data)
        assert "<script>" not in html
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
        assert "Tom &amp; &quot;Jerry&quot; &lt;Co&gt;" in html
        # Plain text is not HTML: no entity-escaping there.
        assert 'Tom & "Jerry" <Co>' in text

    def test_token_cannot_break_out_of_href(self):
        data = dict(DATA, token='"><img src=x onerror=alert(1)>')
        _, html, _ = render_invitation_email(data)
        assert "<img src=x" not in html
        assert "accept-invite?token=%22%3E%3Cimg%20src%3Dx%20onerror%3Dalert%281%29%3E" in html

    def test_newlines_in_names_cannot_split_the_subject(self):
        data = dict(DATA, inviter_name="Eve\r\nBcc: x@evil.test")
        subject, _, _ = render_invitation_email(data)
        assert "\n" not in subject and "\r" not in subject

    def test_fallbacks_when_names_missing(self):
        subject, html, _ = render_invitation_email(dict(DATA, inviter_name="", organization_name=None))
        assert subject == "A teammate invited you to LexiSense"
        assert "your team" in html


class TestSendEmailPassesPlainText:
    async def test_invitation_send_includes_text_alternative(self, monkeypatch):
        captured = {}

        def fake_send(params, *args):
            captured.update(params)
            return {"id": "em_1"}

        monkeypatch.setattr(email_service, "RESEND_API_KEY", "re_test")
        monkeypatch.setattr(email_service.resend.Emails, "send", fake_send)
        result = await email_service.send_invitation_email(
            to_email="new@example.com", inviter_name="Ada Lovelace",
            organization_name="Acme Studio", role="user", token="tok-1",
        )
        assert result["status"] == "success"
        assert captured["subject"] == "Ada Lovelace invited you to LexiSense"
        assert "Accept invite: https://app.lexisense.net/accept-invite?token=tok-1" in captured["text"]
        assert "invited new@example.com to LexiSense" in captured["text"]
        assert 'href="https://app.lexisense.net/accept-invite?token=tok-1"' in captured["html"]

    async def test_token_is_not_logged_without_api_key(self, monkeypatch, caplog):
        monkeypatch.setattr(email_service, "RESEND_API_KEY", None)
        caplog.set_level("INFO")
        await email_service.send_invitation_email(
            to_email="new@example.com", inviter_name="A", organization_name="B",
            role="user", token="super-secret-token",
        )
        assert "super-secret-token" not in caplog.text


def test_developer_comments_are_not_sent_to_recipients():
    _, html, _ = render_invitation_email(DATA)
    for internal in ("DRAFT", "BLOCKER", "SLOT:", "Cindy", "Placeholders:", "README"):
        assert internal not in html
    # Outlook conditional comments are kept intact.
    assert "<!--[if mso]>" in html
    assert "<!--[if !mso]><!-->" in html
    assert "<!--<![endif]-->" in html
    assert html.count("lexisense-email-header-dark@2x.png") == 1


def test_uses_the_brand_account_invite_template_verbatim():
    from pathlib import Path
    tpl = Path(email_service._TEMPLATE_DIR)
    assert email_service.INVITE_HTML_TEMPLATE == "lexisense-account-invite.html"
    assert (tpl / "lexisense-account-invite.html").is_file()
    assert (tpl / "lexisense-account-invite.txt").is_file()
    assert not (tpl / "account_invite.html").exists()  # interim file removed


def test_plain_text_has_no_header_lines_and_matches_copy():
    subject, _, text = render_invitation_email(DATA)
    assert not text.startswith("Subject:")
    assert "Preview:" not in text
    assert text.startswith("Hi there,")
    assert "See how it works: https://lexisense.net/#how-it-works" in text
    assert subject == "Ada Lovelace invited you to LexiSense"


def test_every_referenced_header_image_is_served_by_the_frontend():
    import re
    from pathlib import Path
    html = (Path(email_service._TEMPLATE_DIR) / "lexisense-account-invite.html").read_text()
    public = Path(__file__).resolve().parents[3] / "frontend" / "public"
    srcs = set(re.findall(r'src="https://lexisense\.net(/[^"]+)"', html))
    assert srcs, "template should reference hosted header images"
    for src in srcs:
        assert (public / src.lstrip("/")).is_file(), src
