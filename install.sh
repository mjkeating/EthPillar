#!/bin/bash

# Author: mjkeating (maintained fork) | Original by coincashew.eth
# License: GNU GPL
# Source: https://github.com/mjkeating/EthPillar
#
# Made for home and solo stakers 🏠🥩

# 🫶 Make improvements and suggestions on GitHub:
#    * https://github.com/mjkeating/EthPillar
# 🙌 Ask questions on Discord:
#    * https://discord.gg/g63MpHvt

set -u

# enable command completion
set -o history -o histexpand

abort() {
  printf "%s\n" "$1"
  exit 1
}

getc() {
  local save_state
  save_state=$(/bin/stty -g)
  /bin/stty raw -echo
  IFS= read -r -n 1 -d '' "$@"
  /bin/stty "$save_state"
}

exit_on_error() {
    exit_code=$1
    last_command="${@:2}"
    if [ $exit_code -ne 0 ]; then
        >&2 echo "\"${last_command}\" command failed with exit code ${exit_code}."
        exit $exit_code
    fi
}

wait_for_user() {
  local c
  echo
  echo "Press RETURN to continue or any other key to abort"
  getc c
  # we test for \r and \n because some stuff does \r instead
  if ! [[ "$c" == $'\r' || "$c" == $'\n' ]]; then
    exit 1
  fi
}

shell_join() {
  local arg
  printf "%s" "$1"
  shift
  for arg in "$@"; do
    printf " "
    printf "%s" "${arg// /\ }"
  done
}

# string formatters
if [[ -t 1 ]]; then
  tty_escape() { printf "\033[%sm" "$1"; }
else
  tty_escape() { :; }
fi
tty_mkbold() { tty_escape "1;$1"; }
tty_underline="$(tty_escape "4;39")"
tty_blue="$(tty_mkbold 34)"
tty_red="$(tty_mkbold 31)"
tty_bold="$(tty_mkbold 39)"
tty_reset="$(tty_escape 0)"

ohai() {
  printf "${tty_blue}==>${tty_bold} %s${tty_reset}\n" "$(shell_join "$@")"
}

# Resolve repo location: use this checkout when install.sh is run from a clone,
# otherwise default to ~/git/ethpillar (curl | bash one-liner).
SOURCE="${BASH_SOURCE[0]:-${0:-}}"
while [ -h "$SOURCE" ]; do
  DIR="$( cd -P "$( dirname "$SOURCE" )" && pwd )"
  SOURCE="$(readlink "$SOURCE")"
  [[ $SOURCE != /* ]] && SOURCE="$DIR/$SOURCE"
done
SCRIPT_DIR="$( cd -P "$( dirname "$SOURCE" )" && pwd )"
# Piped installs (curl | bash) expose $0 as "bash" or "-"; treat as outside the repo.
if [[ "$SOURCE" == "bash" || "$SOURCE" == "-" || "$SOURCE" == */bash ]]; then
  SCRIPT_DIR="$(pwd)"
fi

if [[ -f "$SCRIPT_DIR/ethpillar.sh" ]]; then
  # running from a cloned repo, use that as the source location
  REPO="$SCRIPT_DIR"
else
  # running from a curl one-liner, use the default location
  REPO="$HOME/git/ethpillar"
fi

requirements_check() {
  # Check CPU architecture
  if ! [[ $(lscpu | grep -oE 'x86') || $(lscpu | grep -oE 'aarch64') ]]; then
    echo "This machine's CPU architecture is not yet supported."
    echo "Recommend using Intel-AMD x86 or arm64 systems for best experience."
    exit 1
  fi

  # Check operating system
  if ! [[ "$(uname)" == "Linux" ]]; then
    echo "This operating system is not yet supported."
    echo "Recommend installing Ubuntu Desktop 24.04+ LTS or Ubuntu Server 24.04+ LTS for best experience."
    exit 1
  fi
}

linux_install_pre() {
    sudo apt-get update
    sudo apt-get install --no-install-recommends --no-install-suggests -y curl git ccze bc tmux jq nano btop whiptail ufw python3-venv python3-pip
    exit_on_error $?
}

linux_install_python_deps() {
    ohai "Installing Python runtime dependencies"
    export BASE_DIR="${REPO}"
    cd "${REPO}" || exit_on_error $?
    # shellcheck source=functions.sh
    source "${REPO}/functions.sh"
    exit_on_error $?
}

