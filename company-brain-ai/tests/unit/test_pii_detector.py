"""
Unit tests for PII detector — ADR-0064 M1.

Coverage:
  - All 6 regex kinds (email, phone, ssn, credit_card, ip_address, api_key)
  - Dictionary-based personal_name detection
  - Luhn validation for credit cards
  - Recall ≥ 90% on synthetic PII fixture set
  - Cache correctness (same chunk, no re-scan)
  - Scan time < 50ms P50 per chunk
"""
from __future__ import annotations

import time

import pytest

from companybrain.privacy.pii_detector import PIIFinding, scan, clear_cache
from companybrain.privacy.pii_patterns import luhn_valid


# ── Helpers ───────────────────────────────────────────────────────────────────

def kinds(findings: list[PIIFinding]) -> set[str]:
    return {f.kind for f in findings}


def texts(findings: list[PIIFinding]) -> list[str]:
    return [f.text for f in findings]


# ── Individual kind tests ─────────────────────────────────────────────────────

class TestEmail:
    def test_detects_plain_email(self):
        findings = scan("Send the report to alice@example.com please.")
        assert any(f.kind == "email" and "alice@example.com" in f.text for f in findings)

    def test_detects_subdomain_email(self):
        findings = scan("Contact bob.smith@mail.company.org for details.")
        assert any(f.kind == "email" for f in findings)

    def test_no_false_positive_url(self):
        # A URL should not be detected as an email
        findings = scan("Visit https://example.com/about for info.")
        email_findings = [f for f in findings if f.kind == "email"]
        assert len(email_findings) == 0

    def test_email_confidence_high(self):
        findings = scan("test@domain.com")
        email_findings = [f for f in findings if f.kind == "email"]
        assert email_findings and email_findings[0].confidence >= 0.9


class TestPhone:
    def test_detects_us_phone_dashes(self):
        findings = scan("Call me at 415-555-0123 today.")
        assert any(f.kind == "phone" for f in findings)

    def test_detects_e164(self):
        findings = scan("International: +14155550123")
        assert any(f.kind == "phone" for f in findings)

    def test_detects_us_dots(self):
        findings = scan("Phone: 555.867.5309")
        assert any(f.kind == "phone" for f in findings)


class TestSSN:
    def test_detects_ssn_with_dashes(self):
        findings = scan("SSN: 123-45-6789")
        assert any(f.kind == "ssn" for f in findings)

    def test_no_false_positive_000(self):
        # SSN starting with 000 is invalid
        findings = scan("000-12-3456")
        ssn_findings = [f for f in findings if f.kind == "ssn"]
        assert len(ssn_findings) == 0


class TestCreditCard:
    def test_detects_valid_visa(self):
        # 4111111111111111 is a known test Visa that passes Luhn
        findings = scan("Card: 4111 1111 1111 1111")
        assert any(f.kind == "credit_card" for f in findings)

    def test_no_false_positive_invalid_luhn(self):
        # 4111 1111 1111 1112 does NOT pass Luhn
        findings = scan("Card: 4111 1111 1111 1112")
        cc_findings = [f for f in findings if f.kind == "credit_card"]
        assert len(cc_findings) == 0

    def test_luhn_validator_correct(self):
        assert luhn_valid("4111111111111111") is True
        assert luhn_valid("4111111111111112") is False
        assert luhn_valid("378282246310005") is True  # Amex test number


class TestIPAddress:
    def test_detects_public_ipv4(self):
        findings = scan("Server at 203.0.113.42 is down.")
        assert any(f.kind == "ip_address" and "203.0.113.42" in f.text for f in findings)

    def test_detects_private_ipv4_lower_confidence(self):
        findings = scan("Internal server 192.168.1.100")
        ip_findings = [f for f in findings if f.kind == "ip_address"]
        assert ip_findings  # still detected
        assert ip_findings[0].confidence < 0.9  # but lower confidence


