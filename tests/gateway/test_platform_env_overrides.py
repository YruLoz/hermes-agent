"""Invariants for the env -> GatewayConfig bridge that platform plugins declare on their
``PlatformEntry`` (``env_overrides`` / ``env_enable_credentials``, applied by
``gateway.config_env._apply_env_overrides``)."""

import pytest

from agent.secret_scope import reset_secret_scope, set_multiplex_active, set_secret_scope
from gateway.config import Platform, load_gateway_config
from gateway.platform_registry import platform_registry
from hermes_constants import reset_hermes_home_override, set_hermes_home_override

# Core-only platforms keep their env rows in gateway/config_env.py.
_CORE_ONLY = {"local", "api_server", "webhook", "msgraph_webhook", "relay"}


@pytest.mark.parametrize(("token_setting", "expected"), [
    ("    token: profile-bot\n", "profile-bot"),
    ("    token: ${TELEGRAM_PROFILE_TOKEN}\n", "expanded-profile-bot"),
    ("    token: ${env:TELEGRAM_PROFILE_TOKEN}\n", "expanded-profile-bot"),
    ("    token: ${MISSING_TELEGRAM_PROFILE_TOKEN}\n", "shared-bot"),
    ("    token: '   '\n", "shared-bot"),
    ("", "shared-bot"),
])
def test_plugin_platform_token_prefers_resolved_config(tmp_path, monkeypatch, token_setting, expected):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "shared-bot")
    monkeypatch.setenv("TELEGRAM_PROFILE_TOKEN", "expanded-profile-bot")
    monkeypatch.delenv("MISSING_TELEGRAM_PROFILE_TOKEN", raising=False)
    (tmp_path / "config.yaml").write_text(
        "platforms:\n  telegram:\n    enabled: true\n" + token_setting, encoding="utf-8"
    )

    assert load_gateway_config().platforms[Platform.TELEGRAM].token == expected


def test_each_multiplex_profile_retains_its_token_with_shared_env(tmp_path):
    homes = [tmp_path / name for name in ("primary", "secondary")]
    for home in homes:
        home.mkdir()
        (home / "config.yaml").write_text(
            "platforms:\n  telegram:\n    enabled: true\n    token: ${TELEGRAM_PROFILE_TOKEN}\n",
            encoding="utf-8",
        )

    set_multiplex_active(True)
    try:
        for home in [*homes, homes[0]]:
            expected = f"{home.name}-profile-bot"
            home_reset = set_hermes_home_override(str(home))
            secret_reset = set_secret_scope({
                "TELEGRAM_BOT_TOKEN": "shared-bot",
                "TELEGRAM_PROFILE_TOKEN": expected,
            })
            try:
                assert load_gateway_config().platforms[Platform.TELEGRAM].token == expected
            finally:
                reset_secret_scope(secret_reset)
                reset_hermes_home_override(home_reset)
    finally:
        set_multiplex_active(False)


def _entries():
    from hermes_cli.plugins import discover_plugins
    discover_plugins()
    return platform_registry.all_entries()


def test_every_static_plugin_platform_declares_its_env_bridge():
    """A static ``Platform`` member served by a bundled plugin must carry its env rows: without them
    its env-only setup (``TELEGRAM_BOT_TOKEN`` in .env) would silently stop enabling the platform."""
    _entries()
    missing = []
    for member in Platform:
        if member.value in _CORE_ONLY:
            continue
        entry = platform_registry.get(member.value)
        if entry is None or not entry.env_overrides or not entry.env_enable_credentials:
            missing.append(member.value)
    assert missing == []


def test_env_override_secrets_stay_in_terminal_secret_blocklist():
    """The terminal tool's adapter-secret blocklist is built at import time and cannot load adapters,
    so every secret a plugin's env rows read must already be declared where that policy looks."""
    from hermes_cli.config import PLATFORM_SECRET_ENV_SUFFIXES
    from hermes_cli.profile_channels import config_env_table_keys
    from tools.environments.local_env_policy import _ADAPTER_SECRET_ENV

    secrets = {name.upper() for name in config_env_table_keys(_entries())
               if name.upper().endswith(PLATFORM_SECRET_ENV_SUFFIXES)}
    assert "WEIXIN_TOKEN" in secrets  # the rows were actually read
    assert sorted(secrets - _ADAPTER_SECRET_ENV) == []
