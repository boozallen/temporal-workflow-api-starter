# Copyright 2026 Booz Allen Hamilton Inc.
# SPDX-License-Identifier: Apache-2.0
"""
Unit tests for configuration management
"""

import os
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from foundry_agent_core import _MAX_DICT_BYTES

from src.config import (
    MIN_API_AUTH_TOKEN_LENGTH,
    AppConfig,
    assert_mock_mode_safe,
    is_mock_mode_enabled,
)


class TestAppConfig:
    """Test AppConfig class functionality"""

    @patch.dict(os.environ, {}, clear=True)
    def test_default_configuration(self):
        """Test that AppConfig uses correct default values"""
        config = AppConfig(_env_file=None)

        # Verify default API configuration
        assert config.api_host == "127.0.0.1"
        assert config.api_port == 8000
        assert config.log_level == "INFO"

        # Verify default Temporal configuration
        assert config.temporal_host == "localhost:7233"
        assert config.temporal_namespace == "default"
        assert config.temporal_task_queue == "workflow-task-queue"

    @patch.dict(
        os.environ,
        {
            "API_HOST": "127.0.0.1",
            "API_PORT": "9000",
            "LOG_LEVEL": "DEBUG",
            "TEMPORAL_HOST": "prod-temporal:7233",
            "TEMPORAL_NAMESPACE": "production",
            "TEMPORAL_TASK_QUEUE": "prod-queue",
        },
    )
    def test_environment_variable_override(self):
        """Test that environment variables override defaults"""
        config = AppConfig()

        # Verify environment variables are used
        assert config.api_host == "127.0.0.1"
        assert config.api_port == 9000
        assert config.log_level == "DEBUG"
        assert config.temporal_host == "prod-temporal:7233"
        assert config.temporal_namespace == "production"
        assert config.temporal_task_queue == "prod-queue"

    def test_case_insensitive_environment_variables(self):
        """Test that environment variables are case insensitive"""
        with patch.dict(
            os.environ,
            {
                "api_host": "127.0.0.1",  # lowercase
                "TEMPORAL_HOST": "LOCALHOST:7233",  # uppercase
            },
        ):
            config = AppConfig()
            assert config.api_host == "127.0.0.1"
            assert config.temporal_host == "LOCALHOST:7233"

    @patch.dict(os.environ, {"API_PORT": "invalid_number"})
    def test_invalid_environment_variable_type(self):
        """Test that invalid type in environment variable raises validation error"""
        with pytest.raises(ValueError):
            AppConfig()

    def test_configuration_serialization(self):
        """Test that configuration can be serialized to dict"""
        config = AppConfig(
            api_host="test-host", api_port=9000, temporal_host="test-temporal:7233"
        )

        config_dict = config.model_dump()

        # Verify key fields are present
        assert config_dict["api_host"] == "test-host"
        assert config_dict["api_port"] == 9000
        assert config_dict["temporal_host"] == "test-temporal:7233"
        assert "temporal_namespace" in config_dict
        assert "temporal_task_queue" in config_dict

    def test_field_descriptions_exist(self):
        """Test that all fields have proper descriptions"""
        # Get field info from the model class
        field_info = AppConfig.model_fields

        # Check that key fields have descriptions
        assert "description" in str(field_info["api_host"])
        assert "description" in str(field_info["api_port"])
        assert "description" in str(field_info["temporal_host"])
        assert "description" in str(field_info["temporal_namespace"])

    def test_configuration_for_api_startup(self):
        """Test that configuration provides all required values for API startup"""
        config = AppConfig()

        # Verify all required fields for API startup are available
        assert config.api_host is not None
        assert config.api_port is not None
        assert config.log_level is not None
        assert config.temporal_host is not None
        assert config.temporal_namespace is not None
        assert config.temporal_task_queue is not None

        # Verify types are correct
        assert isinstance(config.api_host, str)
        assert isinstance(config.api_port, int)
        assert isinstance(config.log_level, str)
        assert isinstance(config.temporal_host, str)
        assert isinstance(config.temporal_namespace, str)
        assert isinstance(config.temporal_task_queue, str)

    @patch.dict(os.environ, {}, clear=True)
    def test_tls_mode_accepts_valid_values(self, tls_pem_files):
        """Test that AppConfig accepts valid TLS posture values"""
        for mode in ("platform", "tls", "mtls", "off"):
            config = AppConfig(
                temporal_tls_mode=mode,
                temporal_tls_cert=tls_pem_files["cert"],
                temporal_tls_key=tls_pem_files["key"],
                _env_file=None,
            )
            assert config.temporal_tls_mode == mode

    @patch.dict(os.environ, {}, clear=True)
    def test_tls_mode_defaults_to_platform(self):
        """Test that temporal_tls_mode defaults to 'platform' when unset"""
        config = AppConfig(_env_file=None)
        assert config.temporal_tls_mode == "platform"

    @patch.dict(os.environ, {"TEMPORAL_TLS_MODE": "invalid"}, clear=False)
    def test_tls_mode_rejects_invalid_values(self):
        """Test that AppConfig raises ValidationError for invalid TLS mode"""
        with pytest.raises(ValueError):
            AppConfig()

    @patch.dict(os.environ, {}, clear=True)
    def test_max_request_body_bytes_defaults_to_core_dict_cap(self):
        """Test that the request body limit defaults to foundry-agent-core's dict cap"""
        config = AppConfig(_env_file=None)
        assert config.max_request_body_bytes == _MAX_DICT_BYTES

    @patch.dict(os.environ, {"MAX_REQUEST_BODY_BYTES": "4096"}, clear=False)
    def test_max_request_body_bytes_env_override(self):
        """Test that MAX_REQUEST_BODY_BYTES overrides the body limit"""
        config = AppConfig(_env_file=None)
        assert config.max_request_body_bytes == 4096

    @patch.dict(os.environ, {"MAX_REQUEST_BODY_BYTES": "0"}, clear=False)
    def test_max_request_body_bytes_rejects_non_positive(self):
        """Test that a zero body limit is rejected"""
        with pytest.raises(ValueError):
            AppConfig(_env_file=None)

    @patch.dict(os.environ, {"TEMPORAL_NAMESPACE": ""}, clear=False)
    def test_empty_namespace_is_rejected(self):
        """An empty TEMPORAL_NAMESPACE fails at startup instead of reaching Temporal"""
        with pytest.raises(ValueError):
            AppConfig(_env_file=None)

    @patch.dict(os.environ, {}, clear=True)
    def test_probe_timeout_defaults_to_point_nine(self):
        """The health probe timeout defaults to 0.9 s"""
        config = AppConfig(_env_file=None)
        assert config.temporal_probe_timeout_seconds == 0.9

    @patch.dict(os.environ, {"TEMPORAL_PROBE_TIMEOUT_SECONDS": "2.5"}, clear=False)
    def test_probe_timeout_env_override(self):
        """TEMPORAL_PROBE_TIMEOUT_SECONDS overrides the probe timeout"""
        config = AppConfig(_env_file=None)
        assert config.temporal_probe_timeout_seconds == 2.5

    @pytest.mark.parametrize("value", ["0", "-1", "inf", "nan"])
    def test_probe_timeout_rejects_non_positive_or_non_finite(self, value):
        """A zero, negative, infinite or NaN probe timeout is rejected"""
        with (
            patch.dict(os.environ, {"TEMPORAL_PROBE_TIMEOUT_SECONDS": value}),
            pytest.raises(ValueError),
        ):
            AppConfig(_env_file=None)

    @patch.dict(os.environ, {}, clear=True)
    def test_connect_timeout_defaults_to_thirty(self):
        """The shared Temporal connect time limit defaults to 30 s"""
        config = AppConfig(_env_file=None)
        assert config.temporal_connect_timeout_seconds == 30.0

    @patch.dict(os.environ, {"TEMPORAL_CONNECT_TIMEOUT_SECONDS": "60"}, clear=False)
    def test_connect_timeout_env_override(self):
        """TEMPORAL_CONNECT_TIMEOUT_SECONDS overrides the connect time limit"""
        config = AppConfig(_env_file=None)
        assert config.temporal_connect_timeout_seconds == 60.0

    @pytest.mark.parametrize("value", ["0", "-1", "inf", "nan"])
    def test_connect_timeout_rejects_non_positive_or_non_finite(self, value):
        """A zero, negative, infinite or NaN connect time limit is rejected"""
        with (
            patch.dict(os.environ, {"TEMPORAL_CONNECT_TIMEOUT_SECONDS": value}),
            pytest.raises(ValueError),
        ):
            AppConfig(_env_file=None)

    @patch.dict(os.environ, {}, clear=True)
    def test_probe_timeouts_before_reconnect_defaults_to_three(self):
        """The cached client is discarded after 3 probe timeouts in a row by default"""
        config = AppConfig(_env_file=None)
        assert config.temporal_probe_timeouts_before_reconnect == 3

    @patch.dict(
        os.environ, {"TEMPORAL_PROBE_TIMEOUTS_BEFORE_RECONNECT": "5"}, clear=False
    )
    def test_probe_timeouts_before_reconnect_env_override(self):
        """TEMPORAL_PROBE_TIMEOUTS_BEFORE_RECONNECT overrides the threshold"""
        config = AppConfig(_env_file=None)
        assert config.temporal_probe_timeouts_before_reconnect == 5

    @pytest.mark.parametrize("value", ["0", "-1", "1.5"])
    def test_probe_timeouts_before_reconnect_rejects_invalid(self, value):
        """A zero, negative or fractional threshold is rejected"""
        with (
            patch.dict(os.environ, {"TEMPORAL_PROBE_TIMEOUTS_BEFORE_RECONNECT": value}),
            pytest.raises(ValueError),
        ):
            AppConfig(_env_file=None)

    def test_env_template_does_not_pin_body_limit(self):
        """env.template leaves MAX_REQUEST_BODY_BYTES unset so the shared default applies"""
        template = Path(__file__).parents[2] / "env.template"
        active = [
            line
            for line in template.read_text(encoding="utf-8").splitlines()
            if line.strip().startswith("MAX_REQUEST_BODY_BYTES")
        ]
        assert active == []

    def test_development_vs_production_config(self):
        """Test typical development vs production configuration scenarios"""
        # Development config
        dev_config = AppConfig(
            api_host="127.0.0.1",
            api_port=8000,
            log_level="DEBUG",
            temporal_host="localhost:7233",
            temporal_namespace="development",
        )

        assert dev_config.api_host == "127.0.0.1"
        assert dev_config.log_level == "DEBUG"
        assert dev_config.temporal_namespace == "development"

        # Production-like config
        prod_config = AppConfig(
            api_host="0.0.0.0",
            api_port=80,
            log_level="INFO",
            temporal_host="temporal.prod.example.com:7233",
            temporal_namespace="production",
        )

        assert prod_config.api_host == "0.0.0.0"
        assert prod_config.api_port == 80
        assert prod_config.log_level == "INFO"
        assert prod_config.temporal_namespace == "production"


