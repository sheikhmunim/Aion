"""Tests for the setup/enable ollama command handler in the CLI."""

import pytest
from unittest.mock import patch, MagicMock


@pytest.mark.asyncio
async def test_setup_command_clears_declined_flag():
    """setup command should clear ollama_setup_declined and run setup()."""
    saved = {}

    def fake_get_config():
        return {"ollama_setup_declined": True}

    def fake_save_config(cfg):
        saved.update(cfg)

    with patch("aion.cli.get_config", fake_get_config), \
         patch("aion.cli.save_config", fake_save_config), \
         patch("aion.setup.setup", return_value=True), \
         patch("aion.cli.reset_status"), \
         patch("aion.cli.display"):
        from aion.cli import handle_input
        result = await handle_input("setup", gcal=None, solver=MagicMock())

    assert result is True
    assert "ollama_setup_declined" not in saved  # flag was cleared


@pytest.mark.asyncio
async def test_setup_command_handles_failure():
    """setup command should show error if setup() fails."""
    with patch("aion.cli.get_config", return_value={}), \
         patch("aion.cli.save_config"), \
         patch("aion.setup.setup", return_value=False), \
         patch("aion.cli.reset_status"), \
         patch("aion.cli.display") as mock_display:
        from aion.cli import handle_input
        result = await handle_input("enable ollama", gcal=None, solver=MagicMock())

    assert result is True
    mock_display.print_error.assert_called_once()


@pytest.mark.asyncio
async def test_setup_command_aliases():
    """All aliases (setup, enable ollama, setup ollama) should trigger setup."""
    aliases = ["setup", "enable ollama", "setup ollama"]

    for alias in aliases:
        call_count = 0

        def fake_setup():
            nonlocal call_count
            call_count += 1
            return True

        with patch("aion.cli.get_config", return_value={}), \
             patch("aion.cli.save_config"), \
             patch("aion.setup.setup", side_effect=fake_setup), \
             patch("aion.cli.reset_status"), \
             patch("aion.cli.display"):
            from aion.cli import handle_input
            result = await handle_input(alias, gcal=None, solver=MagicMock())

        assert result is True, f"handle_input returned False for alias: {alias!r}"
        assert call_count == 1, f"setup() not called for alias: {alias!r}"
