#!/usr/bin/env bash

install_docker_engine() {
  if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then return; fi
  confirm_mutation
  sudo_run apt-get update
  sudo_run apt-get install -y ca-certificates curl
  sudo_run install -m 0755 -d /etc/apt/keyrings
  run curl -fsSL "https://download.docker.com/linux/$ROBOT_AGENT_DISTRO/gpg" -o /tmp/tangying-docker.asc
  sudo_run install -m 0644 /tmp/tangying-docker.asc /etc/apt/keyrings/docker.asc
  codename=$ROBOT_AGENT_OS_VERSION
  if [ -r /etc/os-release ]; then
    # shellcheck disable=SC1091
    . /etc/os-release
    codename=${VERSION_CODENAME:-$codename}
  fi
  repository="deb [arch=$ROBOT_AGENT_ARCH signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/$ROBOT_AGENT_DISTRO $codename stable"
  if [ "$ROBOT_AGENT_DRY_RUN" = "1" ]; then
    echo "DRY-RUN write /etc/apt/sources.list.d/docker.list: $repository"
  else
    echo "$repository" | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
  fi
  sudo_run apt-get update
  sudo_run apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
}

install_role() {
  info "preparing cloud control plane"
  install_docker_engine
  configuration="$(config_dir)/cloud.env"
  install_config_example "$ROBOT_AGENT_ROOT/deploy/config/cloud.env.example" "$configuration"
  run docker compose --env-file "$configuration" -f "$ROBOT_AGENT_ROOT/deploy/docker-compose.yml" up -d --build
  if [ "$ROBOT_AGENT_DRY_RUN" = "1" ]; then
    echo "DRY-RUN wait for http://127.0.0.1:8080/healthz"
  else
    attempts=0
    until curl -fsS http://127.0.0.1:8080/healthz >/dev/null; do
      attempts=$((attempts + 1))
      [ "$attempts" -lt 60 ] || die "cloud health check timed out; run docker compose logs cloud"
      sleep 1
    done
  fi
  write_receipt
  info "cloud ready at http://127.0.0.1:8080"
}

