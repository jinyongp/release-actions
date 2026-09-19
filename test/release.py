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
                return candidate[len(prefix):]
    return default


if not args:
    sys.exit(1)

if args[0] == "api":
    endpoint = next((arg for arg in args[1:] if arg.startswith("repos/")), "")
    if "/releases?per_page=100" in endpoint:
        release = state.get("release")
        if release:
            print("\t".join([
                release["tag"],
                str(release["id"]),
                str(release["draft"]).lower(),
                str(release["prerelease"]).lower(),
                str(release["immutable"]).lower(),
                release["url"],
            ]))
        sys.exit(0)

    if endpoint.endswith("/assets?per_page=100"):
        release = state.get("release")
        for asset in release.get("assets", []) if release else []:
            print("\t".join([asset["name"], asset["state"], asset["digest"]]))
        sys.exit(0)

    if endpoint.endswith("/releases/42") and "-X" not in args:
        release = state.get("release")
        if not release:
            sys.exit(1)
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
