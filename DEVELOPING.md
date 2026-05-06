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

For the local compatibility check used during the v2 migration, refresh the
same built artifact into the `alloy-sub-e2e-20260419` model and verify both:

- `alloy-sub` attached to `polkadot` stays healthy with a v1 payload
- `alloy-sub-reference` attached to `dwellir-observability-reference` becomes
  healthy with a v2 payload

## Releasing to charmhub

Get a new token.

```bash
charmcraft login --export=secrets.auth --charm=reth --permission=package-manage --permission=package-view --channel=latest/edge --ttl=31536000
```
