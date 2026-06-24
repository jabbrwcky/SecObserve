#!/bin/sh
# Expose the host SSH agent on a loopback TCP port so the SecObserve
# devcontainer (which runs inside the podman VM) can reach it.
#
# Run this on the macOS host while using the devcontainer. It bridges a TCP
# listener on 127.0.0.1:<port> to the host agent unix socket. The container
# reaches it via the podman host gateway (host.containers.internal).
#
# By default it targets the Secretive agent socket. Override with AGENT_SOCK
# (e.g. AGENT_SOCK="$SSH_AUTH_SOCK" for the standard agent) and the port with
# DEVCONTAINER_AGENT_PORT (must match HOST_AGENT_PORT in devcontainer.json).
#
# Uses socat when available, otherwise falls back to bundled Python (no install
# needed on macOS). Bound to loopback only, so the agent is NOT exposed to the
# LAN. Stop with Ctrl-C.
set -eu

PORT="${DEVCONTAINER_AGENT_PORT:-17654}"
AGENT_SOCK="${AGENT_SOCK:-$HOME/Library/Containers/com.maxgoedjen.Secretive.SecretAgent/Data/socket.ssh}"

if [ ! -S "$AGENT_SOCK" ]; then
    echo "error: agent socket not found: $AGENT_SOCK" >&2
    echo "       set AGENT_SOCK to your agent's socket path." >&2
    exit 1
fi

echo "Bridging 127.0.0.1:${PORT} -> ${AGENT_SOCK}"

if command -v socat >/dev/null 2>&1; then
    exec socat "TCP-LISTEN:${PORT},bind=127.0.0.1,reuseaddr,fork" "UNIX-CONNECT:${AGENT_SOCK}"
fi

exec python3 - "$AGENT_SOCK" "$PORT" <<'PY'
import socket, sys, threading
agent_sock, port = sys.argv[1], int(sys.argv[2])
srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
srv.bind(("127.0.0.1", port))
srv.listen(16)

def pipe(a, b):
    try:
        while True:
            data = a.recv(4096)
            if not data:
                break
            b.sendall(data)
    except OSError:
        pass
    finally:
        for s in (a, b):
            try:
                s.close()
            except OSError:
                pass

def handle(client):
    upstream = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    upstream.connect(agent_sock)
    threading.Thread(target=pipe, args=(client, upstream), daemon=True).start()
    threading.Thread(target=pipe, args=(upstream, client), daemon=True).start()

while True:
    conn, _ = srv.accept()
    handle(conn)
PY