class TestApiAuthTokenStrength:
    """Regression: startup must reject a weak API_AUTH_TOKEN."""

    def test_rejects_token_below_minimum_length(self):
        """A token shorter than the minimum raises at construction time."""
        with pytest.raises(ValueError):
            AppConfig(api_auth_token="short", _env_file=None)  # noqa: S106

    def test_accepts_token_at_minimum_length(self):
        """A token exactly at the minimum length is accepted."""
        token = "a" * MIN_API_AUTH_TOKEN_LENGTH
        config = AppConfig(api_auth_token=token, _env_file=None)
        assert config.api_auth_token == token

    def test_unset_token_is_not_treated_as_weak(self):
        """Leaving the token unset is the existing fail-closed case, not a weak token."""
        config = AppConfig(api_auth_token=None, _env_file=None)
        assert config.api_auth_token is None

    def test_empty_token_is_treated_as_unset(self):
        """An empty string (env.template's shipped value) must not crash startup:
        it is treated the same as the token being unset."""
        config = AppConfig(api_auth_token="", _env_file=None)
        assert config.api_auth_token is None

    def test_empty_token_from_env_var_is_treated_as_unset(self):
        """The same empty-is-unset treatment applies when set via the environment,
        not just when passed directly to the constructor."""
        with patch.dict(os.environ, {"API_AUTH_TOKEN": ""}, clear=True):
            config = AppConfig(_env_file=None)
            assert config.api_auth_token is None


