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
    auth = AuthService(ctx, sender, "x" * 32, monotonic=clock, cooldown=30, max_sends=3, admin_pin="4821")
    return auth, sender, clock


def test_successful_login_returns_role_from_sheet(setup):
    auth, sender, _ = setup
    msg, _ = auth.request_code("Admin@Example.com")
    assert msg == GENERIC_SENT
    p = auth.verify_code("admin@example.com", sender.sent[-1][1], admin_pin="4821")
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
    auth = AuthService(ctx, sender, "y" * 32, bootstrap_admin_email="boss@example.com", admin_pin="4821")
    auth.request_code("boss@example.com")
    assert sender.sent
    assert auth.verify_code("boss@example.com", sender.sent[-1][1], admin_pin="4821").role == "admin"
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
    with pytest.raises(AuthenticationError, match="invite"):
        auth.sign_in_email_only("nobody@example.com")
    with pytest.raises(AuthenticationError, match="disabled"):
        auth.sign_in_email_only("off@example.com")


def test_email_only_attempts_are_rate_limited(setup):
    auth, _, _ = setup
    for _ in range(10):
        with pytest.raises(AuthenticationError):
            auth.sign_in_email_only("nobody@example.com")
    with pytest.raises(RateLimited):
        auth.sign_in_email_only("nobody@example.com")



# ===================================================== account status, registration, admin PIN
from services.auth_service import validate_admin_pin  # noqa: E402
from services.repository import DuplicateError  # noqa: E402
from core.snapshot import load_snapshot  # noqa: E402
from utils.validators import ValidationError  # noqa: E402


def test_account_status_values(setup):
    auth, _, _ = setup
    assert auth.account_status("nobody@example.com") == "new"
    assert auth.account_status("alex@example.com") == "member"
    assert auth.account_status("admin@example.com") == "admin"
    assert auth.account_status("off@example.com") == "inactive"


def test_lookups_do_not_trigger_code_cooldown(setup):
    auth, sender, _ = setup
    auth.account_status("alex@example.com")
    auth.request_code("alex@example.com")  # separate counters: the lookup must not count as a code e-mail
    assert len(sender.sent) == 1


def test_register_creates_user_and_line_for_the_mobile_number(setup, ctx):
    auth, _, _ = setup
    p = auth.register("  New.Person@Example.com ", "(555) 010-7777")
    assert p.role == "user" and p.email == "new.person@example.com"
    snap = load_snapshot(ctx.repo, fresh=True)
    user = next(u for u in snap.users if u.email == "new.person@example.com")
    line = next(x for x in snap.lines if x.user_id == user.user_id)
    assert user.active and user.role == "user" and user.name == "New Person"
    assert line.mobile_number == "5550107777" and line.active and line.line_label == "My phone"
    assert auth.account_status("new.person@example.com") == "member"
    audit = [r for r in ctx.repo.list("AuditLog") if r["action"] == "user.register"]
    assert len(audit) == 1 and "5550107777" not in audit[0]["details"]  # only the last four digits are logged


def test_register_rejects_duplicates_bad_numbers_and_disabled_registration(setup, ctx):
    auth, _, _ = setup
    with pytest.raises(DuplicateError, match="email"):
        auth.register("alex@example.com", "5550109999")
    with pytest.raises(DuplicateError, match="mobile number"):
        auth.register("fresh@example.com", "555-010-0002")           # Alex personal already owns this number
    for bad in ("", "12", "abc", "1" * 20):
        with pytest.raises(ValidationError):
            auth.register("fresh@example.com", bad)
    assert not any(u["email"] == "fresh@example.com" for u in ctx.repo.list("Users"))
    closed = AuthService(ctx, Capture(), "z" * 32, allow_registration=False)
    with pytest.raises(AuthenticationError):
        closed.register("fresh@example.com", "5550108888")


def test_registration_is_capped_per_hour(setup):
    auth, _, clock = setup
    auth.max_registrations_per_hour = 2
    auth.register("a1@example.com", "5550101111")
    auth.register("a2@example.com", "5550102222")
    with pytest.raises(RateLimited):
        auth.register("a3@example.com", "5550103333")
    clock.t += 3601
    auth.register("a3@example.com", "5550103333")


def test_admin_needs_the_pin_and_it_is_enforced_by_the_service(setup):
    auth, _, _ = setup
    with pytest.raises(AuthenticationError, match="Incorrect PIN"):
        auth.sign_in_email_only("admin@example.com")                       # no PIN supplied
    with pytest.raises(AuthenticationError, match="Incorrect PIN"):
        auth.sign_in_email_only("admin@example.com", admin_pin="0000")
    assert auth.sign_in_email_only("admin@example.com", admin_pin=" 4821 ").role == "admin"
    assert auth.sign_in_email_only("alex@example.com").role == "user"      # ordinary users need no PIN


def test_pin_locks_after_repeated_failures_even_for_the_right_pin(setup):
    auth, _, clock = setup
    for _ in range(5):
        with pytest.raises(AuthenticationError):
            auth.check_admin_pin("admin@example.com", "9999")
    with pytest.raises(RateLimited):
        auth.check_admin_pin("admin@example.com", "4821")                  # locked: correct PIN refused too
    clock.t += 901
    auth.check_admin_pin("admin@example.com", "4821")                      # lock expired


def test_admin_sign_in_is_refused_when_no_pin_is_configured(setup, ctx):
    _, sender, _ = setup
    no_pin = AuthService(ctx, sender, "q" * 32)
    with pytest.raises(AuthenticationError, match="no admin PIN"):
        no_pin.sign_in_email_only("admin@example.com", admin_pin="4821")
    assert no_pin.sign_in_email_only("alex@example.com").role == "user"    # others unaffected


