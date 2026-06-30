"""Package-level smoke tests: the vault imports as an installed package and its CLI entrypoint is wired."""
from agent_vault import synapse


def test_main_is_callable():
    assert callable(synapse.main)


def test_commands_dispatch_table_populated():
    assert isinstance(synapse.COMMANDS, dict)
    assert synapse.COMMANDS, "COMMANDS must not be empty"
    for required in ("find", "show", "list", "resolve", "compact"):
        assert required in synapse.COMMANDS, f"missing command: {required}"


def test_help_returns_zero(monkeypatch, capsys):
    # `agent-vault --help` prints the module docstring and returns 0.
    monkeypatch.setattr("sys.argv", ["agent-vault", "--help"])
    rc = synapse.main()
    assert rc == 0
    assert "find" in capsys.readouterr().out


def test_no_command_returns_one(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["agent-vault"])
    rc = synapse.main()
    assert rc == 1
