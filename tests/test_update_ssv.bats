#!/usr/bin/env bats
#
# update_ssv.sh skips rebuild/download when binaries already match LATEST.
#

setup() {
    cd "$BATS_TEST_DIRNAME/.."

    export ETHPILLAR_VENV="/tmp/ethpillar_bats_update_ssv_venv"
    rm -rf "$ETHPILLAR_VENV"

    export MOCK_BIN_DIR
    MOCK_BIN_DIR=$(mktemp -d)
    export COMMAND_LOG
    COMMAND_LOG=$(mktemp)
    export TEST_SYSTEMD_DIR
    TEST_SYSTEMD_DIR=$(mktemp -d)
    export SSV_NODE_SERVICE_FILE="$TEST_SYSTEMD_DIR/ssv-node.service"
    export SSV_DKG_SERVICE_FILE="$TEST_SYSTEMD_DIR/ssv-dkg.service"
    export MOCK_SSV_LATEST=v2.5.1
    export MOCK_SSV_DKG_LATEST=v3.1.1

    # Host + venv python: allow ensure_python_deps, serve release_info, refuse builds.
    cat > "$MOCK_BIN_DIR/venv_python3.template" <<EOF
#!/bin/bash
echo "python3 \$*" >> "$COMMAND_LOG"
if [[ "\$*" == *release_info* ]]; then
  client=""
  prev=""
  for arg in "\$@"; do
    if [[ "\$prev" == "release_info" ]]; then
      client="\$arg"
      break
    fi
    prev="\$arg"
  done
  case "\${client,,}" in
    ssv|ssv-node|ssvnode)
      echo "{\"version\":\"\${MOCK_SSV_LATEST:-v2.5.1}\",\"commit\":\"\",\"filenames\":[\"ssv.tgz\"],\"download_urls\":[\"http://example/ssv.tgz\"]}"
      ;;
    ssv-dkg|ssvdkg)
      echo "{\"version\":\"\${MOCK_SSV_DKG_LATEST:-v3.1.1}\",\"commit\":\"\",\"filenames\":[\"ssv-dkg.zip\"],\"download_urls\":[\"http://example/ssv-dkg.zip\"]}"
      ;;
  esac
  exit 0
fi
if [[ "\$*" == *"-c"* && "\$*" == *import* ]]; then
  exit 0
fi
if [[ "\$*" == *"build_ssvnode"* || "\$*" == *"extract_and_install"* ]]; then
  echo "UNEXPECTED build/install: \$*" >> "$COMMAND_LOG"
  exit 1
fi
exit 0
EOF

    cat > "$MOCK_BIN_DIR/python3" <<EOF
#!/bin/bash
echo "python3 \$*" >> "$COMMAND_LOG"
if [[ "\$*" == *"-m venv"* ]]; then
  venv_path="\${@: -1}"
  command -p mkdir -p "\$venv_path/bin"
  {
    echo '#!/bin/bash'
    echo "echo \"pip \\\$*\" >> \"$COMMAND_LOG\""
    echo 'exit 0'
  } > "\$venv_path/bin/pip"
  command -p chmod +x "\$venv_path/bin/pip"
  command -p cp "$MOCK_BIN_DIR/venv_python3.template" "\$venv_path/bin/python3"
  command -p chmod +x "\$venv_path/bin/python3"
  exit 0
fi
if [[ "\$*" == *release_info* ]]; then
  exec "$MOCK_BIN_DIR/venv_python3.template" "\$@"
fi
if [[ "\$*" == *"-c"* && "\$*" == *import* ]]; then
  exit 0
fi
if [[ "\$*" == *"build_ssvnode"* || "\$*" == *"extract_and_install"* ]]; then
  echo "UNEXPECTED build/install: \$*" >> "$COMMAND_LOG"
  exit 1
fi
exit 0
EOF
    chmod +x "$MOCK_BIN_DIR/python3" "$MOCK_BIN_DIR/venv_python3.template"

    cat > "$MOCK_BIN_DIR/ssvnode" <<EOF
#!/bin/bash
echo "v2.5.1"
exit 0
EOF
    chmod +x "$MOCK_BIN_DIR/ssvnode"

    cat > "$MOCK_BIN_DIR/ssv-dkg" <<EOF
#!/bin/bash
echo "v3.1.1"
exit 0
EOF
    chmod +x "$MOCK_BIN_DIR/ssv-dkg"

    cat > "$SSV_NODE_SERVICE_FILE" <<EOF
[Service]
ExecStart=$MOCK_BIN_DIR/ssvnode start-node
EOF
    cat > "$SSV_DKG_SERVICE_FILE" <<EOF
[Service]
ExecStart=$MOCK_BIN_DIR/ssv-dkg start-operator
EOF

    cat > "$MOCK_BIN_DIR/sudo" <<EOF
#!/bin/bash
echo "sudo \$*" >> "$COMMAND_LOG"
export PATH="$MOCK_BIN_DIR:\$PATH"
"\$@"
EOF
    chmod +x "$MOCK_BIN_DIR/sudo"

    cat > "$MOCK_BIN_DIR/systemctl" <<EOF
#!/bin/bash
echo "systemctl \$*" >> "$COMMAND_LOG"
exit 0
EOF
    chmod +x "$MOCK_BIN_DIR/systemctl"

    export PATH="$MOCK_BIN_DIR:$PATH"
}

teardown() {
    rm -rf "$MOCK_BIN_DIR" "$TEST_SYSTEMD_DIR" \
        "${ETHPILLAR_VENV:-/tmp/ethpillar_bats_update_ssv_venv}"
    rm -f "$COMMAND_LOG"
}

@test "update_ssv --auto skips node build and DKG download when already latest" {
    run bash ./update_ssv.sh --auto
    echo "$output"
    [ "$status" -eq 0 ]
    [[ "$output" == *"ssv-node already at 2.5.1 — skipping build"* ]]
    [[ "$output" == *"ssv-dkg already at 3.1.1 — skipping download"* ]]
    ! grep -q "UNEXPECTED" "$COMMAND_LOG"
    ! grep -q "build_ssvnode" "$COMMAND_LOG"
    ! grep -q "extract_and_install" "$COMMAND_LOG"
}