def test_pin_validation():
    assert validate_admin_pin("4821") == "4821" and validate_admin_pin(None) is None
    for bad in ("123", "12345", "abcd", "12 4", "0000", "1234"):
        with pytest.raises(ValueError):
            validate_admin_pin(bad)


def test_code_mode_admin_must_supply_pin_and_a_wrong_pin_does_not_burn_the_code(setup):
    auth, sender, _ = setup
    auth.request_code("admin@example.com")
    code = sender.sent[-1][1]
    with pytest.raises(AuthenticationError, match="Incorrect PIN"):
        auth.verify_code("admin@example.com", code, admin_pin="1357")
    assert auth.verify_code("admin@example.com", code, admin_pin="4821").role == "admin"  # same code still valid


def test_code_mode_registration_creates_account_only_after_the_code(setup, ctx):
    auth, sender, _ = setup
    msg, _ = auth.request_registration_code("newbie@example.com", "5550104444")
    assert sender.sent[-1][0] == "newbie@example.com"
    assert not any(u["email"] == "newbie@example.com" for u in ctx.repo.list("Users"))  # nothing yet
    with pytest.raises(AuthenticationError):
        auth.verify_code("newbie@example.com", "000000" if sender.sent[-1][1] != "000000" else "111111")
    assert not any(u["email"] == "newbie@example.com" for u in ctx.repo.list("Users"))
    p = auth.verify_code("newbie@example.com", sender.sent[-1][1])
    assert p.role == "user"
    snap = load_snapshot(ctx.repo, fresh=True)
    assert any(x.mobile_number == "5550104444" and x.user_id == p.user_id for x in snap.lines)


def test_code_mode_registration_refuses_existing_email_and_number(setup):
    auth, _, clock = setup
    with pytest.raises(DuplicateError):
        auth.request_registration_code("alex@example.com", "5550105555")
    clock.t += 31
    with pytest.raises(DuplicateError):
        auth.request_registration_code("other@example.com", "5550100002")


def test_runtime_reads_admin_pin_and_rejects_bad_values():
    from services.repository import InMemoryRepository, StorageError
    from services.runtime import build_runtime

    base = {"secret": "r" * 32}
    ok = build_runtime({"auth": {**base, "admin_pin": "4821"}}, repo=InMemoryRepository())
    assert ok.auth._admin_pin == "4821" and ok.auth.allow_registration is True
    assert build_runtime({"auth": {**base, "admin_pin": 4821}}, repo=InMemoryRepository()).auth._admin_pin == "4821"
    assert build_runtime({"auth": base}, repo=InMemoryRepository()).auth._admin_pin is None
    assert build_runtime({"auth": {**base, "allow_registration": False}}, repo=InMemoryRepository()).auth.allow_registration is False
    for bad in ("CHANGE-ME", "12", "0000"):
        with pytest.raises(StorageError):
            build_runtime({"auth": {**base, "admin_pin": bad}}, repo=InMemoryRepository())


def test_rejected_registration_does_not_use_up_the_code_cooldown(setup):
    auth, sender, _ = setup
    with pytest.raises(DuplicateError):
        auth.request_registration_code("other@example.com", "5550100002")      # number already taken
    auth.request_registration_code("other@example.com", "5550106666")          # corrected number works at once
    assert len(sender.sent) == 1


# ===================================================== configuration lookup (Streamlit secrets or a mounted file)
def test_load_config_prefers_streamlit_secrets():
    from services.config import load_config

    cfg = {"gsheets": {"spreadsheet_id": "x"}}
    assert load_config(cfg, fallback_files=()) is cfg


def test_load_config_falls_back_to_a_mounted_toml_file(tmp_path):
    from services.config import load_config

    f = tmp_path / "secrets.toml"
    f.write_text('[gsheets]\nspreadsheet_id = "abc"\n[auth]\nsecret = "s"\n', encoding="utf-8")
    cfg = load_config({}, fallback_files=(str(f),))             # Streamlit found nothing
    assert cfg["gsheets"]["spreadsheet_id"] == "abc"
    f.write_bytes(b'\xef\xbb\xbf[gsheets]\nspreadsheet_id = "abc"\n')  # a BOM written by Windows editors is tolerated
    assert load_config({}, fallback_files=(str(f),))["gsheets"]["spreadsheet_id"] == "abc"


def test_load_config_error_says_where_it_looked_without_leaking_values(tmp_path):
    from services.config import load_config
    from services.repository import StorageError

    wrong = tmp_path / "wrong.toml"
    wrong.write_text('[auth]\nsecret = "TOP-SECRET-VALUE"\n', encoding="utf-8")
    broken = tmp_path / "broken.toml"
    broken.write_text("[gsheets\n", encoding="utf-8")
    with pytest.raises(StorageError) as e:
        load_config({}, fallback_files=(str(tmp_path / "missing.toml"), str(wrong), str(broken)))
    msg = str(e.value)
    assert "missing.toml: not found" in msg and "sections are ['auth']" in msg and "not valid TOML" in msg
    assert "TOP-SECRET-VALUE" not in msg


def test_load_config_survives_streamlit_secrets_that_raise(tmp_path):
    from services.config import load_config

    class Exploding(dict):
        def __contains__(self, key):
            raise FileNotFoundError("No secrets found")

    f = tmp_path / "s.toml"
    f.write_text('[gsheets]\nspreadsheet_id = "z"\n', encoding="utf-8")
    assert load_config(Exploding(), fallback_files=(str(f),))["gsheets"]["spreadsheet_id"] == "z"
