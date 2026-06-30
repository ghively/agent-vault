"""The package registers an `agent-vault` console script pointing at synapse.main."""
from importlib.metadata import entry_points


def test_console_script_registered() -> None:
    eps = entry_points(group="console_scripts")
    vault_eps = [e for e in eps if e.name == "agent-vault"]
    assert vault_eps, "agent-vault console script not registered"
    assert vault_eps[0].value == "agent_vault.synapse:main"
