#!/usr/bin/env bash
set -euo pipefail

die() {
  echo "::error::$*" >&2
  exit 1
}

cleanup() {
  local path
  for path in \
    "${RELEASE_ACTIONS_ASSETS_FILE:-}" \
    "${RELEASE_ACTIONS_MISSING_FILE:-}" \
    "${RELEASE_ACTIONS_SEEN_FILE:-}"; do
    if [ -n "$path" ]; then
      rm -f "$path"
    fi
  done
}

trap cleanup EXIT

require_env() {
  local name="$1"
  [ -n "${!name:-}" ] || die "$name is required"
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "missing required command: $1"
}

validate_boolean() {
  case "$2" in
    true|false) ;;
    *) die "$1 must be true or false" ;;
  esac
}

trim_cr() {
  printf '%s' "${1%$'\r'}"
}

lowercase() {
  printf '%s' "$1" | tr '[:upper:]' '[:lower:]'
}

api() {
  gh api \
    -H "Accept: application/vnd.github+json" \
    -H "X-GitHub-Api-Version: 2026-03-10" \
    "$@"
}

sha256_file() {
  shasum -a 256 "$1" | awk '{print $1}'
}

expand_assets() {
  local output="$1"
  local pattern match name digest
  : >"$output"

  while IFS= read -r pattern || [ -n "$pattern" ]; do
    pattern="$(trim_cr "$pattern")"
    [ -n "$pattern" ] || continue

    local matched=0
    while IFS= read -r match; do
      [ -n "$match" ] || continue
      [ -f "$match" ] || die "release asset is not a regular file: $match"

      case "$match" in
        *$'\t'*|*$'\n'*|*$'\r'*)
          die "release asset path contains unsupported control characters: $match"
          ;;
      esac

      name="${match##*/}"
      case "$name" in
        .*|*.|*[!A-Za-z0-9._+-]*|"")
          die "release asset basename is not stable on GitHub: $name"
          ;;
      esac

      if awk -F '\t' -v name="$name" '$1 == name { found=1 } END { exit !found }' "$output"; then
        die "release asset basename is duplicated: $name"
      fi

      digest="$(sha256_file "$match")"
      printf '%s\t%s\t%s\n' "$name" "$match" "$digest" >>"$output"
      matched=1
    done < <(compgen -G "$pattern" || true)

    [ "$matched" -eq 1 ] || die "release asset pattern matched no files: $pattern"
  done <<<"$INPUT_ASSETS"

  [ -s "$output" ] || die "assets must contain at least one matching file"
}

verify_remote_tag() {
  local tag="$1"
  local expected="$2"
  local object=""
  local target=""
  local sha ref

  while read -r sha ref; do
    case "$ref" in
      "refs/tags/$tag") object="$(lowercase "$sha")" ;;
      "refs/tags/$tag^{}") target="$(lowercase "$sha")" ;;
    esac
  done < <(git ls-remote origin "refs/tags/$tag" "refs/tags/$tag^{}")

  [ -n "$object" ] || die "release tag is missing from origin: $tag"
  [ -n "$target" ] || target="$object"
  [ "$target" = "$expected" ] ||
    die "release tag target does not match commit: tag=$target expected=$expected"
}

find_release() {
  local rows tag id draft prerelease immutable url count
  rows="$(
    api \
      --paginate \
      "repos/$GITHUB_REPOSITORY/releases?per_page=100" \
      --jq '.[] | [.tag_name, (.id|tostring), (.draft|tostring), (.prerelease|tostring), (.immutable|tostring), .html_url] | @tsv'
  )" || die "could not list releases for $GITHUB_REPOSITORY"

  count=0
  RELEASE_ID=""
  RELEASE_DRAFT=""
  RELEASE_PRERELEASE=""
  RELEASE_IMMUTABLE=""
  RELEASE_URL=""

  while IFS=$'\t' read -r tag id draft prerelease immutable url || [ -n "$tag" ]; do
    [ "$tag" = "$INPUT_TAG" ] || continue
    count=$((count + 1))
    RELEASE_ID="$id"
    RELEASE_DRAFT="$draft"
    RELEASE_PRERELEASE="$prerelease"
    RELEASE_IMMUTABLE="$immutable"
    RELEASE_URL="$url"
  done <<<"$rows"

  [ "$count" -le 1 ] || die "multiple releases unexpectedly use tag $INPUT_TAG"
  [ "$count" -eq 1 ]
}

expected_asset_row() {
  local name="$1"
  awk -F '\t' -v name="$name" '$1 == name { print; exit }' "$RELEASE_ACTIONS_ASSETS_FILE"
}

