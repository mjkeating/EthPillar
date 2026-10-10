#!/usr/bin/env bats
#
# ensure_python_deps recreates a venv whose pip launcher still exists but
# can no longer import pip (typical after an Ubuntu release upgrade).

setup() {
  cd "$BATS_TEST_DIRNAME/.."
  # shellcheck disable=SC1091
  source ./functions.sh

  export WORK
  WORK=$(mktemp -d)
  export BASE_DIR="$WORK"
  printf 'requests\n' > "$BASE_DIR/requirements.txt"

  export ETHPILLAR_VENV="$WORK/venv"
  export COMMAND_LOG
  COMMAND_LOG=$(mktemp)

  export MOCK_BIN
  MOCK_BIN=$(mktemp -d)

  cat > "$MOCK_BIN/python3" <<EOF
#!/bin/bash
echo "python3 \$*" >> "$COMMAND_LOG"
if [[ "\$*" == *"-m venv"* ]]; then
  venv_path="\${@: -1}"
  mkdir -p "\$venv_path/bin"
  cat > "\$venv_path/bin/python3" <<'PY'
#!/bin/bash
echo "venv-python \$*" >> "COMMAND_LOG_PATH"
if [[ "\$*" == *"import pip"* ]]; then
  exit 0
fi
if [[ "\$*" == *"-m pip"* ]]; then
  exit 0
fi
exit 1
PY
  sed -i "s|COMMAND_LOG_PATH|$COMMAND_LOG|" "\$venv_path/bin/python3"
  chmod +x "\$venv_path/bin/python3"
  cat > "\$venv_path/bin/pip" <<'PIP'
#!/bin/bash
exit 0
PIP
  chmod +x "\$venv_path/bin/pip"
fi
exit 0
EOF
  chmod +x "$MOCK_BIN/python3"
  export PATH="$MOCK_BIN:$PATH"
}

teardown() {
  rm -rf "$WORK" "$MOCK_BIN"
  rm -f "$COMMAND_LOG"
}

write_broken_venv() {
  mkdir -p "$ETHPILLAR_VENV/bin"
  cat > "$ETHPILLAR_VENV/bin/python3" <<EOF
#!/bin/bash
echo "broken-python \$*" >> "$COMMAND_LOG"
echo "ModuleNotFoundError: No module named 'pip'" >&2
exit 1
EOF
  cat > "$ETHPILLAR_VENV/bin/pip" <<EOF
#!/bin/bash
echo "broken-pip \$*" >> "$COMMAND_LOG"
exit 1
EOF
  chmod +x "$ETHPILLAR_VENV/bin/python3" "$ETHPILLAR_VENV/bin/pip"
  echo leftover > "$ETHPILLAR_VENV/from-old-release"
}

@test "ensure_python_deps recreates a venv that cannot import pip" {
  write_broken_venv

  run ensure_python_deps

  [ "$status" -eq 0 ]
  [[ "$output" == *"Python virtual environment is unusable; recreating it"* ]]
  [[ "$output" == *"Installing missing Python packages: requests"* ]]
  [ ! -f "$ETHPILLAR_VENV/from-old-release" ]
  grep -q "python3 -m venv $ETHPILLAR_VENV" "$COMMAND_LOG"
  grep -q "venv-python -m pip install -r $BASE_DIR/requirements.txt" "$COMMAND_LOG"
  ! grep -q "broken-pip" "$COMMAND_LOG"
}

@test "ensure_python_deps leaves a healthy venv in place" {
  mkdir -p "$ETHPILLAR_VENV/bin"
  cat > "$ETHPILLAR_VENV/bin/python3" <<EOF
#!/bin/bash
echo "healthy-python \$*" >> "$COMMAND_LOG"
exit 0
EOF
  cat > "$ETHPILLAR_VENV/bin/pip" <<'EOF'
#!/bin/bash
exit 0
EOF
  chmod +x "$ETHPILLAR_VENV/bin/python3" "$ETHPILLAR_VENV/bin/pip"
  echo keep > "$ETHPILLAR_VENV/keep"

  run ensure_python_deps

  [ "$status" -eq 0 ]
  [[ "$output" != *"recreating it"* ]]
  [[ "$output" != *"Creating Python virtual environment"* ]]
  [[ "$output" != *"Installing missing Python packages"* ]]
  [ -f "$ETHPILLAR_VENV/keep" ]
  ! grep -q "python3 -m venv" "$COMMAND_LOG"
}
