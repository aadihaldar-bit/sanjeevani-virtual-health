import pytest

from app import net_guard, secret_store
from app.net_guard import UnsafeURL, validate_external_url

pytestmark = pytest.mark.asyncio


def resolver_for(*addresses):
    def resolve(host, port, type=None):
        return [(2, 1, 6, "", (a, port)) for a in addresses]
    return resolve


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ("EXTERNAL_ALLOW_HTTP", "EXTERNAL_HOST_ALLOWLIST", "EXTERNAL_HOST_DENYLIST"):
        monkeypatch.delenv(name, raising=False)


# --- URL safety -------------------------------------------------------------------------------

def test_a_public_https_host_is_accepted_and_normalised():
    assert validate_external_url("https://models.example.com/v1/", resolver_for("93.184.216.34")) == "https://models.example.com/v1"


@pytest.mark.parametrize("address", [
    "127.0.0.1", "10.0.0.5", "172.16.3.4", "192.168.1.10", "169.254.169.254", "169.254.10.10", "0.0.0.0",
    "100.64.0.1", "224.0.0.1", "198.18.0.1", "::1", "fe80::1", "fc00::1", "::ffff:10.0.0.1",
])
def test_hosts_that_resolve_to_internal_addresses_are_refused(address):
    with pytest.raises(UnsafeURL):
        validate_external_url("https://innocent.example.com", resolver_for(address))


def test_one_bad_address_among_good_ones_is_enough_to_refuse():
    with pytest.raises(UnsafeURL):
        validate_external_url("https://mixed.example.com", resolver_for("93.184.216.34", "10.1.1.1"))


@pytest.mark.parametrize("url", [
    "http://models.example.com", "ftp://models.example.com", "file:///etc/passwd", "//models.example.com",
    "https://user:pass@models.example.com", "https://", "", "javascript:alert(1)",
])
def test_wrong_schemes_missing_hosts_and_embedded_credentials_are_refused(url):
    with pytest.raises(UnsafeURL):
        validate_external_url(url, resolver_for("93.184.216.34"))


@pytest.mark.parametrize("url", [
    "https://localhost/v1", "https://metadata.google.internal/computeMetadata/v1", "https://thing.internal",
    "https://printer.local", "https://127.0.0.1/v1", "https://[::1]/v1", "https://169.254.169.254/latest/meta-data",
    "https://10.0.0.1", "https://2130706433",       # decimal form of 127.0.0.1
])
def test_obvious_internal_targets_are_refused(url):
    with pytest.raises(UnsafeURL):
        validate_external_url(url, resolver_for("127.0.0.1"))


def test_an_unresolvable_host_is_refused_with_a_clear_message():
    def boom(*a, **k):
        raise OSError("no such host")
    with pytest.raises(UnsafeURL, match="could not be resolved"):
        validate_external_url("https://nowhere.example.com", boom)


def test_plain_http_is_allowed_only_when_explicitly_enabled(monkeypatch):
    monkeypatch.setenv("EXTERNAL_ALLOW_HTTP", "true")
    assert validate_external_url("http://models.example.com", resolver_for("93.184.216.34")) == "http://models.example.com"
    with pytest.raises(UnsafeURL):                       # still never to an internal address
        validate_external_url("http://models.example.com", resolver_for("10.0.0.2"))


def test_allow_list_restricts_hosts_to_listed_names_and_their_subdomains(monkeypatch):
    monkeypatch.setenv("EXTERNAL_HOST_ALLOWLIST", "openai.azure.com, models.acme.com")
    ok = resolver_for("93.184.216.34")
    assert validate_external_url("https://models.acme.com/v1", ok)
    assert validate_external_url("https://eu.openai.azure.com/v1", ok)
    with pytest.raises(UnsafeURL, match="allow-list"):
        validate_external_url("https://evil.example.com", ok)
    with pytest.raises(UnsafeURL):
        validate_external_url("https://notopenai.azure.com.evil.io", ok)


async def test_the_async_variant_does_the_same_check():
    with pytest.raises(UnsafeURL):
        await net_guard.avalidate_external_url("https://x.example.com", resolver_for("192.168.0.1"))


# --- secret store -----------------------------------------------------------------------------------

def test_secrets_round_trip_and_are_not_stored_in_clear(monkeypatch):
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", secret_store.generate_key())
    token = secret_store.encrypt("sk-live-abcdef123456")
    assert "abcdef" not in token and secret_store.decrypt(token) == "sk-live-abcdef123456"
    assert secret_store.encrypt("sk-live-abcdef123456") != token             # random IV: equal secrets differ


def test_without_a_key_the_store_refuses_instead_of_storing_in_clear(monkeypatch):
    monkeypatch.delenv("CREDENTIAL_ENCRYPTION_KEY", raising=False)
    assert not secret_store.is_configured()
    with pytest.raises(secret_store.SecretStoreError, match="CREDENTIAL_ENCRYPTION_KEY"):
        secret_store.encrypt("anything")


def test_a_bad_key_is_reported_clearly(monkeypatch):
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", "not-a-fernet-key")
    with pytest.raises(secret_store.SecretStoreError, match="valid Fernet key"):
        secret_store.encrypt("x")


def test_keys_can_be_rotated_without_losing_old_secrets(monkeypatch):
    old, new = secret_store.generate_key(), secret_store.generate_key()
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", old)
    stored = secret_store.encrypt("the-secret-value")
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", f"{new},{old}")           # new key first, old key still readable
    assert secret_store.decrypt(stored) == "the-secret-value"
    fresh = secret_store.encrypt("the-secret-value")
    monkeypatch.setenv("CREDENTIAL_ENCRYPTION_KEY", new)                      # old key retired
    assert secret_store.decrypt(fresh) == "the-secret-value"
    with pytest.raises(secret_store.SecretStoreError, match="could not be decrypted"):
        secret_store.decrypt(stored)


def test_last4_hint_never_reveals_short_secrets():
    assert secret_store.last4("sk-abcdefgh1234") == "1234"
    assert secret_store.last4("short") == ""