class TestAPIKey:
    def test_detects_anthropic_key(self):
        findings = scan('ANTHROPIC_API_KEY = "sk-ant-api03-AAAA12345678901234"')
        assert any(f.kind == "api_key" for f in findings)

    def test_detects_openai_key(self):
        findings = scan('api_key = "sk-abcdef1234567890abcdef1234567890ab"')
        assert any(f.kind == "api_key" for f in findings)

    def test_detects_aws_key(self):
        findings = scan("AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE")
        assert any(f.kind == "api_key" for f in findings)


class TestPersonalName:
    def test_detects_first_name(self):
        findings = scan("John called to confirm the meeting.")
        assert any(f.kind == "personal_name" and f.text == "John" for f in findings)

    def test_detects_last_name(self):
        findings = scan("Contact Smith at the front desk.")
        assert any(f.kind == "personal_name" and f.text == "Smith" for f in findings)

    def test_no_false_positive_class_name(self):
        # "James" in a Java class context is still detected by dictionary; that's fine
        # but we test that code identifiers not in the dictionary don't trigger it
        findings = scan("class HttpRequestHandler extends BaseHandler {}")
        name_findings = [f for f in findings if f.kind == "personal_name"]
        # None of these tokens appear in our name lists
        assert all(f.text not in {"Http", "Request", "Handler", "Base"} for f in name_findings)


# ── Recall test — ≥ 90% on 10-chunk fixture ──────────────────────────────────

SYNTHETIC_PII_FIXTURES = [
    ("email_basic", "Please reply to user@acme.org by Friday.", {"email"}),
    ("email_complex", "CC: support+ticket@helpdesk.example.com", {"email"}),
    ("phone_us", "Call 800-555-1212 for support.", {"phone"}),
    ("phone_e164", "Overseas contact: +447911123456", {"phone"}),
    ("ssn_dashes", "Employee SSN 234-56-7890 must not be shared.", {"ssn"}),
    ("credit_card_visa", "Charged 4532015112830366 on 2026-01-15.", {"credit_card"}),
    ("ip_public", "Blocked IP 198.51.100.5 from the firewall.", {"ip_address"}),
    ("api_key_anthropic", 'key = "sk-ant-api03-XYZxyz123456789012345"', {"api_key"}),
    ("name_first", "Alice reported the bug.", {"personal_name"}),
    ("name_last", "Mr Johnson will be attending.", {"personal_name"}),
]


def test_recall_at_least_90_percent():
    clear_cache()
    detected = 0
    total = len(SYNTHETIC_PII_FIXTURES)

    for fixture_name, chunk, expected_kinds in SYNTHETIC_PII_FIXTURES:
        findings = scan(chunk)
        found_kinds = {f.kind for f in findings}
        if expected_kinds & found_kinds:  # at least one expected kind found
            detected += 1

    recall = detected / total
    assert recall >= 0.90, (
        f"PII recall {recall:.0%} < 90% "
        f"({detected}/{total} fixtures detected)"
    )


# ── Performance test (< 50ms P50) ─────────────────────────────────────────────

PERF_CHUNK = (
    "Meeting notes: Alice Smith (alice.smith@acme.com, 415-555-0190) and "
    "Bob Johnson discussed the Q3 roadmap. Server IP 10.0.0.42."
)


def test_scan_performance_p50_under_50ms():
    clear_cache()
    N = 20
    times = []
    for _ in range(N):
        clear_cache()
        t0 = time.perf_counter()
        scan(PERF_CHUNK)
        elapsed_ms = (time.perf_counter() - t0) * 1000
        times.append(elapsed_ms)

    times.sort()
    p50 = times[N // 2]
    assert p50 < 50, f"P50 scan time {p50:.1f}ms exceeds 50ms budget"


# ── Cache test ────────────────────────────────────────────────────────────────

def test_cache_returns_same_result():
    clear_cache()
    chunk = "Email: carol@example.com"
    r1 = scan(chunk)
    r2 = scan(chunk)
    # Should be the identical list object (cached)
    assert r1 is r2


def test_cache_different_chunks_not_confused():
    clear_cache()
    r1 = scan("Email: a@example.com")
    r2 = scan("Email: b@different.org")
    assert r1 is not r2
    assert r1[0].text != r2[0].text
