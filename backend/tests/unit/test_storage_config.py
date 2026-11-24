"""Object storage must stop a production process when it is not configured."""

from pathlib import Path

import pytest

from storage_config import StorageConfigError, require_durable_storage

SECRET = "aws-secret-value-do-not-leak"


def _production(**extra):
    env = {"ENVIRONMENT": "production"}
    env.update(extra)
    return env


def test_production_with_bucket_and_keys_starts():
    require_durable_storage(
        _production(
            AWS_ACCESS_KEY_ID="AKIAEXAMPLE",
            AWS_SECRET_ACCESS_KEY=SECRET,
            AWS_S3_BUCKET="lexisense-contracts",
        )
    )


def test_legacy_bucket_name_counts_as_configured():
    require_durable_storage(
        _production(
            AWS_ACCESS_KEY_ID="AKIAEXAMPLE",
            AWS_SECRET_ACCESS_KEY=SECRET,
            S3_BUCKET_NAME="legacy-bucket",
        )
    )


@pytest.mark.parametrize("environment", ["development", "test", "", "  "])
def test_non_production_missing_storage_does_not_raise(environment):
    require_durable_storage({"ENVIRONMENT": environment})


def test_unset_environment_is_development():
    require_durable_storage({})


@pytest.mark.parametrize(
    "env, names",
    [
        ({}, "AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_S3_BUCKET"),
        (
            {"AWS_ACCESS_KEY_ID": "AKIAEXAMPLE", "AWS_SECRET_ACCESS_KEY": SECRET},
            "AWS_S3_BUCKET",
        ),
        (
            {"AWS_S3_BUCKET": "lexisense-contracts"},
            "AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY",
        ),
    ],
)
def test_production_names_what_is_missing_and_not_the_secret(env, names):
    with pytest.raises(StorageConfigError) as exc:
        require_durable_storage(_production(**env))
    message = str(exc.value)
    assert names in message
    assert SECRET not in message
    assert "AKIAEXAMPLE" not in message


@pytest.mark.parametrize("environment", ["production", "prod", "staging", " Production "])
def test_production_like_environments_fail(environment):
    with pytest.raises(StorageConfigError):
        require_durable_storage({"ENVIRONMENT": environment})


def test_blank_values_count_as_missing():
    with pytest.raises(StorageConfigError) as exc:
        require_durable_storage(
            _production(
                AWS_ACCESS_KEY_ID="  ",
                AWS_SECRET_ACCESS_KEY="\n",
                AWS_S3_BUCKET="",
            )
        )
    assert SECRET not in str(exc.value)


def test_server_checks_storage_before_serving():
    source = (Path(__file__).resolve().parents[2] / "server.py").read_text(
        encoding="utf-8"
    )
    assert "require_durable_storage()" in source
