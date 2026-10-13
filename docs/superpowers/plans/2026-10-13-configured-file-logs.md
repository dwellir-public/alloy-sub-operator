# Configured file logs

## Scope and evidence

Add the user-selected `log-files` option, default empty. Implement and verify a built charm in the retained, nonproduction `localhost-localhost:admin/machine-observability-v3-reference` model. No production deployment or publication is authorized by this task.

Target baseline: alloy-sub-operator commit `96115d451cb88e43d8e430c370b2b0cc06f183c2`, clean checkout. All 295 baseline unit tests passed.

Naming/syntax reference: dwellir-public/opentelemetry-collector-operator, verified origin, clean commit `c5d10698835f8b23a3cb346ae54fe0760b1a1c4b`. Its `path_exclude` is a semicolon-separated string of globs; it has no user-configurable file include list and automatically reads `/var/log/**/*log`. User chose `log-files` over proposed `path_include`. Keep Alloy's default empty and reuse the semicolon syntax and existing `path_exclude`. OpenTelemetry reads new files from the beginning; Alloy's existing file source does likewise.

| Requirement and source | Authority / applicability | Before | Disposition / target | Verification |
| --- | --- | --- | --- | --- |
| Configurable file sources, user request | task-required / file logs | Only relation declarations | Implement `log-files`, empty default, absolute paths/globs | Config-event tests and live append/query |
| Additive sources, user request and existing relation contract | contract-required / relation | Existing file and journal sources | Preserve relation sources; skip exact duplicate configured paths | Unit and local config checks |
| Syntax consistent with observed OTel `path_exclude`; user chose name | task-required / config | Exclusions only | Semicolon-separated includes; trim blanks and deduplicate | Parsing and exclusion tests |
| Config-only collection without machine-observability, user intent | task-required / file logs | Relation or host metrics required | Allow configured file logs with juju-info; accurate active status | Scenario test without observability relation |
| Preserve empty behavior and clearing | task-required / lifecycle | Existing relation/metrics gating | Default adds no files; clearing removes config sources | Unit and live clear/query checks |
| Built artifact and user-visible config verification, skill generic/testing DEPLOY-001 | required-baseline / verification | Legacy pytest-operator integration exists | Bounded retained-model verification with built artifact; defer unrelated suite migration | Unit/lint/static, pack Ubuntu 24.04, explicit local model |

No new alerts, relation schema or topology labels. Whole-repository compliance is unassessed. Existing integration framework migration is deferred beyond this bounded change.

## Implementation

1. Add config-event/scenario tests for empty/default, semicolon paths/globs, exclusions, coexistence, exact duplicates, clearing and relation-free collection. Check failure before implementation.
2. Declare `log-files` in charmcraft.yaml. Parse selected paths in a charm helper, rejecting relative paths. Merge into existing file source translation, keeping relation attributes. Reuse existing exclusion merging and Loki forwarding. Enable useful config-only collection and accurate status. Escape file paths using JSON string encoding in the renderer.
3. Document the option in README.md, with set/clear commands, existing-file behavior, permissions, local-machine scope, sink requirement and glob-overlap caveat. Keep helper/renderer boundaries.
4. Run lint, static and unit checks. Build Ubuntu 24.04 amd64 with charmcraft. Inspect existing model state and perform no model/app teardown.
5. Refresh only the local test model's existing Alloy application with the built artifact. Create readable `/tmp/alloy-config-file-test.log` on alloy-sub/0's machine. Query unique preexisting and appended markers in local Loki. Verify exclusion, removal and preserved journal/relation file sources. Restore original application config after checks and leave the model running.

## Handoff

Record actual checks, artifact checksum, deployed source hash, test model/unit, query evidence and any skipped gates. Ubuntu 22.04 live deployment is not included. Review the final diff. Keep work on the feature branch for review; no release is requested.

## Actual results

Implemented in `charmcraft.yaml`, `src/charm.py`, `src/config_builder.py` and `README.md`, with Scenario and baseline tests. Worktree branch: `feat/configured-file-logs`.