class TestMockModeGating:
    """Regression: an environment string alone must not activate mock mode."""

    @patch.dict(os.environ, {}, clear=True)
    def test_no_flag_no_production_string_does_not_enable_mock_mode(self):
        config = AppConfig(environment="development", _env_file=None)
        assert is_mock_mode_enabled(config) is False

    @patch.dict(os.environ, {"TESTING": "true"}, clear=False)
    def test_flag_with_non_production_environment_enables_mock_mode(self):
        config = AppConfig(environment="development", _env_file=None)
        assert is_mock_mode_enabled(config) is True

    @patch.dict(os.environ, {"TESTING": "true"}, clear=False)
    def test_flag_with_production_environment_disables_mock_mode(self):
        config = AppConfig(environment="production", _env_file=None)
        assert is_mock_mode_enabled(config) is False

    @patch.dict(os.environ, {}, clear=True)
    def test_production_string_alone_does_not_enable_mock_mode(self):
        config = AppConfig(environment="production", _env_file=None)
        assert is_mock_mode_enabled(config) is False

    @patch.dict(os.environ, {"TESTING": "true"}, clear=False)
    def test_assert_mock_mode_safe_raises_in_production(self):
        config = AppConfig(environment="production", _env_file=None)
        with pytest.raises(RuntimeError):
            assert_mock_mode_safe(config)

    @patch.dict(os.environ, {"TESTING": "true"}, clear=False)
    def test_assert_mock_mode_safe_allows_non_production(self):
        config = AppConfig(environment="development", _env_file=None)
        assert_mock_mode_safe(config)  # must not raise

    @patch.dict(os.environ, {}, clear=True)
    def test_assert_tls_mode_safe_refuses_off_in_production(self):
        from src.config import assert_tls_mode_safe

        config = AppConfig(
            temporal_tls_mode="off", environment="production", _env_file=None
        )
        with pytest.raises(RuntimeError, match="TEMPORAL_TLS_MODE=off"):
            assert_tls_mode_safe(config)

    @pytest.mark.parametrize(
        ("mode", "environment"),
        [("off", "development"), ("off", "staging"), ("platform", "production")],
    )
    @patch.dict(os.environ, {}, clear=True)
    def test_assert_tls_mode_safe_allows(self, mode, environment):
        from src.config import assert_tls_mode_safe

        config = AppConfig(
            temporal_tls_mode=mode, environment=environment, _env_file=None
        )
        assert_tls_mode_safe(config)  # must not raise

    @patch.dict(os.environ, {}, clear=True)
    def test_create_app_refuses_off_in_production(self):
        from src import main

        config = AppConfig(
            temporal_tls_mode="off", environment="production", _env_file=None
        )
        with (
            patch("src.main.get_config", return_value=config),
            pytest.raises(RuntimeError, match="TEMPORAL_TLS_MODE=off"),
        ):
            main.create_app()


