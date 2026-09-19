#!/usr/bin/env bash
set -euo pipefail

die() {
  echo "::error::$*" >&2
  exit 1
}

cleanup() {
  if [ -n "${RELEASE_ACTIONS_ASSETS_FILE:-}" ]; then
    rm -f "$RELEASE_ACTIONS_ASSETS_FILE"
  fi
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

expand_assets() {
  local output="$1"
  local pattern match name
  : >"$output"

  while IFS= read -r pattern || [ -n "$pattern" ]; do
    pattern="$(trim_cr "$pattern")"
    [ -n "$pattern" ] || continue

    local matched=0
    while IFS= read -r match; do
      [ -n "$match" ] || continue
      [ -f "$match" ] || die "release asset is not a regular file: $match"
      name="${match##*/}"
      if awk -F '\t' -v name="$name" '$1 == name { found=1 } END { exit !found }' "$output"; then
        die "release asset basename is duplicated: $name"
      fi
      printf '%s\t%s\n' "$name" "$match" >>"$output"
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
      "refs/tags/$tag") object="$(printf '%s' "$sha" | tr '[:upper:]' '[:lower:]')" ;;
      "refs/tags/$tag^{}") target="$(printf '%s' "$sha" | tr '[:upper:]' '[:lower:]')" ;;
    esac
  done < <(git ls-remote origin "refs/tags/$tag" "refs/tags/$tag^{}")

  [ -n "$object" ] || die "release tag is missing from origin: $tag"
  [ -n "$target" ] || target="$object"
  [ "$target" = "$expected" ] ||
    die "release tag target does not match commit: tag=$target expected=$expected"
}

require_immutable_releases() {
  if ! gh api \
    -H "Accept: application/vnd.github+json" \
    -H "X-GitHub-Api-Version: 2026-03-10" \
    "repos/$GITHUB_REPOSITORY/immutable-releases" \
    --silent >/dev/null 2>&1; then
    die "immutable releases must be enabled for $GITHUB_REPOSITORY before publishing"
  fi
}

preflight() {
  require_command git
  require_command gh
  require_command awk

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
  INPUT_COMMIT="$(printf '%s' "$INPUT_COMMIT" | tr '[:upper:]' '[:lower:]')"

  validate_boolean "generate-notes" "${INPUT_GENERATE_NOTES:-false}"
  validate_boolean "prerelease" "${INPUT_PRERELEASE:-false}"
  case "${INPUT_LATEST:-automatic}" in
    automatic|true|false) ;;
    *) die "latest must be automatic, true, or false" ;;
  esac

  if [ -n "${INPUT_NOTES_FILE:-}" ]; then
    [ -f "$INPUT_NOTES_FILE" ] || die "notes-file does not exist: $INPUT_NOTES_FILE"
    [ "${INPUT_GENERATE_NOTES:-false}" = "false" ] ||
      die "notes-file and generate-notes are mutually exclusive"
  fi

  RELEASE_ACTIONS_ASSETS_FILE="$(mktemp)"
  export RELEASE_ACTIONS_ASSETS_FILE
  expand_assets "$RELEASE_ACTIONS_ASSETS_FILE"
  verify_remote_tag "$INPUT_TAG" "$INPUT_COMMIT"
  require_immutable_releases
}

main() {
  preflight
  echo "::notice::release preflight passed for $INPUT_TAG"
}

main "$@"