| Requirement from matrix | Actual result and evidence |
| --- | --- |
| Configurable sources | PASS. Schema defaults to empty; live glob `/tmp/alloy-config-file-test/*.log` collected on `alloy-sub/0`. |
| Additive relation sources | PASS. Scenario tests retain journal and relation file sources and attributes. The local rendered journal pipeline remains after setting and clearing config. |
| Semicolon parsing and exclusions | PASS. Tests cover blanks, duplicate paths, exact relation duplicates and escaping. Live duplicate glob produced one target; excluded file did not reach Loki. |
| Config-only collection | PASS in Scenario with juju-info and Loki, without machine-observability. Not separately deployed because the retained model already has the relation. |
| Empty and clearing | PASS. Unit tests cover empty values and clearing a previous config. After live clear, the test path disappeared and a subsequent append was absent from Loki after the observation interval. |
| Built artifact and config behavior | Ubuntu 24.04 amd64 pack PASS. Deployed local artifact revision 12 to the existing Alloy app. Both units active/idle. No production tests, app removal or model teardown. Existing pytest-operator-to-Jubilant suite migration remains deferred. |

Validation: `uv run tox -e lint,static,unit`, followed by `uv run tox -e lint` after correcting test import ordering. Static checks passed; all 304 unit tests passed; final lint passed. Initial feature tests failed for absent configured paths before implementation. `git diff --check` passed. Independent review reported no blocking findings and independently passed 21 focused tests.

Built artifact: `alloy-sub_ubuntu@24.04-amd64.charm`, SHA-256 `e4921b2d9fd3ac9576f4b3103b4c911a0892515fd535ba51ff4419e15a6ba19e`.
Built/deployed `src/charm.py` SHA-256: `6d33e722bfc8881f3fac5f22eed4ad1088684f9caccf73f5c0d48e222287dea5`.

Live target: `localhost-localhost:admin/machine-observability-v3-reference`, model UUID `e10acf49-369c-4fd0-8c0f-19035f134717`, test unit `alloy-sub/0`, principal `dwellir-observability-reference/0`.

Commands:

```bash
charmcraft pack --destructive-mode --platform ubuntu@24.04:amd64
juju refresh -m localhost-localhost:admin/machine-observability-v3-reference alloy-sub --path /absolute/path/to/alloy-sub_ubuntu@24.04-amd64.charm
juju config -m localhost-localhost:admin/machine-observability-v3-reference alloy-sub \
  'log-files= /tmp/alloy-config-file-test/*.log ; /tmp/alloy-config-file-test/*.log ; ' \
  'path_exclude=/tmp/alloy-config-file-test/excluded.log'
```

Created readable `example.log` and `excluded.log` under `/tmp/alloy-config-file-test/` on the test unit's machine. A preexisting line and later append each appeared exactly once in Loki with filename and principal Juju topology labels. The excluded marker and post-clear marker were absent. Read-only Loki query on `loki-vm/0`:

```bash
curl -fsS -G http://localhost:3100/loki/api/v1/query_range \
  --data-urlencode 'query={juju_application="dwellir-observability-reference",filename=~"/tmp/alloy-config-file-test/.*"}' \
  --data-urlencode limit=100
```

Evidence files on the development machine: `/tmp/alloy-filelogs-follow-query.json`, `/tmp/alloy-filelogs-cleared-query.json`, `/tmp/alloy-filelogs-config-before.json`, `/tmp/alloy-filelogs-final-status.json`, `/tmp/alloy-filelogs-checks.log`, `/tmp/alloy-filelogs-lint.log`, `/tmp/alloy-filelogs-pack.log`.

Restored `log-files=''` and the original `path_exclude=''`; preserved host metrics and other settings. The model remains running with the local artifact installed and the test files retained. No release or production refresh performed. Ubuntu 22.04 build/live checks and independent restart/rotation tests were not executed; saved-position behavior uses the existing Alloy file pipeline unchanged.
