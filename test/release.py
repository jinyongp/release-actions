#!/usr/bin/env python3
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "release.sh"

FAKE_GH = r"""#!/usr/bin/env python3
import hashlib
import json
import os
from pathlib import Path
import sys

state_path = Path(os.environ["FAKE_GH_STATE"])
state = json.loads(state_path.read_text())
args = sys.argv[1:]


def save():
    state_path.write_text(json.dumps(state))


def field(name, default=None):
    prefix = name + "="
    for index, arg in enumerate(args):
        if arg in ("-f", "-F") and index + 1 < len(args):
            candidate = args[index + 1]
            if candidate.startswith(prefix):
                value = candidate[len(prefix):]
                if arg == "-F" and value.startswith("@"):
                    return Path(value[1:]).read_text()
                return value
    return default


if not args:
    sys.exit(1)

if args[:2] == ["repo", "view"]:
    explicit_repository = len(args) > 2 and not args[2].startswith("-")
    if explicit_repository:
        print(state.get("repository", "owner/repo"))
    else:
        print(os.environ.get("GH_REPO", state.get("repository", "owner/repo")))
    sys.exit(0)

if args[:2] == ["release", "view"]:
    if state.get("release_view_error"):
        print(state["release_view_error"], file=sys.stderr)
        sys.exit(1)
    tag = args[2]
    release = state.get("release")
    if not release or release["tag"] != tag:
        print("release not found", file=sys.stderr)
        sys.exit(1)
    print("\t".join([
        str(release["id"]),
        str(release["draft"]).lower(),
        str(release["prerelease"]).lower(),
        str(release["immutable"]).lower(),
        release["url"],
    ]))
    sys.exit(0)

if args[0] == "api":
    endpoint = next((arg for arg in args[1:] if arg.startswith("repos/")), "")
    if endpoint.endswith("/assets?per_page=100"):
        release = state.get("release")
        for asset in release.get("assets", []) if release else []:
            print("\t".join([asset["name"], asset["state"], asset["digest"]]))
        sys.exit(0)

    if endpoint.endswith("/releases/42") and "-X" not in args:
        release = state.get("release")
        if not release:
            sys.exit(1)
        jq = args[args.index("--jq") + 1] if "--jq" in args else ""
        if jq == '.name // ""':
            print(release.get("name", ""))
            sys.exit(0)
        if jq == '.body // ""':
            print(release.get("body", ""))
            sys.exit(0)
        print("\t".join([
            str(release["id"]),
            str(release["draft"]).lower(),
            str(release["prerelease"]).lower(),
            str(release["immutable"]).lower(),
            release["url"],
        ]))
        sys.exit(0)

    if "-X" in args and "POST" in args:
        if state.get("release"):
            sys.exit(1)
        state["release"] = {
            "id": 42,
            "tag": field("tag_name"),
            "name": field("name", field("tag_name")),
            "body": field("body", ""),
            "draft": True,
            "prerelease": field("prerelease") == "true",
            "immutable": False,
            "url": "https://example.invalid/release/42",
            "assets": [],
        }
        save()
        print("\t".join([
            str(state["release"]["id"]),
            str(state["release"]["draft"]).lower(),
            str(state["release"]["prerelease"]).lower(),
            str(state["release"]["immutable"]).lower(),
            state["release"]["url"],
        ]))
        sys.exit(0)

    if "-X" in args and "PATCH" in args:
        release = state["release"]
        release["draft"] = field("draft") == "true"
        release["prerelease"] = field("prerelease") == "true"
        if not release["draft"]:
            release["immutable"] = state.get("immutable_enabled", True)
        save()
        if state.get("concurrent_publish"):
            state["concurrent_publish"] = False
            save()
            sys.exit(1)
        sys.exit(0)

    sys.exit(1)

if args[:2] == ["release", "upload"]:
    tag = args[2]
    path = Path(args[3])
    release = state["release"]
    if release["tag"] != tag or not release["draft"]:
        sys.exit(1)
    name = path.name
    if any(asset["name"] == name for asset in release["assets"]):
        sys.exit(1)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    release["assets"].append({
        "name": name,
        "state": "uploaded",
        "digest": "sha256:" + digest,
    })
    concurrent = state.get("concurrent_upload_name")
    if concurrent == name:
        state["concurrent_upload_name"] = None
        save()
        sys.exit(1)
    save()
    sys.exit(0)

sys.exit(1)
"""


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git(*args, cwd=None):
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        text=True,
        capture_output=True,
        check=True,
    )


