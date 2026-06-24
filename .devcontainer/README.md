# SecObserve Dev Container

A unified VS Code Dev Container that bundles the **Python/Poetry backend** and
**Node/npm frontend** toolchains in one image, backed by PostgreSQL and OPA, so
every code check in [`CONTRIBUTING.md`](../CONTRIBUTING.md) runs in a single
reproducible environment.

- **Definition:** [`devcontainer.json`](devcontainer.json)
- **Compose stack:** [`../docker-compose-devcontainer.yml`](../docker-compose-devcontainer.yml)
  (services `workspace`, `postgres`, `opa`)
- **Image:** [`../docker/devcontainer/Dockerfile`](../docker/devcontainer/Dockerfile)

Open it with **Dev Containers: Reopen in Container** in VS Code. Backend
dependencies are baked into the image; the frontend `npm install` runs as the
`postCreateCommand`.

> The container runtime can be Docker Desktop or Podman. The SSH agent
> forwarding described below targets the **macOS + Podman** case, which is where
> the host agent socket cannot be shared directly.

---

## Why SSH agent forwarding needs a workaround

Git operations inside the container (clone/fetch/push over SSH) need access to
your **host** SSH agent — keys never enter the container.

The usual mechanism is to bind-mount the host agent's unix socket
(`$SSH_AUTH_SOCK`) into the container. **This does not work on macOS + Podman:**

- The container does not run on macOS directly — it runs inside a Linux VM
  managed by the Podman machine.
- The host agent socket lives on the macOS side and is reached through
  `virtiofs`. Connecting to a **unix socket** over `virtiofs` fails with
  `Operation not supported` — virtiofs forwards files, not live socket
  endpoints.
