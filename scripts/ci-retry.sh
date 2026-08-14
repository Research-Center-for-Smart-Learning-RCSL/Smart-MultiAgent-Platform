#!/usr/bin/env bash
# Re-run a command that failed only because a network did.
#
# SCOPE. This is for dependency installs and image pulls, whose failures are
# dominated by registry/CDN transients. It must NOT wrap test, lint, build or
# gate commands: a deterministic failure would burn three attempts to report the
# same red, and a genuinely flaky test has to stay visible rather than be
# papered over here.
#
# It cannot help with the failure that motivated it -- an action tarball that
# 500s is fetched during "Set up job", before any step runs. That case is
# handled one level up, by .github/workflows/ci-rerun-on-infra-failure.yml.
#
# Always invoke as `bash scripts/ci-retry.sh ...` rather than executing it
# directly: the repo is also checked out on Windows, where the exec bit does not
# survive, so relying on it would make this work or not depending on who cloned.
set -euo pipefail

attempts="${CI_RETRY_ATTEMPTS:-3}"
delay="${CI_RETRY_DELAY:-10}"

if [ "$#" -eq 0 ]; then
  echo "ci-retry: no command given" >&2
  exit 2
fi

attempt=1
while :; do
  # Captured explicitly rather than read from $? after an `if`, which under
  # `set -e` reports the condition's status and is easy to get subtly wrong.
  status=0
  "$@" || status=$?

  if [ "$status" -eq 0 ]; then
    exit 0
  fi

  if [ "$attempt" -ge "$attempts" ]; then
    echo "ci-retry: '$*' failed on attempt ${attempt}/${attempts} with exit ${status} — giving up" >&2
    exit "$status"
  fi

  echo "ci-retry: '$*' exited ${status} on attempt ${attempt}/${attempts} — retrying in ${delay}s" >&2
  sleep "$delay"
  delay=$((delay * 2))
  attempt=$((attempt + 1))
done