verify_release_assets() {
  local allow_missing="$1"
  local rows name state digest expected expected_name expected_path expected_digest
  local seen_name

  : >"$RELEASE_ACTIONS_MISSING_FILE"
  : >"$RELEASE_ACTIONS_SEEN_FILE"

  rows="$(
    api \
      --paginate \
      "repos/$GITHUB_REPOSITORY/releases/$RELEASE_ID/assets?per_page=100" \
      --jq '.[] | [.name, .state, (.digest // "")] | @tsv'
  )" || die "could not list assets for release $INPUT_TAG"

  while IFS=$'\t' read -r name state digest || [ -n "$name" ]; do
    [ -n "$name" ] || continue
    expected="$(expected_asset_row "$name")"
    [ -n "$expected" ] || die "release $INPUT_TAG contains unexpected asset: $name"

    IFS=$'\t' read -r expected_name expected_path expected_digest <<<"$expected"
    [ "$state" = "uploaded" ] ||
      die "release asset is not fully uploaded: $name (state=$state)"
    [ "$digest" = "sha256:$expected_digest" ] ||
      die "release asset digest mismatch: $name"

    printf '%s\n' "$name" >>"$RELEASE_ACTIONS_SEEN_FILE"
  done <<<"$rows"

  while IFS=$'\t' read -r expected_name expected_path expected_digest; do
    seen_name="$(
      awk -v name="$expected_name" '$0 == name { print; exit }' "$RELEASE_ACTIONS_SEEN_FILE"
    )"
    if [ -z "$seen_name" ]; then
      if [ "$allow_missing" = "true" ]; then
        printf '%s\t%s\t%s\n' \
          "$expected_name" "$expected_path" "$expected_digest" \
          >>"$RELEASE_ACTIONS_MISSING_FILE"
      else
        die "release $INPUT_TAG is missing required asset: $expected_name"
      fi
    fi
  done <"$RELEASE_ACTIONS_ASSETS_FILE"
}

upload_missing_assets() {
  local name path digest
  while IFS=$'\t' read -r name path digest; do
    [ -n "$name" ] || continue
    if ! gh release upload "$INPUT_TAG" "$path" --repo "$GITHUB_REPOSITORY"; then
      die "failed to upload release asset: $name"
    fi
  done <"$RELEASE_ACTIONS_MISSING_FILE"
}

create_draft_release() {
  local title
  local -a args

  title="${INPUT_TITLE:-$INPUT_TAG}"
  args=(
    -X POST
    "repos/$GITHUB_REPOSITORY/releases"
    -f "tag_name=$INPUT_TAG"
    -f "target_commitish=$INPUT_COMMIT"
    -f "name=$title"
    -F "draft=true"
    -F "prerelease=${INPUT_PRERELEASE:-false}"
    -F "generate_release_notes=${INPUT_GENERATE_NOTES:-false}"
  )

  if [ -n "${INPUT_NOTES_FILE:-}" ]; then
    args+=(-F "body=@$INPUT_NOTES_FILE")
  fi

  if ! api "${args[@]}" --silent >/dev/null; then
    return 1
  fi
}

publish_draft_release() {
  local make_latest

  case "${INPUT_LATEST:-automatic}" in
    automatic)
      if [ "${INPUT_PRERELEASE:-false}" = "true" ]; then
        make_latest="false"
      else
        make_latest="legacy"
      fi
      ;;
    true)
      [ "${INPUT_PRERELEASE:-false}" = "false" ] ||
        die "prerelease releases cannot be marked latest"
      make_latest="true"
      ;;
    false) make_latest="false" ;;
  esac

  api \
    -X PATCH \
    "repos/$GITHUB_REPOSITORY/releases/$RELEASE_ID" \
    -F "draft=false" \
    -F "prerelease=${INPUT_PRERELEASE:-false}" \
    -f "make_latest=$make_latest" \
    --silent >/dev/null ||
    die "failed to publish draft release $INPUT_TAG"
}

verify_published_release() {
  find_release || die "published release disappeared: $INPUT_TAG"

  [ "$RELEASE_DRAFT" = "false" ] ||
    die "release remained a draft after publish: $INPUT_TAG"
  [ "$RELEASE_PRERELEASE" = "${INPUT_PRERELEASE:-false}" ] ||
    die "release prerelease state does not match requested state: $INPUT_TAG"
  [ "$RELEASE_IMMUTABLE" = "true" ] ||
    die "published release is not immutable: $INPUT_TAG"

  verify_release_assets "false"
}

