"""
Tests for CLI commands: run and doctor.
"""

import pytest
from unittest.mock import patch, MagicMock


class TestRunCommand:
    """Tests for the run command."""

    @patch("core.cli.commands.run.print_error")
    def test_run_server_missing_uvicorn(self, mock_err):
        """Test run_server when uvicorn is not installed."""
        # Clean way to mock import error for a specific module
        import core.cli.commands.run as run_module

        # We mock the internal 'import uvicorn' by raising ImportError when it's called
        with patch(
            "builtins.__import__",
            side_effect=lambda name, *args, **kwargs: (
                MagicMock() if name != "uvicorn" else (_ for _ in ()).throw(ImportError)
            ),
        ):
            # We need to make sure we don't break other internal imports
            # Actually, just raising ImportError whenever 'uvicorn' is requested
            def side_effect(name, *args, **kwargs):
                if name == "uvicorn":
                    raise ImportError("No module named 'uvicorn'")
                # For other modules, we'd want the real import, but that's hard in a side_effect
                # without an infinite loop.
                # Let's try a simpler approach: mock 'uvicorn' in sys.modules as a Non-Importable
                pass

        with patch.dict("sys.modules", {"uvicorn": None}):
            # Force a re-run of the function logic which has the 'import uvicorn' inside
            result = run_module.run_server()
            assert result == 1
            mock_err.assert_called_once()

    @patch("core.cli.commands.run.print_error")
    def test_run_server_missing_backend(self, mock_err, tmp_path, monkeypatch):
        """Test run_server when backend.py doesn't exist."""
        from core.cli.commands.run import run_server

        monkeypatch.chdir(tmp_path)

        with patch("core.cli.commands.run.Path") as mock_path:
            mock_path.cwd.return_value = tmp_path
            mock_backend = MagicMock()
            mock_backend.exists.return_value = False
            mock_path.return_value.__truediv__.return_value = mock_backend

            # Since uvicorn is available, we need to mock it
            with patch.dict("sys.modules", {"uvicorn": MagicMock()}):
                result = run_server()
                assert result == 1
                mock_err.assert_called_once()
                assert "backend.py not found" in mock_err.call_args[0][0]

    @patch("core.cli.commands.run.console")
    def test_run_server_success(self, mock_console, tmp_path, monkeypatch):
        """Test run_server successful execution."""
        # Create mock backend.py
        backend_file = tmp_path / "backend.py"
        backend_file.write_text("# mock backend")

        monkeypatch.chdir(tmp_path)

        mock_uvicorn = MagicMock()

        with patch.dict("sys.modules", {"uvicorn": mock_uvicorn}):
            from core.cli.commands import run as run_module

            run_module.run_server(host="localhost", port=9000, reload=False)
            mock_uvicorn.run.assert_called_once()
            mock_console.print.assert_called()

    @patch("core.cli.commands.run.console")
    def test_run_server_keyboard_interrupt(self, mock_console, tmp_path, monkeypatch):
        """Test run_server handles KeyboardInterrupt gracefully."""
        backend_file = tmp_path / "backend.py"
        backend_file.write_text("# mock backend")

        monkeypatch.chdir(tmp_path)

        mock_uvicorn = MagicMock()
        mock_uvicorn.run.side_effect = KeyboardInterrupt()

        with patch.dict("sys.modules", {"uvicorn": mock_uvicorn}):
            from core.cli.commands import run as run_module

            result = run_module.run_server()
            assert result == 0  # Graceful exit
            mock_console.print.assert_called()


