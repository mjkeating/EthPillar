#!/bin/bash

set -e

echo "🔄 Updating and upgrading all system packages..."
sudo apt update -y && sudo apt upgrade -y

echo "🧹 Removing old or conflicting Docker packages..."
for pkg in docker.io docker-doc docker-compose docker-compose-v2 podman-docker containerd runc; do sudo apt-get remove -y $pkg || true; done

echo "🔑 Adding Docker’s official GPG key..."
sudo install -m 0755 -d /etc/apt/keyrings
sudo apt-get install -y ca-certificates curl gnupg
# shellcheck disable=SC1091
source /etc/os-release
curl -fsSL https://download.docker.com/linux/"${ID}"/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg

echo "📚 Adding Docker’s official repository..."
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/${ID} \
  ${VERSION_CODENAME} stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
echo "🔄 Updating and upgrading all system packages again (with Docker repo)..."
sudo apt update -y && sudo apt upgrade -y

echo "🐳 Installing Docker Engine, CLI, containerd, Buildx, and Compose plugin..."
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

# Except when logged in as root, configure ROOTLESS mode
if [ "$(id -u)" -ne 0 ]; then
    echo "🧱 Enabling ROOTLESS Docker Mode..."
    sudo apt-get install -y docker-ce-rootless-extras
    sudo systemctl disable --now docker.service docker.socket
    sudo rm -f /var/run/docker.sock || true
    sudo apt-get install -y uidmap
    dockerd-rootless-setuptool.sh install
    # enable user service (best-effort) and allow running after logout
    sudo loginctl enable-linger "$USER" || true
    systemctl enable docker || true
    systemctl restart docker || true
    export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
    # point docker CLI to rootless socket for this user
    # shellcheck disable=SC2016
    if ! grep -q 'DOCKER_HOST=unix://\$XDG_RUNTIME_DIR/docker.sock' "$HOME/.profile" 2>/dev/null; then
      # shellcheck disable=SC2016
      echo 'export DOCKER_HOST=unix://$XDG_RUNTIME_DIR/docker.sock' >> "$HOME/.profile"
    fi
    # shellcheck disable=SC2016
    if ! grep -q 'DOCKER_HOST=unix://\$XDG_RUNTIME_DIR/docker.sock' "$HOME/.bashrc" 2>/dev/null; then
      # shellcheck disable=SC2016
      echo 'export DOCKER_HOST=unix://$XDG_RUNTIME_DIR/docker.sock' >> "$HOME/.bashrc"
    fi
fi

if [ "$(id -u)" -eq 0 ]; then
  echo "🚦 Enabling and starting Docker service..."
  sudo systemctl enable --now docker
else
  echo "ℹ️ Rootless Docker uses the per-user service. Skipping system docker.service."
fi

echo "🎉 Docker and Docker Compose are fully installed and up to date!"