linux_install_motd() {
    # Keep login MOTD in sync with the installed repo (replace legacy ~/git/ethpillar paths)
    local motd_line="cat \"${REPO}/motd\""
    if [[ -f ~/.profile ]] && grep -q "cat.*motd" ~/.profile 2>/dev/null; then
        grep -v 'cat.*motd' ~/.profile > ~/.profile.tmp && mv ~/.profile.tmp ~/.profile
    fi
    if ! grep -q "cat.*motd" ~/.profile 2>/dev/null; then
        echo "$motd_line" >> ~/.profile
    fi
}

linux_install_installer() {
    if [[ -f "$SCRIPT_DIR/ethpillar.sh" ]]; then
        # install from manually cloned repo (user defined location)
        ohai "Installing ethpillar from ${REPO}"
    else
        # Curl | bash one-liner; REPO is ~/git/ethpillar default location
        ohai "Cloning EthPillar into ${REPO}"
        INSTALL_GIT_URL="${ETHPILLAR_INSTALL_GIT_URL:-https://github.com/mjkeating/EthPillar.git}"
        if [[ -n "${ETHPILLAR_INSTALL_COPY_FROM:-}" && -f "${ETHPILLAR_INSTALL_COPY_FROM}/ethpillar.sh" ]]; then
            # Install smoke test only - copy files instead of cloning
            # (see tests/integration/install_smoke/)
            rm -rf "${REPO}"
            mkdir -p "${REPO}"
            cp -a "${ETHPILLAR_INSTALL_COPY_FROM}/." "${REPO}/"
        elif [[ -d "${REPO}/.git" ]]; then
            # Re-run: default path already cloned - fetch latest code
            (cd "${REPO}" ; git fetch origin main ; git checkout main ; git pull)
        else
            # First-time curl | bash install - clone repo
            rm -rf "${REPO}"
            mkdir -p "$(dirname "${REPO}")"
            git clone "${INSTALL_GIT_URL}" "${REPO}" 2> /dev/null || \
              (cd "${REPO}" ; git fetch origin main ; git checkout main ; git pull)
        fi
    fi
    chmod +x "${REPO}"/*.sh
    ohai "Installing ethpillar"
    if [ -f /usr/local/bin/ethpillar ]; then
      sudo rm /usr/local/bin/ethpillar
    fi
    sudo ln -s "${REPO}/ethpillar.sh" /usr/local/bin/ethpillar
    exit_on_error $?
}

# Check OS and CPU requirements
requirements_check

# Do install.
OS="$(uname)"
if [[ "$OS" == "Linux" ]]; then
    echo """
███████╗████████╗██╗  ██╗██████╗ ██╗██╗     ██╗      █████╗ ██████╗ 
██╔════╝╚══██╔══╝██║  ██║██╔══██╗██║██║     ██║     ██╔══██╗██╔══██╗
█████╗     ██║   ███████║██████╔╝██║██║     ██║     ███████║██████╔╝
██╔══╝     ██║   ██╔══██║██╔═══╝ ██║██║     ██║     ██╔══██║██╔══██╗
███████╗   ██║   ██║  ██║██║     ██║███████╗███████╗██║  ██║██║  ██║
╚══════╝   ╚═╝   ╚═╝  ╚═╝╚═╝     ╚═╝╚══════╝╚══════╝╚═╝  ╚═╝╚═╝  ╚═╝
                                                          
  - This is my node. There are many like it, but this one is mine.
  - coincashew
    """
    ohai "This script will install a node management tool called 'ethpillar'"

    if [[ -t 0 && -z "${ETHPILLAR_INSTALL_NONINTERACTIVE:-}" ]]; then
      wait_for_user
    fi
    linux_install_pre
    linux_install_installer
    linux_install_python_deps
    ohai "Allowing user to view journalctl logs"
    ensure_journal_access || ohai "Journal access granted; open a new terminal session before viewing logs without sudo"
    linux_install_motd

    echo ""
    echo ""
    echo "######################################################################"
    echo "##                                                                  ##"
    echo "##           INSTALL COMPLETE - To run, type \"ethpillar\"            ##"
    echo "##                                                                  ##"
    echo "######################################################################"
    echo ""
    echo ""
fi