set_outputs() {
  local state="$1"
  {
    echo "state=$state"
    echo "release-url=$RELEASE_URL"
  } >>"$GITHUB_OUTPUT"
}

preflight() {
  require_command git
  require_command gh
  require_command awk
  require_command shasum
  require_command tr

  require_env GITHUB_REPOSITORY
  require_env GITHUB_OUTPUT
  require_env GH_TOKEN
  require_env INPUT_TAG
  require_env INPUT_COMMIT
  require_env INPUT_ASSETS

  case "$GITHUB_REPOSITORY" in
    */*/*|/*|*/|*".."*|*[!A-Za-z0-9._/-]*|"")
      die "GITHUB_REPOSITORY must be owner/name"
      ;;
    */*) ;;
    *) die "GITHUB_REPOSITORY must be owner/name" ;;
  esac

  git check-ref-format "refs/tags/$INPUT_TAG" >/dev/null 2>&1 ||
    die "tag is not a valid Git tag: $INPUT_TAG"

  case "$INPUT_COMMIT" in
    *[!0-9A-Fa-f]*|"") die "commit must be a full 40-character SHA" ;;
  esac
  [ "${#INPUT_COMMIT}" -eq 40 ] || die "commit must be a full 40-character SHA"
  INPUT_COMMIT="$(lowercase "$INPUT_COMMIT")"

  validate_boolean "generate-notes" "${INPUT_GENERATE_NOTES:-false}"
  validate_boolean "prerelease" "${INPUT_PRERELEASE:-false}"
  case "${INPUT_LATEST:-automatic}" in
    automatic|true|false) ;;
    *) die "latest must be automatic, true, or false" ;;
  esac
  if [ "${INPUT_PRERELEASE:-false}" = "true" ] && [ "${INPUT_LATEST:-automatic}" = "true" ]; then
    die "prerelease releases cannot be marked latest"
  fi

  if [ -n "${INPUT_NOTES_FILE:-}" ]; then
    [ -f "$INPUT_NOTES_FILE" ] || die "notes-file does not exist: $INPUT_NOTES_FILE"
    [ "${INPUT_GENERATE_NOTES:-false}" = "false" ] ||
      die "notes-file and generate-notes are mutually exclusive"
  fi

  RELEASE_ACTIONS_ASSETS_FILE="$(mktemp)"
  RELEASE_ACTIONS_MISSING_FILE="$(mktemp)"
  RELEASE_ACTIONS_SEEN_FILE="$(mktemp)"
  export RELEASE_ACTIONS_ASSETS_FILE RELEASE_ACTIONS_MISSING_FILE RELEASE_ACTIONS_SEEN_FILE

  expand_assets "$RELEASE_ACTIONS_ASSETS_FILE"
  verify_remote_tag "$INPUT_TAG" "$INPUT_COMMIT"
}

main() {
  local state

  preflight

  if find_release; then
    [ "$RELEASE_PRERELEASE" = "${INPUT_PRERELEASE:-false}" ] ||
      die "existing release prerelease state does not match requested state: $INPUT_TAG"

    if [ "$RELEASE_DRAFT" = "false" ]; then
      [ "$RELEASE_IMMUTABLE" = "true" ] ||
        die "existing published release is not immutable: $INPUT_TAG"
      verify_release_assets "false"
      set_outputs "existing"
      echo "::notice::verified existing immutable release $INPUT_TAG"
      exit 0
    fi

    verify_release_assets "true"
    state="resumed-draft"
  else
    if ! create_draft_release; then
      # A concurrent publisher may have created the same release after our lookup.
      find_release || die "failed to create draft release $INPUT_TAG"
      [ "$RELEASE_PRERELEASE" = "${INPUT_PRERELEASE:-false}" ] ||
        die "concurrent release prerelease state does not match requested state: $INPUT_TAG"

      if [ "$RELEASE_DRAFT" = "false" ]; then
        [ "$RELEASE_IMMUTABLE" = "true" ] ||
          die "concurrent published release is not immutable: $INPUT_TAG"
        verify_release_assets "false"
        set_outputs "existing"
        echo "::notice::verified concurrently published immutable release $INPUT_TAG"
        exit 0
      fi
    fi

    find_release || die "created draft release could not be found: $INPUT_TAG"
    verify_release_assets "true"
    state="created"
  fi

  upload_missing_assets
  verify_release_assets "false"
  publish_draft_release
  verify_published_release
  set_outputs "$state"
  echo "::notice::published immutable release $INPUT_TAG ($state)"
}

main "$@"