class TestTemporalTlsMaterial:
    """TLS certificate material is validated at startup."""

    @patch.dict(os.environ, {}, clear=True)
    def test_mtls_without_cert_and_key_fails(self):
        with pytest.raises(ValueError, match="mtls requires TEMPORAL_TLS_CERT"):
            AppConfig(temporal_tls_mode="mtls", _env_file=None)

    @patch.dict(os.environ, {}, clear=True)
    def test_mtls_with_cert_only_fails(self, tls_pem_files):
        with pytest.raises(ValueError, match="mtls requires TEMPORAL_TLS_CERT"):
            AppConfig(
                temporal_tls_mode="mtls",
                temporal_tls_cert=tls_pem_files["cert"],
                _env_file=None,
            )

    @patch.dict(os.environ, {}, clear=True)
    def test_mtls_with_key_only_fails(self, tls_pem_files):
        with pytest.raises(ValueError, match="mtls requires TEMPORAL_TLS_CERT"):
            AppConfig(
                temporal_tls_mode="mtls",
                temporal_tls_key=tls_pem_files["key"],
                _env_file=None,
            )

    @patch.dict(os.environ, {}, clear=True)
    def test_mtls_with_cert_and_key_passes(self, tls_pem_files):
        config = AppConfig(
            temporal_tls_mode="mtls",
            temporal_tls_cert=tls_pem_files["cert"],
            temporal_tls_key=tls_pem_files["key"],
            _env_file=None,
        )
        assert (config.temporal_tls_cert, config.temporal_tls_key) == (
            tls_pem_files["cert"],
            tls_pem_files["key"],
        )

    @patch.dict(os.environ, {}, clear=True)
    def test_tls_cert_without_key_fails(self, tls_pem_files):
        with pytest.raises(ValueError, match="must be set together"):
            AppConfig(
                temporal_tls_mode="tls",
                temporal_tls_cert=tls_pem_files["cert"],
                _env_file=None,
            )

    @patch.dict(os.environ, {}, clear=True)
    def test_tls_key_without_cert_fails(self, tls_pem_files):
        with pytest.raises(ValueError, match="must be set together"):
            AppConfig(
                temporal_tls_mode="tls",
                temporal_tls_key=tls_pem_files["key"],
                _env_file=None,
            )

    @patch.dict(os.environ, {}, clear=True)
    def test_tls_with_only_private_ca_passes(self, tls_pem_files):
        config = AppConfig(
            temporal_tls_mode="tls", temporal_tls_ca=tls_pem_files["ca"], _env_file=None
        )
        assert config.temporal_tls_ca == tls_pem_files["ca"]
        assert config.temporal_tls_cert is None

    @pytest.mark.parametrize("mode", ["tls", "mtls"])
    @pytest.mark.parametrize("missing", ["cert", "key", "ca"])
    @patch.dict(os.environ, {}, clear=True)
    def test_missing_tls_file_fails_at_startup(
        self, tls_pem_files, tmp_path, mode, missing
    ):
        paths = dict(tls_pem_files)
        paths[missing] = str(tmp_path / "not-mounted.pem")
        env_name = {
            "cert": "TEMPORAL_TLS_CERT",
            "key": "TEMPORAL_TLS_KEY",
            "ca": "TEMPORAL_TLS_CA",
        }
        with pytest.raises(
            ValueError, match=f"{env_name[missing]} file is missing or unreadable"
        ):
            AppConfig(
                temporal_tls_mode=mode,
                temporal_tls_cert=paths["cert"],
                temporal_tls_key=paths["key"],
                temporal_tls_ca=paths["ca"],
                _env_file=None,
            )

    @patch.dict(os.environ, {}, clear=True)
    def test_platform_accepts_shared_tls_paths(self):
        """Paths are ignored under platform, so shared TEMPORAL_TLS_* vars don't fail,
        even when the files are not mounted in this container."""
        config = AppConfig(
            temporal_tls_mode="platform",
            temporal_tls_cert="/certs/not-mounted.pem",
            _env_file=None,
        )
        assert config.temporal_tls_mode == "platform"

    @pytest.mark.parametrize("mode", ["platform", "off"])
    @patch.dict(os.environ, {}, clear=True)
    def test_ignored_tls_paths_warn_under_platform_and_off(self, mode):
        from src.config import warn_if_tls_paths_ignored

        config = AppConfig(
            temporal_tls_mode=mode,
            temporal_tls_cert="/certs/not-mounted.pem",
            _env_file=None,
        )
        mock_logger = Mock()
        mock_logger.bind.return_value = mock_logger
        with patch("foundry_temporal.logging.get_logger", return_value=mock_logger):
            warn_if_tls_paths_ignored(config)
        mock_logger.warning.assert_called_once()
        assert (
            f"ignored because TEMPORAL_TLS_MODE is {mode}"
            in (mock_logger.warning.call_args.args[0])
        )

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"temporal_tls_mode": "platform"},
            {"temporal_tls_mode": "tls"},
        ],
    )
    @patch.dict(os.environ, {}, clear=True)
    def test_no_tls_warning_when_paths_unset_or_used(self, kwargs):
        from src.config import warn_if_tls_paths_ignored

        config = AppConfig(**kwargs, _env_file=None)
        mock_logger = Mock()
        mock_logger.bind.return_value = mock_logger
        with patch("foundry_temporal.logging.get_logger", return_value=mock_logger):
            warn_if_tls_paths_ignored(config)
        mock_logger.warning.assert_not_called()

    def test_tls_warning_runs_after_logging_setup(self):
        """create_app sets up structured logging first, so the warning uses its format."""
        from src import main

        calls = []
        with (
            patch(
                "foundry_temporal.logging.setup_structured_logging",
                side_effect=lambda *_: calls.append("logging"),
            ),
            patch.object(
                main,
                "warn_if_tls_paths_ignored",
                side_effect=lambda *_: calls.append("warn"),
            ),
        ):
            main.create_app()
        assert calls == ["logging", "warn"]

    @patch.dict(
        os.environ,
        {"TEMPORAL_TLS_CERT": "", "TEMPORAL_TLS_KEY": "   ", "TEMPORAL_TLS_CA": ""},
        clear=True,
    )
    def test_blank_tls_paths_are_unset(self):
        config = AppConfig(temporal_tls_mode="tls", _env_file=None)
        assert config.temporal_tls_cert is None
        assert config.temporal_tls_key is None
        assert config.temporal_tls_ca is None

    @patch.dict(
        os.environ,
        {"TEMPORAL_TLS_MODE": "mtls", "TEMPORAL_TLS_CERT": " ", "TEMPORAL_TLS_KEY": ""},
        clear=True,
    )
    def test_blank_paths_do_not_satisfy_mtls(self):
        with pytest.raises(ValueError, match="mtls requires TEMPORAL_TLS_CERT"):
            AppConfig(_env_file=None)

    def test_tls_paths_load_from_environment(self, tls_pem_files):
        env = {
            "TEMPORAL_TLS_MODE": "mtls",
            "TEMPORAL_TLS_CERT": tls_pem_files["cert"],
            "TEMPORAL_TLS_KEY": tls_pem_files["key"],
            "TEMPORAL_TLS_CA": tls_pem_files["ca"],
        }
        with patch.dict(os.environ, env, clear=True):
            config = AppConfig(_env_file=None)
        assert (
            config.temporal_tls_cert,
            config.temporal_tls_key,
            config.temporal_tls_ca,
        ) == (tls_pem_files["cert"], tls_pem_files["key"], tls_pem_files["ca"])


