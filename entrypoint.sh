#!/bin/sh
set -eu

key_src="${SSH_KEY_PATH:-/run/secrets/upm_ssh_key}"
if [ -f "$key_src" ]; then
    cp "$key_src" /tmp/upm_ssh_key
    chown upm:upm /tmp/upm_ssh_key
    chmod 600 /tmp/upm_ssh_key
    export SSH_KEY_PATH=/tmp/upm_ssh_key
fi

exec su-exec upm "$@"
