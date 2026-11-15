#!/usr/bin/env bats

setup() {
  cd "$BATS_TEST_DIRNAME/.."
  export COMMAND_LOG WHIPTAIL_LOG WHIPTAIL_YESNO_FILE
  COMMAND_LOG=$(mktemp)
  WHIPTAIL_LOG=$(mktemp)
  WHIPTAIL_YESNO_FILE=$(mktemp)

  export SSV_DATA_DIR SSV_BACKUP_PARENT SYSTEMD_UNIT_DIR INSTALL_DIR
  SSV_DATA_DIR=$(mktemp -d)
  SSV_BACKUP_PARENT=$(mktemp -d)
  SYSTEMD_UNIT_DIR=$(mktemp -d)
  INSTALL_DIR=$(mktemp -d)

  mkdir -p "${SSV_DATA_DIR}/db"
  echo '{"key":1}' > "${SSV_DATA_DIR}/encrypted_private_key.json"
  echo 'secret' > "${SSV_DATA_DIR}/password"
  echo 'Network: hoodi' > "${SSV_DATA_DIR}/config.yaml"
  echo 'operatorID: 1' > "${SSV_DATA_DIR}/operator.yaml"
  echo 'dbdata' > "${SSV_DATA_DIR}/db/state"

  touch "${SYSTEMD_UNIT_DIR}/ssv-node.service" "${SYSTEMD_UNIT_DIR}/ssv-dkg.service"
  touch "${INSTALL_DIR}/ssvnode" "${INSTALL_DIR}/ssv-dkg"

  source ./uninstall.sh

  retireSystemdUnit() {
    echo "retireSystemdUnit $*" >> "$COMMAND_LOG"
    local unit="$1"
    local unit_dir="${SYSTEMD_UNIT_DIR:-/etc/systemd/system}"
    [[ -f "${unit_dir}/${unit}.service" ]] || return 0
    mv "${unit_dir}/${unit}.service" "${unit_dir}/${unit}.service.bak"
  }
  export -f retireSystemdUnit

  sudo() {
    echo "sudo $*" >> "$COMMAND_LOG"
    case "$1" in
      mkdir|cp|chmod|rm|test)
        "$@"
        return $?
        ;;
      chown)
        # Unprivileged CI cannot chown to root; treat as success.
        return 0
        ;;
      userdel)
        return 0
        ;;
      *)
        return 0
        ;;
    esac
  }
  export -f sudo

  whiptail() {
    echo "whiptail $*" >> "$WHIPTAIL_LOG"
    if [[ "$*" == *"--yesno"* ]]; then
      local ans=0
      if [[ -s "${WHIPTAIL_YESNO_FILE}" ]]; then
        ans=$(head -n1 "${WHIPTAIL_YESNO_FILE}")
        sed -i '1d' "${WHIPTAIL_YESNO_FILE}"
      fi
      return "${ans:-0}"
    fi
    return 0
  }
  export -f whiptail

  > "$COMMAND_LOG"
  > "$WHIPTAIL_LOG"
}

teardown() {
  rm -f "$COMMAND_LOG" "$WHIPTAIL_LOG" "$WHIPTAIL_YESNO_FILE"
  rm -rf "$SSV_DATA_DIR" "$SSV_BACKUP_PARENT" "$SYSTEMD_UNIT_DIR" "$INSTALL_DIR"
}

@test "backupSsvDatadir copies secrets/config and sets 700/600 modes" {
  run backupSsvDatadir 0
  [ "$status" -eq 0 ]
  # bats merges stderr into $output; path is the last line from stdout.
  local dest
  dest=$(printf '%s\n' "$output" | tail -n1)
  [ -d "$dest" ]
  [ -f "$dest/encrypted_private_key.json" ]
  [ -f "$dest/password" ]
  [ -f "$dest/config.yaml" ]
  [ -f "$dest/operator.yaml" ]
  [ ! -e "$dest/db" ]
  [ "$(stat -c '%a' "$dest")" = "700" ]
  [ "$(stat -c '%a' "$dest/password")" = "600" ]
  grep -q 'SSV backup written to:' <<<"$output"
}

