import re
from pathlib import Path

import yaml

WORKFLOW_DIR = Path(".github/workflows")
RELEASE_WORKFLOW = WORKFLOW_DIR / "release.yml"


def _workflow(path: Path) -> dict:
    return yaml.load(path.read_text(), Loader=yaml.BaseLoader)


def test_workflows_pin_third_party_actions_to_documented_commit_shas():
    action_use = re.compile(r"^\s*uses:\s*([^\s#]+)(?:\s+#\s*(\S+))?", re.MULTILINE)

    for path in sorted(WORKFLOW_DIR.glob("*.yml")):
        for action, version_comment in action_use.findall(path.read_text()):
            assert re.fullmatch(r"[^@]+@[0-9a-f]{40}", action), f"{path}: {action} is not SHA-pinned"
            assert version_comment.startswith("v"), f"{path}: {action} lacks a version comment"


def test_every_checkout_disables_credential_persistence():
    for path in sorted(WORKFLOW_DIR.glob("*.yml")):
        workflow = _workflow(path)
        for job_name, job in workflow["jobs"].items():
            for step in job["steps"]:
                if str(step.get("uses", "")).startswith("actions/checkout@"):
                    assert step.get("with", {}).get("persist-credentials") == "false", (
                        f"{path}: {job_name} checkout persists credentials"
                    )


def test_workflows_declare_least_privilege_permissions():
    charm_tests = _workflow(WORKFLOW_DIR / "charm-tests.yml")
    assert charm_tests["permissions"] == {"contents": "read"}
    assert charm_tests["jobs"]["charm-tests"]["permissions"] == {"contents": "read"}

    release = _workflow(RELEASE_WORKFLOW)
    assert release["permissions"] == {"contents": "read"}
    assert release["jobs"]["build-ubuntu-22"]["permissions"] == {"contents": "read"}
    assert release["jobs"]["build-ubuntu-24"]["permissions"] == {"contents": "read"}
    assert release["jobs"]["release"]["permissions"] == {"actions": "read", "contents": "read"}
    assert release["jobs"]["github-release"]["permissions"] == {
        "actions": "read",
        "contents": "write",
    }


def test_release_is_manual_main_only_and_publish_jobs_depend_on_gated_builds():
    workflow = _workflow(RELEASE_WORKFLOW)
    assert workflow["on"] == {"workflow_dispatch": ""}

    jobs = workflow["jobs"]
    assert jobs["build-ubuntu-22"]["if"] == "github.ref == 'refs/heads/main'"
    assert jobs["build-ubuntu-24"]["if"] == "github.ref == 'refs/heads/main'"
    assert jobs["release"]["needs"] == ["build-ubuntu-22", "build-ubuntu-24"]
    assert jobs["github-release"]["needs"] == ["release"]
    assert "always()" not in RELEASE_WORKFLOW.read_text()


def test_release_serializes_complete_latest_edge_revision_pairs():
    workflow = _workflow(RELEASE_WORKFLOW)

    assert workflow["concurrency"] == {
        "group": "${{ github.workflow }}-latest-edge",
        "cancel-in-progress": "false",
    }
    assert all("concurrency" not in job for job in workflow["jobs"].values())


def test_release_preserves_two_base_artifacts_and_publishes_only_latest_edge():
    text = RELEASE_WORKFLOW.read_text()

    assert "./alloy-sub_ubuntu@22.04-amd64.charm" in text
    assert "./alloy-sub_ubuntu@24.04-amd64.charm" in text
    release_commands = re.findall(r"^\s*[^#\n]*charmcraft\s+(?:upload|release)[^\n]*$", text, re.MULTILINE)
    assert len(release_commands) == 2
    assert all("upload" in command and "--release latest/edge" in command for command in release_commands)
    assert not any(re.search(r"latest/(?:candidate|stable)", command) for command in release_commands)


def test_both_charmhub_revision_outputs_are_positive_integers():
    workflow = _workflow(RELEASE_WORKFLOW)
    release_steps = workflow["jobs"]["release"]["steps"]

    for step_id in ("upload_ubuntu_22", "upload_ubuntu_24"):
        script = next(step["run"] for step in release_steps if step.get("id") == step_id)
        assert '[[ ! "$revision" =~ ^[1-9][0-9]*$ ]]' in script

    metadata_step = next(
        step for step in workflow["jobs"]["github-release"]["steps"] if step.get("id") == "release_meta"
    )
    assert metadata_step["env"] == {
        "CHANNEL": "${{ needs.release.outputs.release_channel }}",
        "REVISION_22": "${{ needs.release.outputs.rev_ubuntu_22 }}",
        "REVISION_24": "${{ needs.release.outputs.rev_ubuntu_24 }}",
    }
    assert metadata_step["run"].count("=~ ^[1-9][0-9]*$") == 2


def test_release_tag_uses_both_revisions_and_existing_tag_must_match_source_sha():
    workflow = _workflow(RELEASE_WORKFLOW)
    github_steps = workflow["jobs"]["github-release"]["steps"]
    metadata = next(step for step in github_steps if step.get("id") == "release_meta")
    assert 'tag="latest-edge-r${REVISION_22}-r${REVISION_24}"' in metadata["run"]

    provenance = next(step for step in github_steps if step["name"] == "Verify existing release tag provenance")
    assert provenance["env"] == {
        "GITHUB_TOKEN": "${{ github.token }}",
        "TAG": "${{ steps.release_meta.outputs.tag }}",
    }
    script = provenance["run"]
    assert "credential.helper=" in script
    assert "x-access-token" in script
    assert "git " in script and "ls-remote" in script
    assert 'tag_ref="refs/tags/${TAG}"' in script
    assert '"${tag_ref}^{}"' in script
    assert '"$existing_sha" != "$GITHUB_SHA"' in script

    release_action = next(step for step in github_steps if str(step.get("uses", "")).startswith("softprops/"))
    assert release_action["with"]["prerelease"] == "true"
    assert release_action["with"]["make_latest"] == "false"
    assert release_action["with"]["fail_on_unmatched_files"] == "true"