class TestDoctorCommand:
    """Tests for the doctor command."""

    def test_check_port_open(self):
        """Test check_port with open port."""
        from core.cli.commands.doctor import check_port

        with patch("socket.socket") as mock_socket:
            instance = MagicMock()
            mock_socket.return_value = instance
            instance.connect_ex.return_value = 0

            result = check_port("localhost", 6379)
            assert result is True
            instance.close.assert_called_once()

    def test_check_port_closed(self):
        """Test check_port with closed port."""
        from core.cli.commands.doctor import check_port

        with patch("socket.socket") as mock_socket:
            instance = MagicMock()
            mock_socket.return_value = instance
            instance.connect_ex.return_value = 1  # Connection refused

            result = check_port("localhost", 9999)
            assert result is False

    @patch("core.config.get_storage_config")
    def test_check_redis_connected(self, mock_storage_config):
        """Test check_redis when Redis is available."""
        from core.cli.commands.doctor import check_redis

        mock_storage_config.return_value = MagicMock(
            cache_redis_url="redis://localhost:6379"
        )

        with patch("core.cli.commands.doctor.check_port", return_value=True):
            result = check_redis()
            assert result.passed is True
            assert "Connected" in result.message

    @patch("core.config.get_storage_config")
    def test_check_redis_disconnected(self, mock_storage_config):
        """Test check_redis when Redis is not available."""
        from core.cli.commands.doctor import check_redis

        mock_storage_config.return_value = MagicMock(
            cache_redis_url="redis://localhost:6379"
        )

        with patch("core.cli.commands.doctor.check_port", return_value=False):
            result = check_redis()
            assert result.passed is False
            assert "Cannot connect" in result.message

    def test_check_env_file_exists(self, tmp_path):
        """Test check_env_file when .env exists."""
        from core.cli.commands.doctor import check_env_file

        with patch("core.cli.commands.doctor.Path") as mock_path:
            mock_cwd = MagicMock()
            mock_path.cwd.return_value = mock_cwd

            mock_configs = MagicMock()
            mock_env = MagicMock()
            mock_env.exists.return_value = True

            # Chain: Path.cwd() / "configs" / ".env"
            mock_cwd.__truediv__.return_value = mock_configs
            mock_configs.__truediv__.return_value = mock_env

            result = check_env_file()
            assert result.passed is True

    def test_check_env_file_missing(self, tmp_path, monkeypatch):
        """Test check_env_file when .env doesn't exist."""
        from core.cli.commands.doctor import check_env_file

        monkeypatch.chdir(tmp_path)

        with patch("core.cli.commands.doctor.Path") as mock_path:
            mock_path.cwd.return_value = tmp_path
            mock_env = MagicMock()
            mock_env.exists.return_value = False
            mock_path.return_value.__truediv__.return_value = mock_env

            result = check_env_file()
            assert result.passed is False
            assert ".env" in result.message

    def test_check_plugins_found(self, tmp_path, monkeypatch):
        """Test check_plugins when plugins exist."""
        from core.cli.commands.doctor import check_plugins

        # Create mock plugins directory with plugins
        plugins_dir = tmp_path / "plugins"
        plugins_dir.mkdir()
        plugin1 = plugins_dir / "test-plugin"
        plugin1.mkdir()
        (plugin1 / "plugin.py").write_text("# plugin")

        monkeypatch.chdir(tmp_path)

        with patch("core.cli.commands.doctor.Path") as mock_path:
            mock_path.cwd.return_value = tmp_path
            mock_plugins = MagicMock()
            mock_plugins.exists.return_value = True

            # Mock iterdir to return our plugin directory
            mock_plugin_dir = MagicMock()
            mock_plugin_dir.is_dir.return_value = True
            mock_plugin_file = MagicMock()
            mock_plugin_file.exists.return_value = True
            mock_plugin_dir.__truediv__.return_value = mock_plugin_file

            mock_plugins.iterdir.return_value = [mock_plugin_dir]
            mock_path.return_value.__truediv__.return_value = mock_plugins

            result = check_plugins()
            assert result.passed is True

    @patch("core.config.get_llm_config")
    def test_check_llm_provider_ollama_available(self, mock_llm_config):
        """Test check_llm_provider when Ollama is available."""
        from core.cli.commands.doctor import check_llm_provider

        mock_llm_config.return_value = MagicMock(
            provider="ollama", api_base="http://localhost:11434"
        )

        with patch("core.cli.commands.doctor.check_port", return_value=True):
            result = check_llm_provider()
            assert result.passed is True
            assert "Ollama connected" in result.message

    @patch("core.config.get_llm_config")
    def test_check_llm_provider_openai_configured(self, mock_llm_config):
        """Test check_llm_provider when OpenAI API key is set."""
        from core.cli.commands.doctor import check_llm_provider

        mock_llm_config.return_value = MagicMock(
            provider="openai", api_key="sk-test-key"
        )

        result = check_llm_provider()
        assert result.passed is True
        assert "OPENAI" in result.message

    @patch("core.cli.commands.doctor.console")
    def test_run_doctor_all_pass(self, mock_console):
        """Test run_doctor when all checks pass."""
        from core.cli.commands.doctor import run_doctor, CheckResult

        mock_results = [
            CheckResult("Environment", True, "OK"),
            CheckResult("LLM Provider", True, "OK"),
            CheckResult("Redis (Cache)", True, "OK"),
            CheckResult("Qdrant", True, "OK"),
            CheckResult("GraphDB", True, "OK"),
            CheckResult("Plugins", True, "OK"),
        ]

        with patch(
            "core.cli.commands.doctor.check_env_file", return_value=mock_results[0]
        ):
            with patch(
                "core.cli.commands.doctor.check_llm_provider",
                return_value=mock_results[1],
            ):
                with patch(
                    "core.cli.commands.doctor.check_redis", return_value=mock_results[2]
                ):
                    with patch(
                        "core.cli.commands.doctor.check_qdrant",
                        return_value=mock_results[3],
                    ):
                        with patch(
                            "core.cli.commands.doctor.check_graph_db",
                            return_value=mock_results[4],
                        ):
                            with patch(
                                "core.cli.commands.doctor.check_postgres",
                                return_value=mock_results[0],  # Use any passing result
                            ):
                                with patch(
                                    "core.cli.commands.doctor.check_plugins",
                                    return_value=mock_results[5],
                                ):
                                    result = run_doctor()
                                    assert result == 0
                                mock_console.print.assert_called()


