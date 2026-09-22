# actions

Publish an immutable GitHub Release from an existing Git tag.

`actions` owns the GitHub Release lifecycle. The caller owns version selection,
tag creation, build commands, artifact naming, release triggers, and changelog policy.

## Quick start

Create and push the Git tag before invoking the action. The release job needs
`contents: write` and a checkout of the caller repository so the action can bind
the checkout identity, remote tag, and GitHub Release to the same repository.

```yaml
jobs:
  release:
    runs-on: ubuntu-24.04
    permissions:
      contents: write

    steps:
      - uses: actions/checkout@<full-commit-sha>

      - name: Build release assets
        run: ./scripts/build-release.sh

      - name: Publish release
        id: release
        uses: releaseway/actions@<full-commit-sha> # v0.1.2
        with:
          tag: ${{ github.ref_name }}
          commit: ${{ github.sha }}
          assets: |
            dist/*.tar.gz
            dist/*.zip
```

Always pin cross-repository actions to a full commit SHA. The adjacent `vX.Y.Z`
comment is release metadata for humans and dependency tooling.

## Releases without assets

GitHub Action and reusable-workflow repositories often publish a versioned release
without binary assets. Omit `assets` in that case:

```yaml
- name: Publish automation release
  uses: releaseway/actions@<full-commit-sha> # v0.1.2
  with:
    tag: ${{ inputs.tag }}
    commit: ${{ steps.release.outputs.commit }}
    latest: "true"
```

The same provenance, immutability, idempotency, and release-state checks apply whether
or not the release contains assets.

## Requirements

Before publishing:

- enable immutable releases for the caller repository;
- create and push the requested Git tag;
- give the job `contents: write`;
- check out the caller repository with its authenticated `origin` available;
- build any requested assets before invoking the action.

The action uses the caller's `github.token` by default. Supply `token` only when a
different repository credential is intentionally required. The checked-out repository
must resolve to the same `owner/name` as `GITHUB_REPOSITORY`; the action rejects a
different checkout before checking or mutating release state.

Immutable releases are a repository prerequisite, not something this action enables.
The default release token intentionally does not require repository-administration
permission. If immutability is disabled, GitHub can publish the draft as a mutable
release before the action observes `immutable: false` and fails. Enable immutable
releases before the first invocation; a failed run in this state may therefore require
manual cleanup before retrying.

## Inputs

| Input | Required | Default | Meaning |
| --- | --- | --- | --- |
| `tag` | yes | — | Existing remote Git tag to publish. |
| `commit` | yes | — | Full 40-character commit SHA the remote tag must resolve to. |
| `assets` | no | empty | Newline-separated file paths or glob patterns. Every supplied pattern must match at least one regular file. |
| `title` | no | tag | Release title. |
| `notes-file` | no | empty | Release notes file. Mutually exclusive with `generate-notes: "true"`. |
| `generate-notes` | no | `false` | Ask GitHub to generate release notes. |
| `prerelease` | no | `false` | Publish as a prerelease. |
| `latest` | no | `automatic` | `automatic`, `true`, or `false`. |
| `token` | no | caller `github.token` | Explicit GitHub token override. |

A prerelease cannot use `latest: "true"`.

When assets are supplied, each basename must be stable on GitHub and unique within the
release. Leading or trailing dots, unsupported characters, duplicate basenames, and
patterns that match no regular file are rejected.

## Outputs

| Output | Meaning |
| --- | --- |
| `state` | `created`, `resumed-draft`, or `existing`. |
| `release-url` | Published GitHub Release URL. |

## Lifecycle guarantees

Every invocation first verifies that the remote tag resolves to the requested commit.

For a new release, the action creates a draft, uploads any requested assets, verifies
their exact set and SHA-256 digests, publishes the draft, verifies immutability, and
checks the final asset set again.

An existing draft is resumed only when its prerelease state, requested title, and
existing assets are compatible with the invocation. Explicit `notes-file` content
must match exactly after normal shell newline handling, and an invocation that requests
no notes rejects a draft with an existing body. For `generate-notes: "true"`, the
release API does not expose whether an existing body was generated, so the action
preserves that body rather than regenerating or claiming to verify its provenance.

An existing published release is accepted as an idempotent no-op only when its tag
target, prerelease state, immutability, and complete asset set all match the request.
If another invocation uploads the same asset concurrently, a failed upload is accepted
only when the resulting remote asset has the expected name, uploaded state, and SHA-256
digest.

The action never retargets a published release, deletes or replaces an existing asset,
uses `--clobber`, or forcefully repairs mismatched release state.

## Release notes

Release-note policy remains caller-owned. Use either a file:

```yaml
with:
  notes-file: dist/release-notes.md
```

or GitHub-generated notes:

```yaml
with:
  generate-notes: "true"
```

The two modes are mutually exclusive.

## Development

Run the deterministic regression suite with:

```sh
python3 test/release.py
```

The tests use a temporary Git remote and a stateful fake GitHub CLI; they do not create
live releases. CI runs the same regression on Linux and macOS.

## License

MIT