@test "backupSsvDatadir optionally includes db/" {
  run backupSsvDatadir 1
  [ "$status" -eq 0 ]
  local dest
  dest=$(printf '%s\n' "$output" | tail -n1)
  [ -d "$dest/db" ]
  [ -f "$dest/db/state" ]
}

@test "uninstallSsv retires units and keeps datadir when wipe declined" {
  # wipe? → No
  printf '1\n' > "$WHIPTAIL_YESNO_FILE"

  run uninstallSsv
  [ "$status" -eq 0 ]

  grep -q 'retireSystemdUnit ssv-dkg' "$COMMAND_LOG"
  grep -q 'retireSystemdUnit ssv-node' "$COMMAND_LOG"
  [ -d "$SSV_DATA_DIR" ]
  [ -f "$SSV_DATA_DIR/encrypted_private_key.json" ]
  [ ! -f "${INSTALL_DIR}/ssvnode" ]
  [ -f "${SYSTEMD_UNIT_DIR}/ssv-node.service.bak" ]
  grep -q 'Wipe SSV datadir' "$WHIPTAIL_LOG"
  ! grep -q 'Back up SSV before wipe' "$WHIPTAIL_LOG"
  grep -q -- '--defaultno' "$WHIPTAIL_LOG"
}

@test "uninstallSsv wipe+backup+db then removes datadir" {
  # wipe Yes, backup Yes, include DB Yes
  printf '0\n0\n0\n' > "$WHIPTAIL_YESNO_FILE"

  run uninstallSsv
  [ "$status" -eq 0 ]

  grep -q 'retireSystemdUnit ssv-node' "$COMMAND_LOG"
  [ ! -d "$SSV_DATA_DIR" ]
  local backups=( "$SSV_BACKUP_PARENT"/ssv-backup-* )
  [ -d "${backups[0]}" ]
  [ -f "${backups[0]}/encrypted_private_key.json" ]
  [ -d "${backups[0]}/db" ]
  grep -q 'SSV backup complete' "$WHIPTAIL_LOG"
}

@test "uninstallSsv aborts wipe when backup fails" {
  printf '0\n0\n0\n' > "$WHIPTAIL_YESNO_FILE"

  backupSsvDatadir() {
    echo "ERROR: forced backup failure" >&2
    return 1
  }
  export -f backupSsvDatadir

  run uninstallSsv
  [ "$status" -eq 0 ]

  grep -q 'retireSystemdUnit ssv-node' "$COMMAND_LOG"
  [ -d "$SSV_DATA_DIR" ]
  [ -f "$SSV_DATA_DIR/encrypted_private_key.json" ]
  grep -q 'refusing to wipe' <<<"$output"
}

@test "uninstallSsv wipe without backup when backup declined" {
  # wipe Yes, backup No
  printf '0\n1\n' > "$WHIPTAIL_YESNO_FILE"

  run uninstallSsv
  [ "$status" -eq 0 ]

  [ ! -d "$SSV_DATA_DIR" ]
  shopt -s nullglob
  local backups=( "$SSV_BACKUP_PARENT"/ssv-backup-* )
  [ "${#backups[@]}" -eq 0 ]
  grep -q 'without backup' <<<"$output"
}

@test "backup prompt does not use --defaultno" {
  # wipe Yes, backup No
  printf '0\n1\n' > "$WHIPTAIL_YESNO_FILE"
  run uninstallSsv
  [ "$status" -eq 0 ]
  grep -q 'Back up SSV before wipe' "$WHIPTAIL_LOG"
  # Ensure the backup yesno invocation itself is not default-No.
  ! grep 'Back up SSV before wipe' "$WHIPTAIL_LOG" | grep -q -- '--defaultno'
}

