import pytest

from publichost.tunnel.config import Settings


def test_max_tunnels_per_ip_from_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PUBLICHOST_MAX_TUNNELS_PER_IP", "5")
    assert Settings.from_env().max_tunnels_per_ip == 5


@pytest.mark.parametrize("value", ["0", "-3"])
def test_max_tunnels_per_ip_is_clamped_to_at_least_one(
    value: str, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("PUBLICHOST_MAX_TUNNELS_PER_IP", value)
    assert Settings.from_env().max_tunnels_per_ip == 1
