"""Lock in Sentry's data-collection privacy settings across sentry-sdk versions.

sentry-sdk 2.72 deprecated ``send_default_pii`` in favour of ``data_collection``.
We pass neither option, so the SDK falls back to its "no default PII" behaviour.
These tests check three things:
* the options we pass to ``init`` emit no DeprecationWarning;
* the resolved client config matches the legacy ``send_default_pii=False`` config
  exactly, so collection has not been widened;
* frame locals stay off.
"""
import warnings

import pytest
import sentry_sdk
from sentry_sdk.scrubber import EventScrubber

from utils import sentry_scrub

DSN = "https://public@sentry.invalid/1"


def _client(**extra):
    return sentry_sdk.Client(dsn=DSN, **sentry_scrub.sentry_init_options(), **extra)


def _scrubber_config(scrubber):
    assert isinstance(scrubber, EventScrubber)
    return (sorted(scrubber.denylist), scrubber.recursive)


def test_init_options_do_not_set_deprecated_or_widening_keys():
    opts = sentry_scrub.sentry_init_options()
    assert "send_default_pii" not in opts
    # An explicit data_collection dict fills omitted categories with collect-by-default
    # values and disables the EventScrubber, so it must not be passed.
    assert "data_collection" not in opts
    assert opts["include_local_variables"] is False
    assert opts["max_request_body_size"] == "never"


def test_client_init_emits_no_deprecation_warning():
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        _client()
    deprecations = [w for w in caught if issubclass(w.category, DeprecationWarning)]
    assert not deprecations, [str(w.message) for w in deprecations]


def test_resolved_privacy_matches_legacy_send_default_pii_false():
    client = _client()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        legacy = _client(send_default_pii=False)

    assert client.should_send_default_pii() is False
    assert not client.options.get("send_default_pii")
    assert client.options["include_local_variables"] is False
    assert client.options["max_request_body_size"] == "never"
    assert _scrubber_config(client.options["event_scrubber"]) == _scrubber_config(legacy.options["event_scrubber"])

    if "data_collection" not in client.options:  # sentry-sdk < 2.72: nothing more to compare
        return
    dc = client.options["data_collection"]
    assert dc == legacy.options["data_collection"]
    assert dc["provided_by_user"] is False
    assert dc["user_info"] is False
    assert dc["stack_frame_variables"] is False
    assert dc["database_query_data"] is False
    assert dc["queues"] is False
    assert dc["gen_ai"] == {"inputs": False, "outputs": False}
    assert dc["graphql"] == {"document": False, "variables": False}
    # PII-ish headers/cookies/query params stay on the denylist.
    for terms in (dc["cookies"]["terms"], dc["http_headers"]["request"]["terms"], dc["url_query_params"]["terms"]):
        assert {"forwarded", "-ip", "remote-", "via", "-user"} <= set(terms)


@pytest.mark.filterwarnings("error::DeprecationWarning")
def test_init_is_clean_under_deprecation_errors():
    _client()
