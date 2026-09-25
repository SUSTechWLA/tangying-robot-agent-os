#!/bin/sh
set -eu

role="${1:-}"
if [ "$#" -gt 0 ]; then
    shift
fi
case "$role" in
    server)
        exec /usr/local/bin/fleet-control-plane "$@"
        ;;
    edge)
        /usr/local/bin/local-agent --config /etc/tangying/edge.env --check-config
        exec /usr/local/bin/local-agent --config /etc/tangying/edge.env --data-dir /var/lib/tangying-agent/edge "$@"
        ;;
    worker)
        /usr/local/bin/edge-worker --check-config
        exec /usr/local/bin/edge-worker "$@"
        ;;
    *)
        echo "usage: tangying-agent server|edge|worker [arguments...]" >&2
        exit 64
        ;;
esac
