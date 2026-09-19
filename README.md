# release-actions

Reusable GitHub Action for publishing caller-built artifacts as an immutable GitHub Release.

The public API is the repository root:

```yaml
- uses: jinyongp/release-actions@<full-commit-sha> # v1.0.0
```

The action owns the GitHub Release lifecycle only. Product build commands, version
selection, tag creation, release triggers, artifact naming, and changelog policy stay in
the caller repository.

## Requirements

Before using the action:

- enable **immutable releases** for the caller repository;
- give the release job `contents: write`;
- create and push the Git tag before invoking the action;
- check out the caller repository so its authenticated `origin` is available for tag
  provenance verification;
- build every release asset before invoking the action.

The action uses the caller's `github.token` by default. An explicit `token` input may
be supplied when the caller intentionally needs a different repository token.

GitHub's repository-setting endpoint for checking whether immutable releases are enabled
requires repository Administration (read), which the normal Actions `GITHUB_TOKEN`
cannot request. The action therefore treats immutable releases as a required repository
precondition and verifies the resulting published Release's immutable state immediately
after publication. If the repository setting is not enabled, publication fails the
postcondition check.

## Usage

```yaml
jobs:
  release:
    runs-on: ubuntu-24.04
    permissions:
      contents: write

    steps:
      - uses: actions/checkout@<full-commit-sha>
        with:
          fetch-depth: 0

      - name: Build release assets
        run: ./scripts/build-release.sh

      - name: Publish GitHub Release
        id: release
        uses: jinyongp/release-actions@<full-commit-sha> # v1.0.0
        with:
          tag: ${{ github.ref_name }}
          commit: ${{ github.sha }}
          assets: |
            dist/*.tar.gz
            dist/*.zip
          generate-notes: "true"
```

Always pin cross-repository actions to a full commit SHA. The adjacent `vX.Y.Z`
comment is version metadata for review and update tooling; the SHA is the executable
identity.

## Inputs

| Input | Required | Default | Meaning |
| --- | --- | --- | --- |
| `tag` | yes | — | Existing remote Git tag to publish. |
| `commit` | yes | — | Full 40-character commit SHA that the remote tag must resolve to. |
| `assets` | yes | — | Newline-separated file paths or glob patterns. Every pattern must match at least one regular file. |
| `title` | no | tag | Release title. |
| `notes-file` | no | empty | File containing release notes. Mutually exclusive with `generate-notes: "true"`. |
| `generate-notes` | no | `false` | Ask GitHub to generate release notes. |
| `prerelease` | no | `false` | Publish the release as a prerelease. |
| `latest` | no | `automatic` | `automatic`, `true`, or `false`. Automatic uses GitHub's legacy semantic/date selection for stable releases. |
| `token` | no | caller `github.token` | Explicit GitHub token override. |

A prerelease cannot use `latest: "true"`.

Asset basenames must be stable on GitHub: letters, numbers, dots, underscores, plus,
and dash are supported; leading/trailing dots and control characters are rejected.
Duplicate basenames are rejected even when the files come from different directories.

## Outputs

| Output | Meaning |
| --- | --- |
| `state` | `created`, `resumed-draft`, or `existing`. |
| `release-url` | URL of the published GitHub Release. |

## Lifecycle guarantees

Every invocation verifies the remote tag before release mutation.

For a new release, the action:

1. creates a draft release;
2. uploads caller-produced assets;
3. verifies the exact asset set and SHA-256 digests;
4. publishes the draft;
5. verifies that the published release is immutable;
6. verifies the exact assets again.

If a matching draft already exists, the action accepts already-uploaded assets only when
their GitHub SHA-256 digest matches the local file. Missing assets are uploaded; unexpected,
partial, or mismatched assets fail without being deleted or overwritten.

If a published release already exists, it is an idempotent no-op only when:

- the remote tag still resolves to the requested commit;
- prerelease state matches;
- the release is immutable;
- the release contains exactly the requested assets;
- every asset digest matches the local file.

Any mismatch fails. The action never retargets a published release, deletes an existing
asset, uses `--clobber`, or replaces a tagged artifact.

## Release notes

Release-note policy remains caller-owned.

Use either:

```yaml
with:
  notes-file: dist/release-notes.md
```

or:

```yaml
with:
  generate-notes: "true"
```

The two modes are mutually exclusive.

## Development

The regression suite uses a temporary Git remote and a stateful fake GitHub CLI. It does
not create live releases:

```sh
python3 test/release.py
```

CI runs the same regression on Linux and macOS.

## License

MIT
