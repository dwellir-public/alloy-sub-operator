from pathlib import Path


def test_developing_doc_exists():
    assert Path("DEVELOPING.md").exists()


def test_architecture_doc_exists():
    assert Path("docs/charm-architecture.md").exists()


def test_charmcraft_uses_uv_baseline():
    charmcraft = Path("charmcraft.yaml").read_text()

    assert "title: Grafana Alloy Subordinate" in charmcraft
    assert "summary: Subordinate charm for workload-local metrics and logs using Grafana Alloy." in charmcraft
    assert "machine-observability:" in charmcraft
    assert "send-loki-logs:" in charmcraft
    assert "send-remote-write:" in charmcraft
    assert "plugin: uv" in charmcraft
    assert "build-snaps:" in charmcraft
    assert "- astral-uv" in charmcraft
    assert "ubuntu@22.04:amd64:" in charmcraft
    assert "ubuntu@24.04:amd64:" in charmcraft
    assert "ubuntu@26.04:amd64:" in charmcraft


def test_charmcraft_declares_the_host_metrics_option():
    """An undeclared option is unsettable, so the charm would read False forever."""
    charmcraft = Path("charmcraft.yaml").read_text()

    assert "enable-host-metrics:" in charmcraft
    assert "type: boolean" in charmcraft
    assert "80% warning, 90% critical" in charmcraft


def test_readme_documents_host_metrics():
    readme = Path("README.md").read_text()

    assert "## Host metrics" in readme
    assert "enable-host-metrics=true" in readme
    assert "HostDiskUsageWarning" in readme
    assert "HostMemoryUsageCritical" in readme
    assert "send-remote-write" in readme.split("## Host metrics")[1].split("## Validation Flow")[0]


def test_charmcraft_declares_empty_log_files_option():
    import yaml

    options = yaml.safe_load(Path("charmcraft.yaml").read_text())["config"]["options"]
    assert options["log-files"]["type"] == "string"
    assert options["log-files"]["default"] == ""


def test_pyproject_has_uv_style_dependency_groups():
    pyproject = Path("pyproject.toml").read_text()

    assert '[project]\nname = "alloy-sub"' in pyproject
    assert 'requires-python = ">=3.10"' in pyproject
    assert "[dependency-groups]" in pyproject
    assert 'lint = ["ruff", "codespell"]' in pyproject
    assert 'unit = ["pytest", "coverage[toml]", "ops[testing]"]' in pyproject
    assert 'integration = ["pytest", "pytest-operator", "juju"]' in pyproject
    assert '"pyright"' in pyproject
    assert '"tox"' in pyproject


def test_tox_uses_uv_commands():
    tox_ini = Path("tox.ini").read_text()

    assert "env_list = format, lint, static, unit" in tox_ini
    assert "PYTHONPATH = {tox_root}/lib:{tox_root}/src" in tox_ini
    assert "uv run ruff format src tests" in tox_ini
    assert "uv run codespell {tox_root}" in tox_ini
    assert "uv run pyright src" in tox_ini
    assert "uv run coverage run --source=src -m pytest -v tests/unit" in tox_ini


def test_developing_doc_covers_local_setup_and_integration():
    developing = Path("DEVELOPING.md").read_text()

    assert "# Developing alloy-sub" in developing
    assert "uv sync --group dev" in developing
    assert "tox -e unit" in developing
    assert "charmcraft pack" in developing
    assert "CHARM_PATH=/path/to/alloy-sub.charm uv run pytest tests/integration -v" in developing


def test_architecture_doc_describes_subordinate_responsibilities():
    architecture = Path("docs/charm-architecture.md").read_text()

    assert "# alloy-sub Charm Architecture" in architecture
    assert "machine subordinate" in architecture
    assert "machine-observability" in architecture
    assert "render and validate `/etc/alloy/config.alloy`" in architecture
    assert "forward logs to Loki" in architecture


def test_readme_documents_profiling():
    readme = Path("README.md").read_text()

    assert "## Profiling" in readme
    assert "profiling-source" in readme


def test_principal_facing_endpoints_are_limited_to_one_principal():
    """Juju only refuses a second principal application when limit is declared."""
    import yaml

    metadata = yaml.safe_load(Path("charmcraft.yaml").read_text())
    requires = metadata["requires"]

    for endpoint in ("juju-info", "machine-observability", "profiling-source"):
        assert requires[endpoint]["scope"] == "container"
        assert requires[endpoint]["limit"] == 1
    for endpoint in ("send-loki-logs", "send-remote-write", "grafana-cloud-config"):
        assert "limit" not in requires[endpoint]
    assert requires["send-profiles"]["limit"] == 1
    assert metadata["peers"] == {"alloy-peers": {"interface": "alloy_sub_peers"}}


def test_readme_documents_single_principal_rule():
    readme = Path("README.md").read_text()

    assert "## Single principal" in readme
    section = readme.split("## Single principal")[1].split("\n## ")[0]
    for endpoint in ("`juju-info`", "`machine-observability`", "`profiling-source`"):
        assert endpoint in section
    assert "`limit: 1`" in section
    assert "`alloy-peers`" in section
    assert "multiple principal applications: kusama, polkadot; relate alloy-sub to one principal" in section
    assert "juju deploy alloy-sub alloy-sub-" in section
    assert "juju remove-relation" in section
    assert "juju refresh" in section
    assert "| `profiling-source` | `pprof_scrape` | optional, `scope: container`, `limit: 1` |" in readme


def test_architecture_doc_states_single_principal_rule():
    architecture = Path("docs/charm-architecture.md").read_text()

    assert "one principal application" in architecture
    assert "`limit: 1`" in architecture
    assert "`alloy-peers`" in architecture


def test_charmcraft_declares_the_adopt_alloy_action():
    import yaml

    action = yaml.safe_load(Path("charmcraft.yaml").read_text())["actions"]["adopt-alloy"]
    assert action["params"] == {
        "force": {"type": "boolean", "default": False, "description": action["params"]["force"]["description"]}
    }
    assert action["additionalProperties"] is False


def test_readme_documents_alloy_ownership_and_adoption():
    readme = Path("README.md").read_text()

    assert "## One Alloy per machine" in readme
    section = readme.split("## One Alloy per machine")[1].split("\n## ")[0]
    assert "Alloy on this machine is managed by <unit>; remove this unit's principal relation" in section
    assert "unmanaged Alloy found; run the adopt-alloy action or uninstall Alloy" in section
    assert "juju run alloy-sub/0 adopt-alloy" in section
    assert "force=true" in section
    assert "/var/lib/juju-alloy/owner.json" in section
    assert "alloy-vm" in section
    assert "apt-get purge alloy" in section


def test_architecture_doc_states_alloy_ownership():
    architecture = Path("docs/charm-architecture.md").read_text()

    assert "/var/lib/juju-alloy/owner.json" in architecture
    assert "adopt-alloy" in architecture
