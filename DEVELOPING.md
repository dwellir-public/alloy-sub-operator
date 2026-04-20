# Developing alloy-sub

## Local setup

```bash
uv sync --group dev
tox -e format
tox -e lint
tox -e static
tox -e unit
charmcraft pack
```

## Integration

```bash
CHARM_PATH=/path/to/alloy-sub.charm uv run pytest tests/integration -v
```