def setup_source(tmp):
    remote = tmp / "remote.git"
    work = tmp / "work"
    subprocess.run(
        ["git", "init", "--bare", "--initial-branch=main", str(remote)],
        check=True,
        capture_output=True,
    )
    subprocess.run(["git", "clone", str(remote), str(work)], check=True, capture_output=True)
    for key, value in [
        ("user.name", "test"),
        ("user.email", "test@example.invalid"),
        ("commit.gpgsign", "false"),
        ("tag.gpgSign", "false"),
        ("core.hooksPath", "/dev/null"),
    ]:
        git("config", key, value, cwd=work)

    (work / "README.md").write_text("fixture\n")
    git("add", "README.md", cwd=work)
    git("commit", "-m", "fixture", cwd=work)
    git("push", "origin", "main", cwd=work)
    git("tag", "v1.0.0", cwd=work)
    git("push", "origin", "v1.0.0", cwd=work)
    commit = git("rev-parse", "HEAD", cwd=work).stdout.strip()
    return work, commit


def run_case(work, fakebin, tmp, commit, assets, state, **overrides):
    state_path = tmp / "state.json"
    state_path.write_text(json.dumps(state))
    output = tmp / "output.txt"
    output.write_text("")

    env = dict(os.environ)
    env.update({
        "PATH": str(fakebin) + os.pathsep + env["PATH"],
        "FAKE_GH_STATE": str(state_path),
        "GITHUB_REPOSITORY": "owner/repo",
        "GITHUB_OUTPUT": str(output),
        "GH_TOKEN": "test-token",
        "INPUT_TAG": "v1.0.0",
        "INPUT_COMMIT": commit,
        "INPUT_ASSETS": assets,
        "INPUT_TITLE": "",
        "INPUT_NOTES_FILE": "",
        "INPUT_GENERATE_NOTES": "false",
        "INPUT_PRERELEASE": "false",
        "INPUT_LATEST": "automatic",
    })
    env.update(overrides)

    result = subprocess.run(
        ["bash", str(SCRIPT)],
        cwd=work,
        env=env,
        text=True,
        capture_output=True,
    )
    return result, json.loads(state_path.read_text()), output.read_text()


def release_state(a, b, *, draft=False, immutable=True, prerelease=False):
    return {
        "immutable_enabled": True,
        "release": {
            "id": 42,
            "tag": "v1.0.0",
            "name": "v1.0.0",
            "body": "",
            "draft": draft,
            "prerelease": prerelease,
            "immutable": immutable,
            "url": "https://example.invalid/release/42",
            "assets": [
                {"name": a.name, "state": "uploaded", "digest": "sha256:" + digest(a)},
                {"name": b.name, "state": "uploaded", "digest": "sha256:" + digest(b)},
            ],
        },
    }


def assetless_release_state(*, draft=False, immutable=True, prerelease=False):
    return {
        "immutable_enabled": True,
        "release": {
            "id": 42,
            "tag": "v1.0.0",
            "name": "v1.0.0",
            "body": "",
            "draft": draft,
            "prerelease": prerelease,
            "immutable": immutable,
            "url": "https://example.invalid/release/42",
            "assets": [],
        },
    }


def require_failure(result, text):
    assert result.returncode != 0, result.stdout
    assert text in result.stderr, result.stderr


