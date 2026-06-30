"""Package-level smoke tests: the vault imports as an installed package and its CLI entrypoint is wired."""
from agent_vault import synapse
from _pytest.monkeypatch import MonkeyPatch
from _pytest.capture import CaptureFixture


def test_main_is_callable() -> None:
    assert callable(synapse.main)


def test_commands_dispatch_table_populated() -> None:
    assert isinstance(synapse.COMMANDS, dict)
    assert synapse.COMMANDS, "COMMANDS must not be empty"
    for required in ("find", "show", "list", "resolve", "compact"):
        assert required in synapse.COMMANDS, f"missing command: {required}"


def test_help_returns_zero(monkeypatch: MonkeyPatch, capsys: CaptureFixture[str]) -> None:
    # `agent-vault --help` prints the module docstring and returns 0.
    monkeypatch.setattr("sys.argv", ["agent-vault", "--help"])
    rc = synapse.main()
    assert rc == 0
    assert "find" in capsys.readouterr().out


def test_no_command_returns_one(monkeypatch: MonkeyPatch, capsys: CaptureFixture[str]) -> None:
    monkeypatch.setattr("sys.argv", ["agent-vault"])
    rc = synapse.main()
    assert rc == 1
