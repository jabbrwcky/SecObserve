# Unified development container for SecObserve.
# Provides the Python/Poetry backend toolchain (incl. code quality tools) and
# Node/npm frontend toolchain so all CONTRIBUTING.md code checks can run in one
# container. Node itself is installed by the devcontainer "node" feature.
FROM python:3.14-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    POETRY_NO_INTERACTION=1 \
    # Install dependencies into the image's system interpreter, not an in-project
    # .venv (which would be shadowed by the bind-mounted workspace).
    POETRY_VIRTUALENVS_CREATE=false \
    # Disable the keyring backend: its availability probe crashes with SIGILL on
    # some platforms (e.g. arm64 under podman) and is not needed for installs.
    PYTHON_KEYRING_BACKEND=keyring.backends.null.Keyring

# System build/runtime dependencies for the backend (psycopg, mysql client,
# git for pre-commit, postgres client for dbshell, etc.).
# kics-scan ignore-block
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        ca-certificates \
        curl \
        default-libmysqlclient-dev \
        git \
        libffi-dev \
        libpq-dev \
        openssh-client \
        pkg-config \
        postgresql-client \
        procps \
        socat \
        sudo \
    && rm -rf /var/lib/apt/lists/*

# SSH agent relay: forwards the in-container agent socket to the host agent
# exposed on host.containers.internal (see the script header for the rationale).
COPY docker/devcontainer/ssh-agent-relay.sh /usr/local/bin/ssh-agent-relay.sh
RUN chmod +x /usr/local/bin/ssh-agent-relay.sh

# Pre-seed known host keys so `git`/`ssh` to these hosts don't prompt to accept
# the host key on first connect (SSH auth itself uses the forwarded host agent).
RUN mkdir -p /etc/ssh \
    && ssh-keyscan github.com gitlab.com bitbucket.org >> /etc/ssh/ssh_known_hosts 2>/dev/null

# Non-root development user. UID/GID 1000 match the common host default; the
# devcontainer adjusts them to the host user at startup (updateRemoteUserUID).
ARG USERNAME=dc
ARG USER_UID=1000
ARG USER_GID=1000
RUN groupadd --gid ${USER_GID} ${USERNAME} \
    && useradd --uid ${USER_UID} --gid ${USER_GID} --create-home --shell /bin/bash ${USERNAME} \
    && echo "${USERNAME} ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/${USERNAME} \
    && chmod 0440 /etc/sudoers.d/${USERNAME} \
    && mkdir -p /var/lib/huey \
    && chown -R ${USERNAME}:${USERNAME} /var/lib/huey

# Poetry (pinned to the version used by the project, see backend/poetry_requirements.txt).
COPY backend/poetry_requirements.txt /tmp/poetry_requirements.txt
RUN pip install --no-cache-dir -r /tmp/poetry_requirements.txt

# Install backend dependencies including the "dev" and "code_quality" extras so
# black, isort, flake8, mypy and pylint are available for the code checks.
COPY backend/pyproject.toml backend/poetry.lock /tmp/backend/
RUN cd /tmp/backend \
    && poetry install --extras "dev code_quality" --no-root \
    && rm -rf /tmp/backend /tmp/poetry_requirements.txt

# Put the project-pinned frontend binaries (prettier, eslint, vite, ...) on PATH
# so CONTRIBUTING.md commands such as `prettier -w src` work after `npm install`.
ENV PATH="/workspace/frontend/node_modules/.bin:${PATH}"

WORKDIR /workspace
USER ${USERNAME}
