# DNA-Report web front door. Built by GitHub Actions (which has native auth to the
# private biocore/methylask/geneask repos) and pushed to a registry; Coolify on the
# Hetzner box pulls the finished image so the mild-CPU box never runs a build.
FROM python:3.12-slim

# git needed to resolve the private git-source deps at build time
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . /app

# Install the app + its web extra. The three private deps (biocore, methylask,
# geneask) resolve via git; Actions injects a credential for the build (see the
# workflow). No secret is baked into the image — the build arg is used only
# during pip install and not persisted in the final layer's environment.
ARG GIT_TOKEN=""
RUN if [ -n "$GIT_TOKEN" ]; then \
        git config --global url."https://x-access-token:${GIT_TOKEN}@github.com/".insteadOf "https://github.com/"; \
    fi \
    && pip install --no-cache-dir ".[web]" \
    && git config --global --unset-all url."https://x-access-token:${GIT_TOKEN}@github.com/".insteadOf || true

EXPOSE 8000
# the light front door; heavy work is enqueued to workers, so 2 workers is plenty
CMD ["uvicorn", "dnareport.web:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
