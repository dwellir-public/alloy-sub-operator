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


def test_pyproject_has_uv_style_dependency_groups():
    pyproject = Path("pyproject.toml").read_text()

    assert '[project]\nname = "alloy-sub"' in pyproject
    assert 'requires-python = ">=3.10"' in pyproject
    assert "[dependency-groups]" in pyproject
    assert 'lint = ["ruff", "codespell"]' in pyproject
    assert 'unit = ["pytest", "coverage[toml]", "ops[testing]"]' in pyproject
    assert 'integration = ["pytest", "pytest-operator", "juju"]' in pyproject
    assert '"pyright"' in pyproject


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


def test_charm_tests_workflow_runs_pack():
    workflow = Path(".github/workflows/charm-tests.yml").read_text()

    assert "uv sync --group dev" in workflow
    assert "tox -e unit" in workflow
    assert "charmcraft pack" in workflow


def test_architecture_doc_describes_subordinate_responsibilities():
    architecture = Path("docs/charm-architecture.md").read_text()

    assert "# alloy-sub Charm Architecture" in architecture
    assert "machine subordinate" in architecture
    assert "machine-observability" in architecture
    assert "render and validate `/etc/alloy/config.alloy`" in architecture
    assert "forward logs to Loki" in architecture