- Hardware-backed agents such as [Secretive](https://github.com/maxgoedjen/secretive)
  (keys in the Secure Enclave / a YubiKey) make this worse: the key material is
  never exportable, so copying keys into the container is not an option even in
  principle.

**Workaround:** expose the host agent over a loopback **TCP** port, then bridge
that port to a unix socket *inside* the container. TCP traverses the Podman VM
boundary cleanly via the host gateway (`host.containers.internal`); a unix
socket does not.

---

## How the pieces fit together

```
  macOS host                                  │  Podman VM / container
                                              │
  SSH agent (Secretive, ssh-agent, …)         │
        ▲ unix socket ($AGENT_SOCK)           │
        │                                     │
  bin/devcontainer-host-agent-bridge.sh       │
   socat/python: TCP-LISTEN 127.0.0.1:17654   │
        ▲                                     │
        │  TCP                                │
        └─────────  host.containers.internal:17654  ◄──┐
                                              │        │ TCP
                                              │  docker/devcontainer/ssh-agent-relay.sh
                                              │   socat: UNIX-LISTEN /tmp/ssh-agent-relay.sock
                                              │        ▲
                                              │        │ unix socket
                                              │   git / ssh  (SSH_AUTH_SOCK=/tmp/ssh-agent-relay.sock)
```

| Component | Side | Role |
| --- | --- | --- |
| [`bin/devcontainer-host-agent-bridge.sh`](../bin/devcontainer-host-agent-bridge.sh) | host | Bridges `TCP 127.0.0.1:17654` → host agent unix socket. Uses `socat` if present, else a bundled Python fallback (no install needed). Bound to loopback only — the agent is **not** exposed to the LAN. |
| [`bin/install-agent-bridge-launchagent.sh`](../bin/install-agent-bridge-launchagent.sh) | host | Generates a per-user launchd plist from the template and loads it, so the bridge starts automatically at login. `--uninstall` removes it. |
| [`docker/devcontainer/com.secobserve.devcontainer-agent-bridge.plist`](../docker/devcontainer/com.secobserve.devcontainer-agent-bridge.plist) | host | launchd **template** with `__SCRIPT_PATH__` / `__LOG_DIR__` placeholders. Not loaded directly — the install script fills it in. |
| [`docker/devcontainer/ssh-agent-relay.sh`](../docker/devcontainer/ssh-agent-relay.sh) | container | Bridges a unix socket `/tmp/ssh-agent-relay.sock` → `TCP host.containers.internal:17654`. Started by the `postStartCommand`. |
| [`devcontainer.json`](devcontainer.json) | both | Sets `SSH_AUTH_SOCK` (so git/ssh use the relay socket) and `HOST_AGENT_PORT`, and runs the relay on container start. |
| [`Dockerfile`](../docker/devcontainer/Dockerfile) | image | Installs `socat` (the relay), `procps` (the `postStartCommand`'s `pgrep` check), `openssh-client`, and pre-seeds GitHub/GitLab/Bitbucket host keys into `/etc/ssh/ssh_known_hosts`. |

**Port `17654`** is the single coupling point and must match in all four places:
`devcontainer.json` (`HOST_AGENT_PORT`), the bridge default (`DEVCONTAINER_AGENT_PORT`),
the plist, and the relay default (`HOST_AGENT_PORT`).

Nothing here is hard-coded to a specific machine: host paths derive from `$HOME`
and the script location, the agent socket and port are environment-overridable,
and the launchd `PATH` covers both Apple Silicon (`/opt/homebrew`) and Intel
(`/usr/local`) Homebrew.

---

## Setup (macOS + Podman)

1. **Start the host bridge** (keeps running while you use the container):

   ```sh
   sh bin/devcontainer-host-agent-bridge.sh
   ```

   To run it automatically at every login instead:

   ```sh
   sh bin/install-agent-bridge-launchagent.sh
   # later: sh bin/install-agent-bridge-launchagent.sh --uninstall
   ```

2. **Reopen in container** in VS Code. The `postStartCommand` starts the relay
   and `SSH_AUTH_SOCK` is pre-set.

3. **Verify** from a container terminal:

   ```sh
   ssh-add -l                         # lists host agent keys
   ssh -T git@github.com              # "Hi <user>! You've successfully authenticated"
   ```

### Using a different agent

The bridge defaults to Secretive's socket. Point it at the standard agent (or
any other) with `AGENT_SOCK`:

```sh
AGENT_SOCK="$SSH_AUTH_SOCK" sh bin/devcontainer-host-agent-bridge.sh
```

To change the port, override `DEVCONTAINER_AGENT_PORT` on the host **and**
`HOST_AGENT_PORT` in `devcontainer.json` to match.

---

## Troubleshooting

**`git@github.com: Permission denied (publickey)`** or **`Could not open a
connection to your authentication agent`** inside the container:

1. **Is the host bridge running?**
   ```sh
   lsof -nP -iTCP:17654 -sTCP:LISTEN          # should show socat or Python
   ```
   If not, start it (step 1 above). The bridge only listens on the loopback
   interface; `host.containers.internal` reaches it through the Podman gateway.

2. **Is the relay running in the container?**
   ```sh
   ls -l /tmp/ssh-agent-relay.sock            # socket must exist
   pgrep -af 'socat.*ssh-agent-relay.sock'    # relay process
   cat /tmp/ssh-agent-relay.log               # relay errors
   ```
   Re-run the relay manually if needed:
   ```sh
   SSH_AUTH_SOCK=/tmp/ssh-agent-relay.sock HOST_AGENT_PORT=17654 \
     /usr/local/bin/ssh-agent-relay.sh &
   ```

3. **Container started without lifecycle hooks.** Bringing the stack up with
   plain `podman compose up` does **not** run `postCreateCommand` /
   `postStartCommand` — only the Dev Containers CLI / VS Code "Reopen in
   Container" does. If the relay never started, reopen via VS Code.

> Note: the relay script `exec`s into `socat`, so once running its process
> command line is `socat …`, not `ssh-agent-relay.sh`. The `postStartCommand`
> idempotency check therefore matches the live process by its **socket path**
> (`pgrep -f 'socat.*ssh-agent-relay.sock'`), not by the script name.