class TestStartupErrorsHideSecrets:
    """Validation errors never echo settings such as API_AUTH_TOKEN."""

    TOKEN = "SECRET-token-abcdefghijklmnopqrstuvwxyz-0123"  # noqa: S105

    def test_tls_error_does_not_leak_token(self, tmp_path):
        env = {
            "TEMPORAL_TLS_MODE": "mtls",
            "TEMPORAL_TLS_CERT": str(tmp_path / "missing.pem"),
            "TEMPORAL_TLS_KEY": str(tmp_path / "missing.key"),
            "API_AUTH_TOKEN": self.TOKEN,
        }
        with patch.dict(os.environ, env, clear=True), pytest.raises(ValueError) as err:
            AppConfig(_env_file=None)
        assert "TEMPORAL_TLS_CERT file is missing or unreadable" in str(err.value)
        # Pydantic truncates long inputs to a start and an end, so check both.
        assert self.TOKEN[:12] not in str(err.value)
        assert self.TOKEN[-12:] not in str(err.value)

    def test_weak_token_error_does_not_echo_token(self):
        weak = "short-weak-token"
        with (
            patch.dict(os.environ, {"API_AUTH_TOKEN": weak}, clear=True),
            pytest.raises(ValueError) as err,
        ):
            AppConfig(_env_file=None)
        assert weak not in str(err.value)
