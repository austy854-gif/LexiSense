"""Stripe redirect allow-list, webhook org binding, and production fail-fast."""

from pathlib import Path

import pytest

from stripe_guard import (
    StripeConfigError,
    redirect_allowed,
    require_stripe_config,
    resolve_webhook_org,
)

SECRET = "sk_live_do_not_leak"
WEBHOOK = "whsec_do_not_leak"
APP = "https://app.lexisense.net"


def test_redirect_allows_the_application_origin():
    assert redirect_allowed(f"{APP}/billing?success=true", APP)
    assert redirect_allowed(f"{APP}/billing?canceled=true", f"{APP}/")


def test_redirect_rejects_other_hosts_and_missing_app_url():
    assert not redirect_allowed("https://evil.example/billing", APP)
    assert not redirect_allowed(f"{APP}.evil.example/billing", APP)
    assert not redirect_allowed("https://app.lexisense.net.evil.example/", APP)
    assert not redirect_allowed(f"{APP}/ok", None)
    assert not redirect_allowed(f"{APP}/ok", "  ")
    assert not redirect_allowed("javascript:alert(1)", APP)
    assert not redirect_allowed("https://user:pw@app.lexisense.net/ok", APP)
    assert not redirect_allowed(f"{APP}/ok\\@evil.example", APP)


def test_redirect_rejects_a_different_port():
    assert not redirect_allowed("https://app.lexisense.net:8443/ok", APP)
    assert redirect_allowed("http://localhost:3000/billing", "http://localhost:3000")


def test_webhook_org_must_match_the_stored_customer():
    assert resolve_webhook_org("org-a", "org-a") == "org-a"
    assert resolve_webhook_org("org-a", None) == "org-a"
    assert resolve_webhook_org(None, "org-a") == "org-a"
    assert resolve_webhook_org("org-b", "org-a") is None
    assert resolve_webhook_org(None, None) is None
    assert resolve_webhook_org("  ", "org-a") == "org-a"


def test_production_requires_both_secrets_and_hides_values():
    require_stripe_config(
        {
            "ENVIRONMENT": "production",
            "STRIPE_SECRET_KEY": SECRET,
            "STRIPE_WEBHOOK_SECRET": WEBHOOK,
        }
    )
    with pytest.raises(StripeConfigError) as exc:
        require_stripe_config(
            {"ENVIRONMENT": "prod", "STRIPE_SECRET_KEY": SECRET}
        )
    message = str(exc.value)
    assert "STRIPE_WEBHOOK_SECRET" in message
    assert SECRET not in message
    assert WEBHOOK not in message


def test_test_environment_boots_without_stripe():
    require_stripe_config({"ENVIRONMENT": "test"})
    require_stripe_config({})


def test_server_checks_stripe_before_serving():
    source = (Path(__file__).resolve().parents[2] / "server.py").read_text(
        encoding="utf-8"
    )
    assert "require_stripe_config()" in source