@test "backupSsvDatadir uses sudo probes on a 700 unreadable datadir" {
  # Simulate /var/lib/ssv at 700 owned by another user: the test user cannot
  # [[ -e ]] files inside, but sudo test/cp (stubbed) still can.
  chmod 700 "$SSV_DATA_DIR"
  # Drop local execute/read for the current user where possible. On some
  # filesystems chown is unavailable; instead hide children behind a mode that
  # blocks the test user while the sudo stub still runs test/cp as itself after
  # briefly relaxing — so we use a nested unreadable layout:
  #   SSV_DATA_DIR is 711 (traversable) but secrets live in a 700 subdir that
  #   the unsudoed shell cannot list. Prefer the real 700-on-datadir case:
  chmod 000 "$SSV_DATA_DIR/encrypted_private_key.json" 2>/dev/null || true
  chmod 000 "$SSV_DATA_DIR/password" 2>/dev/null || true
  chmod 000 "$SSV_DATA_DIR/config.yaml" 2>/dev/null || true
  chmod 000 "$SSV_DATA_DIR/operator.yaml" 2>/dev/null || true
  chmod 700 "$SSV_DATA_DIR/db"
  chmod 000 "$SSV_DATA_DIR/db/state" 2>/dev/null || true

  # Without sudo, the test user must not be able to read the keystore.
  if cat "${SSV_DATA_DIR}/encrypted_private_key.json" >/dev/null 2>&1; then
    skip "filesystem does not enforce mode-000 for owner (cannot simulate)"
  fi

  # sudo stub runs `test`/`cp` as the test user — restore readable mode only
  # inside the stub so probes go through sudo while unsudoed [[ -e ]] fails.
  sudo() {
    echo "sudo $*" >> "$COMMAND_LOG"
    case "$1" in
      test)
        # Temporarily make target readable for the privileged probe.
        local target="${*: -1}"
        if [[ -e "$target" ]]; then
          chmod u+r "$target" 2>/dev/null || true
          if [[ -d "$target" ]]; then
            chmod u+rx "$target" 2>/dev/null || true
          fi
        fi
        "$@"
        local rc=$?
        # Re-hide files after probe (best-effort).
        if [[ -f "$target" ]]; then
          chmod 000 "$target" 2>/dev/null || true
        fi
        return $rc
        ;;
      cp)
        # Ensure sources are readable for the copy, then re-hide.
        local src="" dest=""
        # sudo cp -a src dest
        src="$3"
        dest="$4"
        if [[ -e "$src" ]]; then
          chmod -R u+rX "$src" 2>/dev/null || true
        fi
        "$@"
        local rc=$?
        if [[ -f "${SSV_DATA_DIR}/encrypted_private_key.json" ]]; then
          chmod 000 "${SSV_DATA_DIR}/encrypted_private_key.json" \
            "${SSV_DATA_DIR}/password" \
            "${SSV_DATA_DIR}/config.yaml" \
            "${SSV_DATA_DIR}/operator.yaml" 2>/dev/null || true
        fi
        return $rc
        ;;
      mkdir|chmod|rm)
        "$@"
        return $?
        ;;
      chown)
        return 0
        ;;
      *)
        return 0
        ;;
    esac
  }
  export -f sudo

  # Unsudoed existence check must fail (the bug we are guarding against).
  if [[ -r "${SSV_DATA_DIR}/encrypted_private_key.json" ]]; then
    skip "could not make keystore unreadable to test user"
  fi

  run backupSsvDatadir 1
  [ "$status" -eq 0 ]
  local dest
  dest=$(printf '%s\n' "$output" | tail -n1)
  [ -f "$dest/encrypted_private_key.json" ]
  [ -f "$dest/password" ]
  [ -d "$dest/db" ]
  grep -q 'sudo test -e' "$COMMAND_LOG"
  grep -q 'sudo test -d' "$COMMAND_LOG"
}

@test "backupSsvDatadir fails when keystore cannot be copied" {
  rm -f "${SSV_DATA_DIR}/encrypted_private_key.json"
  run backupSsvDatadir 0
  [ "$status" -ne 0 ]
  grep -q 'missing operator keystore' <<<"$output"
}

@test "uninstallSsv include-db prompt uses sudo test -d" {
  printf '0\n0\n0\n' > "$WHIPTAIL_YESNO_FILE"
  run uninstallSsv
  [ "$status" -eq 0 ]
  grep -q 'Include SSV DB in backup' "$WHIPTAIL_LOG"
  grep -q "sudo test -d ${SSV_DATA_DIR}/db" "$COMMAND_LOG" || \
    grep -qF "test -d ${SSV_DATA_DIR}/db" "$COMMAND_LOG"
}
