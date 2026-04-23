# Developing alloy-sub

## Local setup

```bash
uv sync --group dev
uv run tox -e format
uv run tox -e lint
uv run tox -e static
uv run tox -e unit
charmcraft pack
```

## Integration

```bash
CHARM_PATH=/path/to/alloy-sub.charm uv run pytest tests/integration -v
```
