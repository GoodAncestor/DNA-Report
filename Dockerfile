# syntax=docker/dockerfile:1.7
# DNA-Report web front door. Built by GitHub Actions (which has native auth to the
# private biocore/methylask/geneask repos) and pushed to a registry; Coolify on the
# Hetzner box pulls the finished image so the mild-CPU box never runs a build.
#
# SECURITY: the private-repo read token is passed as a BuildKit SECRET
# (--mount=type=secret), NOT a build-arg. A build-arg's resolved value is recorded
# in the image's layer-history metadata (visible via `docker history --no-trunc`)
# even after being unset in the same layer — so a build-arg token leaks the moment
# the image is public. A secret mount is exposed only as a tmpfs file for the
# duration of that one RUN and never lands in any layer or the history. The image
# is therefore safe to publish publicly.
FROM python:3.12-slim

# git needed to resolve the private git-source deps at build time
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . /app

# Install the app + its web extra. The three private deps (biocore, methylask,
# geneask) resolve via git, authenticated by the mounted secret. git's insteadOf
# rewrite is scoped to a throwaway HOME under the mount so no credential config
# persists into the image either.
RUN --mount=type=secret,id=git_token \
    sh -c 'set -e; \
      if [ -s /run/secrets/git_token ]; then \
        export HOME=/tmp/gitcfg; mkdir -p "$HOME"; \
        git config --global url."https://x-access-token:$(cat /run/secrets/git_token)@github.com/".insteadOf "https://github.com/"; \
      fi; \
      pip install --no-cache-dir ".[web]"; \
      rm -rf /tmp/gitcfg'

EXPOSE 8000
# the light front door; heavy work is enqueued to workers, so 2 workers is plenty
CMD ["uvicorn", "dnareport.web:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
