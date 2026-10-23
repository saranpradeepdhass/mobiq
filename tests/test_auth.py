import pytest

from services.auth_service import (
    GENERIC_FAIL, GENERIC_SENT, AuthService, RateLimited, resolve_principal,
)
from services.common import AuthenticationError
from tests.conftest import add_user


class Capture:
    def __init__(self):
        self.sent = []

    def send_code(self, email, code, ttl_minutes, app_name):
        self.sent.append((email, code))


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


@pytest.fixture
def setup(world, ctx):
    add_user(world, "u_off", "off@example.com", active=False)
    clock, sender = Clock(), Capture()
    auth = AuthService(ctx, sender, "x" * 32, monotonic=clock, cooldown=30, max_sends=3)
    return auth, sender, clock


def test_successful_login_returns_role_from_sheet(setup):
    auth, sender, _ = setup
    msg, _ = auth.request_code("Admin@Example.com")
    assert msg == GENERIC_SENT
    p = auth.verify_code("admin@example.com", sender.sent[-1][1])
    assert p.role == "admin" and p.user_id == "u_admin"


def test_code_is_single_use(setup):
    auth, sender, _ = setup
    auth.request_code("alex@example.com")
    code = sender.sent[-1][1]
    auth.verify_code("alex@example.com", code)
    with pytest.raises(AuthenticationError):
        auth.verify_code("alex@example.com", code)


def test_wrong_code_and_lockout_after_max_attempts(setup):
    auth, sender, _ = setup
    auth.request_code("alex@example.com")
    good = sender.sent[-1][1]
    bad = "000000" if good != "000000" else "111111"
    for _ in range(5):
        with pytest.raises(AuthenticationError, match=GENERIC_FAIL):
            auth.verify_code("alex@example.com", bad)
    with pytest.raises(AuthenticationError):
        auth.verify_code("alex@example.com", good)  # code burned after too many attempts


def test_code_expires(setup):
    auth, sender, clock = setup
    auth.request_code("alex@example.com")
    clock.t += 601
    with pytest.raises(AuthenticationError):
        auth.verify_code("alex@example.com", sender.sent[-1][1])


def test_unknown_and_inactive_emails_get_same_response_and_no_email(setup):
    auth, sender, _ = setup
    assert auth.request_code("nobody@example.com")[0] == GENERIC_SENT
    assert auth.request_code("off@example.com")[0] == GENERIC_SENT
    assert sender.sent == []
    with pytest.raises(AuthenticationError):
        auth.verify_code("off@example.com", "123456")


def test_inactive_after_code_issued_cannot_sign_in(setup, ctx):
    auth, sender, _ = setup
    auth.request_code("sam@example.com")
    ctx.repo.update("Users", "u_sam", {"active": "FALSE"})
    with pytest.raises(AuthenticationError):
        auth.verify_code("sam@example.com", sender.sent[-1][1])
    assert resolve_principal(ctx, "u_sam") is None


def test_rate_limiting(setup):
    auth, _, clock = setup
    auth.request_code("alex@example.com")
    with pytest.raises(RateLimited):
        auth.request_code("alex@example.com")  # cooldown
    for _ in range(2):
        clock.t += 31
        auth.request_code("alex@example.com")
    clock.t += 31
    with pytest.raises(RateLimited):
        auth.request_code("alex@example.com")  # max sends per window
    clock.t += 1000
    auth.request_code("alex@example.com")  # window passed


def test_codes_are_not_stored_in_plaintext(setup):
    auth, sender, _ = setup
    auth.request_code("alex@example.com")
    code = sender.sent[-1][1]
    assert all(code not in p.digest for p in auth._pending.values())


def test_bootstrap_admin_only_when_no_admin(repo, ctx):
    sender = Capture()
    auth = AuthService(ctx, sender, "y" * 32, bootstrap_admin_email="boss@example.com")
    auth.request_code("boss@example.com")
    assert sender.sent
    assert auth.verify_code("boss@example.com", sender.sent[-1][1]).role == "admin"
    sender2 = Capture()
    auth2 = AuthService(ctx, sender2, "y" * 32, bootstrap_admin_email="other@example.com")
    auth2.request_code("other@example.com")
    assert sender2.sent == []  # an admin already exists, so no second bootstrap


def test_weak_secret_rejected(ctx):
    with pytest.raises(ValueError):
        AuthService(ctx, Capture(), "short")


def test_email_only_sign_in(setup):
    auth, sender, _ = setup
    p = auth.sign_in_email_only("  Alex@Example.com ")
    assert p.user_id == "u_alex" and p.role == "user"
    assert sender.sent == []  # no code is issued


def test_email_only_requires_valid_email_and_active_account(setup):
    from utils.validators import ValidationError

    auth, _, _ = setup
    for bad in ("", "   ", "nope"):
        with pytest.raises(ValidationError):
            auth.sign_in_email_only(bad)
    for unknown in ("nobody@example.com", "off@example.com"):  # unknown and inactive: same message
        with pytest.raises(AuthenticationError) as e:
            auth.sign_in_email_only(unknown)
        assert "invite" in str(e.value)


def test_email_only_attempts_are_rate_limited(setup):
    auth, _, _ = setup
    for _ in range(10):
        with pytest.raises(AuthenticationError):
            auth.sign_in_email_only("nobody@example.com")
    with pytest.raises(RateLimited):
        auth.sign_in_email_only("nobody@example.com")