def main():
    subprocess.run(["bash", "-n", str(SCRIPT)], check=True)

    metadata = (ROOT / "action.yml").read_text()
    assert "using: composite" in metadata
    assert "id: release" in metadata
    for input_name in ["tag", "commit", "assets", "title", "notes-file", "generate-notes", "prerelease", "latest", "token"]:
        assert f"  {input_name}:" in metadata
    for output_name in ["state", "release-url"]:
        assert f"  {output_name}:" in metadata
    assets_contract = metadata.split("  assets:", 1)[1].split("  title:", 1)[0]
    assert "required: false" in assets_contract
    assert 'default: ""' in assets_contract
    assert "homebrew" not in metadata.lower()

    release_workflow = (ROOT / ".github/workflows/release.yml").read_text()
    assert "uses: ./release-action" in release_workflow
    assert "gh release create" not in release_workflow

    script = SCRIPT.read_text()
    assert 'gh release view "$INPUT_TAG"' in script
    assert 'releases?per_page=100' not in script
    for line in metadata.splitlines():
        stripped = line.strip()
        if stripped.startswith("description: "):
            value = stripped[len("description: "):]
            if ": " in value:
                assert value.startswith(('"', "'")), line

    with tempfile.TemporaryDirectory() as directory:
        tmp = Path(directory)
        fakebin = tmp / "bin"
        fakebin.mkdir()
        fake_gh = fakebin / "gh"
        fake_gh.write_text(FAKE_GH)
        fake_gh.chmod(0o755)

        work, commit = setup_source(tmp)
        a = work / "artifact-a.bin"
        b = work / "artifact-b.bin"
        a.write_bytes(b"aaa\n")
        b.write_bytes(b"bbb\n")
        assets = str(work / "artifact-*.bin")

        result, state, output = run_case(
            work, fakebin, tmp, commit, assets, release_state(a, b)
        )
        assert result.returncode == 0, result.stderr
        assert "state=existing" in output

        draft = release_state(a, b, draft=True, immutable=False)
        draft["release"]["assets"] = draft["release"]["assets"][:1]
        result, state, output = run_case(work, fakebin, tmp, commit, assets, draft)
        assert result.returncode == 0, result.stderr
        assert state["release"]["draft"] is False
        assert state["release"]["immutable"] is True
        assert len(state["release"]["assets"]) == 2
        assert "state=resumed-draft" in output

        result, state, output = run_case(
            work,
            fakebin,
            tmp,
            commit,
            assets,
            {"immutable_enabled": True, "release": None},
        )
        assert result.returncode == 0, result.stderr
        assert state["release"]["draft"] is False
        assert state["release"]["immutable"] is True
        assert len(state["release"]["assets"]) == 2
        assert "state=created" in output

        result, _, _ = run_case(
            work,
            fakebin,
            tmp,
            commit,
            assets,
            {
                "immutable_enabled": True,
                "release": None,
                "release_view_error": "simulated provider failure",
            },
        )
        require_failure(result, "could not look up release v1.0.0: simulated provider failure")

        result, state, output = run_case(
            work,
            fakebin,
            tmp,
            commit,
            "",
            {"immutable_enabled": True, "release": None},
        )
        assert result.returncode == 0, result.stderr
        assert state["release"]["assets"] == []
        assert state["release"]["immutable"] is True
        assert "state=created" in output

        result, _, output = run_case(
            work, fakebin, tmp, commit, "", assetless_release_state()
        )
        assert result.returncode == 0, result.stderr
        assert "state=existing" in output

        unexpected_assetless = assetless_release_state()
        unexpected_assetless["release"]["assets"].append({
            "name": "unexpected.bin",
            "state": "uploaded",
            "digest": "sha256:" + ("1" * 64),
        })
        result, _, _ = run_case(
            work, fakebin, tmp, commit, "", unexpected_assetless
        )
        require_failure(result, "unexpected asset")

        mismatch = release_state(a, b)
        mismatch["release"]["assets"][0]["digest"] = "sha256:" + ("0" * 64)
        result, _, _ = run_case(work, fakebin, tmp, commit, assets, mismatch)
        require_failure(result, "digest mismatch")

        unexpected = release_state(a, b)
        unexpected["release"]["assets"].append({
            "name": "unexpected.bin",
            "state": "uploaded",
            "digest": "sha256:" + ("1" * 64),
        })
        result, _, _ = run_case(work, fakebin, tmp, commit, assets, unexpected)
        require_failure(result, "unexpected asset")

        nonimmutable = release_state(a, b, immutable=False)
        result, _, _ = run_case(work, fakebin, tmp, commit, assets, nonimmutable)
        require_failure(result, "existing published release is not immutable")

        prerelease = release_state(a, b, prerelease=True)
        result, _, _ = run_case(work, fakebin, tmp, commit, assets, prerelease)
        require_failure(result, "prerelease state does not match")

        result, _, _ = run_case(
            work,
            fakebin,
            tmp,
            commit,
            assets,
            {"immutable_enabled": False, "release": None},
        )
        require_failure(result, "published release is not immutable")

        result, _, _ = run_case(
            work,
            fakebin,
            tmp,
            "0" * 40,
            assets,
            {"immutable_enabled": True, "release": None},
        )
        require_failure(result, "release tag target does not match commit")

        result, _, _ = run_case(
            work,
            fakebin,
            tmp,
            commit,
            str(work / "missing-*"),
            {"immutable_enabled": True, "release": None},
        )
        require_failure(result, "matched no files")

        one = work / "one"
        two = work / "two"
        one.mkdir()
        two.mkdir()
        (one / "same.bin").write_bytes(b"one")
        (two / "same.bin").write_bytes(b"two")
        duplicate_assets = str(one / "same.bin") + "\n" + str(two / "same.bin")
        result, _, _ = run_case(
            work,
            fakebin,
            tmp,
            commit,
            duplicate_assets,
            {"immutable_enabled": True, "release": None},
        )
        require_failure(result, "basename is duplicated")

        identity_mismatch = {"immutable_enabled": True, "release": None, "repository": "other/repo"}
        result, _, _ = run_case(work, fakebin, tmp, commit, assets, identity_mismatch)
        require_failure(result, "checkout repository does not match GITHUB_REPOSITORY")

        result, state, output = run_case(
            work,
            fakebin,
            tmp,
            commit,
            assets,
            {"immutable_enabled": True, "release": None},
            GH_REPO="other/repo",
        )
        assert result.returncode == 0, result.stderr
        assert state["release"]["immutable"] is True
        assert "state=created" in output

        stale_title = release_state(a, b, draft=True, immutable=False)
        stale_title["release"]["name"] = "stale title"
        result, _, _ = run_case(work, fakebin, tmp, commit, assets, stale_title)
        require_failure(result, "draft release title does not match")

        notes = work / "release-notes.md"
        notes.write_text("expected notes\n")
        stale_notes = release_state(a, b, draft=True, immutable=False)
        stale_notes["release"]["body"] = "different notes"
        result, _, _ = run_case(
            work,
            fakebin,
            tmp,
            commit,
            assets,
            stale_notes,
            INPUT_NOTES_FILE=str(notes),
        )
        require_failure(result, "draft release notes do not match")

        generated_notes = release_state(a, b, draft=True, immutable=False)
        generated_notes["release"]["body"] = "existing generated notes"
        result, state, output = run_case(
            work,
            fakebin,
            tmp,
            commit,
            assets,
            generated_notes,
            INPUT_GENERATE_NOTES="true",
        )
        assert result.returncode == 0, result.stderr
        assert state["release"]["body"] == "existing generated notes"
        assert "state=resumed-draft" in output

        concurrent = {
            "immutable_enabled": True,
            "release": None,
            "concurrent_upload_name": a.name,
        }
        result, state, output = run_case(work, fakebin, tmp, commit, assets, concurrent)
        assert result.returncode == 0, result.stderr
        assert state["release"]["immutable"] is True
        assert len(state["release"]["assets"]) == 2
        assert "uploaded concurrently" in result.stdout
        assert "state=created" in output

        concurrent_publish = {
            "immutable_enabled": True,
            "release": None,
            "concurrent_publish": True,
        }
        result, state, output = run_case(
            work, fakebin, tmp, commit, assets, concurrent_publish
        )
        assert result.returncode == 0, result.stderr
        assert state["release"]["draft"] is False
        assert state["release"]["immutable"] is True
        assert "published concurrently" in result.stdout
        assert "state=created" in output

        result, _, _ = run_case(
            work,
            fakebin,
            tmp,
            commit,
            assets,
            {"immutable_enabled": True, "release": None},
            INPUT_PRERELEASE="true",
            INPUT_LATEST="true",
        )
        require_failure(result, "prerelease releases cannot be marked latest")

    print("release-actions regression passed")


if __name__ == "__main__":
    main()
