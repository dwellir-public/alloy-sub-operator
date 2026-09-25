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


## Dependency ownership and BOMs

`pyproject.toml` and `uv.lock` define Python dependencies. `dependencies/upstreams.json`
records upstream ownership; `dependencies/vendored.json` pins the exact source,
commit, hash, license and LIB metadata of each shipped library. Locally modified
libraries identify their downstream source and upstream owner explicitly. An
upstream catalog entry alone does not install a dependency.

Review dependency updates together with their locks, source pins and compatibility
tests. Do not replace a locally patched library without reviewing its documented
changes. Run these checks from this repository:

```bash
uv lock --check
uv run tox -e provenance
python3 tools/dependency_bom.py --verify-upstream
python3 tools/dependency_bom.py --output build/development.cdx.json
# Set CHARM_PATH and CHARM_BASE to the artifact and base actually built.
python3 tools/dependency_bom.py --artifact "$CHARM_PATH" --base "$CHARM_BASE" \
  --arch amd64 --output build/runtime.cdx.json
```

The development CycloneDX BOM describes the locked development/test dependency
graph. The runtime BOM checks installed distribution versions and vendored bytes
against the built archive and records its checksum, source revision and input
hashes. These BOMs do not cover OS packages, downloaded workload binaries or
transitive build-tool environments. CI verifies provenance and keeps generated
BOMs as artifacts rather than source files.

Rule compression uses Canonical's public `cosl` API. Rule acceptance, retention
and size policy belong to this charm; no shared Dwellir transport package or
cross-repository source synchronization is required.
