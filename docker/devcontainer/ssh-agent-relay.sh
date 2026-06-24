#!/usr/bin/env bash
# Bridges the in-container SSH agent socket to the host SSH agent.
#
# Why: on macOS + podman the container runs inside a Linux VM. The host agent
# socket (e.g. Secretive's, or any agent referenced via IdentityAgent) lives on
# the macOS side and cannot be bind-mounted/connected through virtiofs
# ("Operation not supported"). Instead the host exposes the agent on a loopback
# TCP port (see bin/devcontainer-host-agent-bridge.sh) and this script forwards
# a local unix socket to that port via the podman host gateway.
#
# Started in the background by the devcontainer postStartCommand. SSH_AUTH_SOCK
# (set in devcontainer.json) tells ssh/git to use the socket created here.
set -eu

SOCK="${SSH_AUTH_SOCK:-/tmp/ssh-agent-relay.sock}"
PORT="${HOST_AGENT_PORT:-17654}"
HOST="${HOST_AGENT_HOST:-host.containers.internal}"

rm -f "$SOCK"
exec socat "UNIX-LISTEN:${SOCK},fork,reuseaddr,mode=600" "TCP:${HOST}:${PORT}"
