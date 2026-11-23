command: ["/bin/sh", "-ec"]
args:
  - |
    cookie=/var/lib/rabbitmq/.erlang.cookie
    if [ -f "$cookie" ]; then
      id
      stat -c 'cookie uid=%u gid=%g mode=%a' "$cookie"
      chmod 600 "$cookie"
    fi
    exec /usr/local/bin/docker-entrypoint.sh rabbitmq-server