"""STORAGE_PROVIDER=s3 with missing credentials must say so clearly.

This shipped as a bare `os.environ["S3_BUCKET"]`, so a deployment that selected
s3 without configuring R2 raised KeyError from whichever caller reached storage
first. In practice that was the health endpoint, so the visible symptom was a
500 on /api/health and a deploy stuck at "in progress" -- with nothing anywhere
saying storage was never configured.
"""
import os

import pytest

from providers import storage

S3_KEYS = ("S3_BUCKET", "S3_ENDPOINT", "S3_ACCESS_KEY", "S3_SECRET_KEY")


@pytest.fixture(autouse=True)
def _isolate_provider(monkeypatch):
    """get_provider() memoises into a module global; reset around each test."""
    monkeypatch.setattr(storage, "_provider", None)
    for key in S3_KEYS + ("STORAGE_PROVIDER", "S3_REGION", "EMERGENT_LLM_KEY"):
        monkeypatch.delenv(key, raising=False)
    yield
    storage._provider = None


def test_s3_without_credentials_names_every_missing_variable():
    os.environ["STORAGE_PROVIDER"] = "s3"

    with pytest.raises(RuntimeError) as exc:
        storage.get_provider()

    message = str(exc.value)
    for key in S3_KEYS:
        assert key in message, f"{key} missing from the error"
    # The operator needs to know the way out, not just what is wrong.
    assert "STORAGE_PROVIDER=null" in message


def test_empty_string_counts_as_missing():
    """A deploy dashboard left blank sets the variable to "", not nothing."""
    os.environ["STORAGE_PROVIDER"] = "s3"
    for key in S3_KEYS:
        os.environ[key] = ""

    with pytest.raises(RuntimeError) as exc:
        storage.get_provider()
    assert "S3_BUCKET" in str(exc.value)


def test_null_provider_runs_without_any_configuration():
    """The state the app is deployed in today: up, with uploads disabled."""
    os.environ["STORAGE_PROVIDER"] = "null"
    assert storage.get_provider().name == "null"


def test_unset_provider_falls_back_to_null_rather_than_failing():
    """A fresh blueprint deploy sets nothing, and must still boot."""
    assert storage.get_provider().name == "null"


def test_s3_is_built_when_fully_configured():
    os.environ["STORAGE_PROVIDER"] = "s3"
    os.environ["S3_BUCKET"] = "haulcheck"
    os.environ["S3_ENDPOINT"] = "https://acct.r2.cloudflarestorage.com"
    os.environ["S3_ACCESS_KEY"] = "key"
    os.environ["S3_SECRET_KEY"] = "secret"

    provider = storage.get_provider()
    assert provider.name == "s3"