class TestCLIMain:
    """Tests for CLI main entry point."""

    def test_cli_help(self, capsys):
        """Test CLI shows help when no command given."""
        from core.cli.__main__ import main

        with patch("sys.argv", ["mas"]):
            result = main()
            assert result == 0

    def test_cli_version(self, capsys):
        """Test CLI version command."""
        from core.cli.__main__ import main

        with patch("sys.argv", ["mas", "--version"]):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0

    def test_cli_run_command_dispatch(self):
        """Test CLI dispatches to run command."""
        from core.cli.__main__ import main

        with patch("sys.argv", ["mas", "run", "--port", "9000"]):
            with patch("core.cli.__main__.cmd_run", return_value=0) as mock_run:
                main()
                mock_run.assert_called_once()

    def test_cli_doctor_command_dispatch(self):
        """Test CLI dispatches to doctor command."""
        from core.cli.__main__ import main

        with patch("sys.argv", ["mas", "doctor"]):
            with patch("core.cli.__main__.cmd_doctor", return_value=0) as mock_doctor:
                main()
                mock_doctor.assert_called_once()

    def test_cli_init_with_rag_template(self):
        """Test CLI init with rag-system template."""
        from core.cli.__main__ import main

        with patch(
            "sys.argv", ["mas", "init", "test-project", "--template", "rag-system"]
        ):
            with patch("core.cli.__main__.cmd_init", return_value=0) as mock_init:
                main()
                mock_init.assert_called_once()

    def test_cli_shell_command_dispatch(self):
        """Test CLI dispatches to shell command."""
        from core.cli.__main__ import main

        with patch("sys.argv", ["mas", "shell"]):
            with patch("core.cli.__main__.cmd_shell", return_value=0) as mock_shell:
                main()
                mock_shell.assert_called_once()

    def test_cli_db_command_dispatch(self):
        """Test CLI dispatches to db command."""
        from core.cli.__main__ import main

        with patch("sys.argv", ["mas", "db", "status"]):
            with patch("core.cli.__main__.cmd_db", return_value=0) as mock_db:
                main()
                mock_db.assert_called_once()

    def test_cli_cache_command_dispatch(self):
        """Test CLI dispatches to cache command."""
        from core.cli.__main__ import main

        with patch("sys.argv", ["mas", "cache", "stats"]):
            with patch("core.cli.__main__.cmd_cache", return_value=0) as mock_cache:
                main()
                mock_cache.assert_called_once()

    def test_cli_queue_command_dispatch(self):
        """Test CLI dispatches to queue command."""
        from core.cli.__main__ import main

        with patch("sys.argv", ["mas", "queue", "status"]):
            with patch("core.cli.__main__.cmd_queue", return_value=0) as mock_queue:
                main()
                mock_queue.assert_called_once()

    def test_cli_docs_command_dispatch(self):
        """Test CLI dispatches to docs command."""
        from core.cli.__main__ import main

        with patch("sys.argv", ["mas", "docs", "generate"]):
            with patch("core.cli.__main__.cmd_docs", return_value=0) as mock_docs:
                main()
                mock_docs.assert_called_once()
