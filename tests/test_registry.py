import pytest

from publichost.tunnel.registry import (
    ExtendLimitError,
    Registry,
    SubdomainPoolExhaustedError,
)


def test_acquire_returns_unique_subdomains():
    registry = Registry("publichost.me")
    domains = {registry.acquire()[0] for _ in range(50)}
    assert len(domains) == 50
    assert all(d.endswith(".publichost.me") for d in domains)
    assert all("-" in d for d in domains)


def test_release_returns_domain_to_pool():
    registry = Registry("publichost.me")
    domain, _token = registry.acquire()
    assert len(registry) == 1
    registry.release(domain)
    assert len(registry) == 0
    # releasing twice is a no-op
    registry.release(domain)
    assert len(registry) == 0


def test_pool_exhaustion_raises(monkeypatch):
    # Deterministic: the generator only ever produces one label, so the second
    # acquire collides on every retry and must exhaust the pool.
    monkeypatch.setattr(
        "publichost.tunnel.registry._generate_label", lambda: "test-fox-123"
    )
    registry = Registry("publichost.me")
    registry.acquire()
    with pytest.raises(SubdomainPoolExhaustedError):
        registry.acquire()


def test_bind_listener_stores_port_and_release_clears_it():
    registry = Registry("publichost.me")
    domain, _token = registry.acquire()
    registry.bind_listener(domain, 12345)
    assert registry.get_listener_port(domain) == 12345
    assert next(iter(registry.active_domains)) == domain
    registry.release(domain)
    assert registry.get_listener_port(domain) is None
    assert len(registry) == 0


def test_active_tunnel_count_tracks_bound_and_live_tunnels():
    registry = Registry("publichost.me")
    domain, _token = registry.acquire()
    # Acquired but no listener bound yet -> not counted.
    assert registry.active_tunnel_count == 0
    registry.bind_listener(domain, 12345)
    assert registry.active_tunnel_count == 1
    # Once expired/closed it drops back to zero even with a bound listener.
    registry.set_status(domain, "expired")
    assert registry.active_tunnel_count == 0
    registry.set_status(domain, "closed")
    assert registry.active_tunnel_count == 0


def test_bind_listener_for_unknown_domain_raises():
    registry = Registry("publichost.me")
    with pytest.raises(KeyError):
        registry.bind_listener("nope.publichost.me", 12345)


def test_bind_listener_twice_raises():
    registry = Registry("publichost.me")
    domain, _token = registry.acquire()
    registry.bind_listener(domain, 12345)
    with pytest.raises(RuntimeError):
        registry.bind_listener(domain, 12346)


def test_extend_within_limits_succeeds():
    registry = Registry("publichost.me", default_lifetime_minutes=120)
    domain, _token = registry.acquire()
    before = registry.get_info(domain).expires_at
    new_expires = registry.extend(
        domain, 30, max_extend_minutes=120, max_total_lifetime_minutes=1440
    )
    assert new_expires > before
    assert registry.get_info(domain).status == "extended"


def test_extend_beyond_single_cap_is_rejected():
    registry = Registry("publichost.me", default_lifetime_minutes=120)
    domain, _token = registry.acquire()
    before = registry.get_info(domain).expires_at
    with pytest.raises(ExtendLimitError):
        registry.extend(
            domain, 121, max_extend_minutes=120, max_total_lifetime_minutes=1440
        )
    # Expiry must be untouched: not silently capped or advanced.
    assert registry.get_info(domain).expires_at == before


def test_extend_beyond_total_lifetime_is_rejected():
    # Initial lifetime already 120 min; total cap 200 min. A 120 min extend
    # (allowed as a single step) would push the aggregate lifetime past 200.
    registry = Registry("publichost.me", default_lifetime_minutes=120)
    domain, _token = registry.acquire()
    before = registry.get_info(domain).expires_at
    with pytest.raises(ExtendLimitError):
        registry.extend(
            domain, 120, max_extend_minutes=120, max_total_lifetime_minutes=200
        )
    assert registry.get_info(domain).expires_at == before


def test_extend_total_lifetime_measured_from_creation():
    registry = Registry("publichost.me", default_lifetime_minutes=60)
    domain, _token = registry.acquire()
    created_at = registry.get_info(domain).created_at
    new_expires = registry.extend(
        domain, 60, max_extend_minutes=120, max_total_lifetime_minutes=120
    )
    # Exactly at the cap is allowed; total lifetime from creation == 120 min.
    assert (new_expires - created_at).total_seconds() == pytest.approx(120 * 60, abs=5)


def test_extend_huge_value_does_not_raise_overflow():
    registry = Registry("publichost.me", default_lifetime_minutes=120)
    domain, _token = registry.acquire()
    # 999999999999 minutes overflows datetime arithmetic; must be reported as a
    # rejected extend, never a raw OverflowError.
    with pytest.raises(ExtendLimitError):
        registry.extend(
            domain,
            999_999_999_999,
            max_extend_minutes=120,
            max_total_lifetime_minutes=1440,
        )


def test_extend_overflow_caught_even_without_single_cap():
    # Belt-and-suspenders: even if a caller bypasses the per-extend cap, the
    # datetime overflow is caught and surfaced as ExtendLimitError.
    registry = Registry("publichost.me", default_lifetime_minutes=120)
    domain, _token = registry.acquire()
    with pytest.raises(ExtendLimitError):
        registry.extend(
            domain,
            10**18,
            max_extend_minutes=10**18,
            max_total_lifetime_minutes=10**18,
        )


def test_extend_unknown_domain_still_raises_key_error():
    registry = Registry("publichost.me")
    with pytest.raises(KeyError):
        registry.extend(
            "nope.publichost.me",
            10,
            max_extend_minutes=120,
            max_total_lifetime_minutes=1440,
        )
