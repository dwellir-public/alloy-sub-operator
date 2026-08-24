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

For a compatibility check, refresh the same built artifact into a disposable
model and verify both:

- `alloy-sub` attached to `polkadot` stays healthy with a v1 payload
- `alloy-sub-reference` attached to `dwellir-observability-reference` becomes
  healthy with its v3 payload

## Releasing to charmhub

Get a new token.

```bash
charmcraft login --export=secrets.auth --charm=reth --permission=package-manage --permission=package-view --channel=latest/edge --ttl=31536000
```
