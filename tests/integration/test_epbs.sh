#!/bin/bash
# EthPillar ePBS migration integration test.
# Prysm or Lodestar: VC + MEV-Boost, empty-wallet VC start.
# Erigon-Caplin v3.7.1: integrated execution.service, no separate VC.
# Runs inside the Docker container after a MEV node is deployed.

set -e

cd /ethpillar
source "${ETHPILLAR_ENV_FILE:-/ethpillar/env}"

py="${ETHPILLAR_PYTHON:-python3}"
VC_UNIT="/etc/systemd/system/validator.service"
BN_UNIT="/etc/systemd/system/consensus.service"
EXEC_UNIT="/etc/systemd/system/execution.service"
MEV_UNIT="/etc/systemd/system/mevboost.service"
SIDECAR="127.0.0.1:18550"
SETTINGS="/var/lib/prysm_validator/proposer-settings.json"
CAPLIN_BUILDERS="/var/lib/erigon/caplin-builders.json"

# Run manage.epbs with the integration venv. Extra args are forwarded.
epbs_cli() {
    PYTHONPATH=/ethpillar "$py" -m manage.epbs "$@"
}

# First word of Description= (Prysm, Lodestar, …).
detect_vc_client() {
    grep -m1 '^Description=' "$VC_UNIT" 2>/dev/null | awk -F'=' '{print $2}' | awk '{print $1}'
}

# Delegate to run_inside_docker.py verify-service-health (optional extra flags).
# Args: $1 service name; remaining args passed through (e.g. --force-validator).
check_service_health() {
    local service="$1"
    shift
    echo "  [ePBS] Health check for $service..."
    bash /ethpillar/tests/integration/run_test.sh verify-service-health --service "$service" "$@"
}

# Fail unless validator.service is a supported ePBS VC.
assert_supported_vc() {
    if [[ ! -f "$VC_UNIT" ]]; then
        echo "❌ validator.service not found"
        exit 1
    fi
    case "$VC_CLIENT" in
        Prysm|Lodestar) ;;
        *)
            echo "❌ ePBS integration test requires a Prysm or Lodestar validator client"
            grep Description= "$VC_UNIT" || true
            exit 1
            ;;
    esac
}

# Fail unless mevboost.service exists.
assert_mev_installed() {
    if [[ ! -f "$MEV_UNIT" ]]; then
        echo "❌ mevboost.service not found (install with --mev)"
        exit 1
    fi
}

# Fail unless *unit* contains *needle* (fixed string).
assert_unit_has() {
    local unit="$1"
    local needle="$2"
    if ! grep -qF -- "$needle" "$unit"; then
        echo "❌ $unit is missing: $needle"
        cat "$unit"
        exit 1
    fi
}

# Fail if *unit* still contains *needle* (fixed string).
assert_unit_lacks() {
    local unit="$1"
    local needle="$2"
    if grep -qF -- "$needle" "$unit"; then
        echo "❌ $unit still contains: $needle"
        cat "$unit"
        exit 1
    fi
}

# Fail unless proposer-settings.json is schema v2 with a nonempty builders list.
assert_proposer_settings() {
    if [[ ! -f "$SETTINGS" ]]; then
        echo "❌ proposer-settings.json not written at $SETTINGS"
        exit 1
    fi
    PYTHONPATH=/ethpillar "$py" - <<'PY'
import json
from pathlib import Path
path = Path("/var/lib/prysm_validator/proposer-settings.json")
data = json.loads(path.read_text(encoding="utf-8"))
builder = (data.get("default_config") or {}).get("builder") or {}
entries = builder.get("builders") or []
urls = [e.get("url") for e in entries if isinstance(e, dict) and e.get("url")]
assert data.get("version") == 2, data
assert "enabled" not in builder, builder
assert "relays" not in builder, builder
assert urls, "proposer-settings.json has no builder URLs"
assert builder.get("max_execution_payment") == "0", builder
print(f"✅ proposer-settings.json: {len(urls)} builder URL(s), schema v2")
PY
}

# Fail unless the live validator process argv includes this client's ePBS flags.
assert_vc_process_has_epbs_flags() {
    local pid cmdline
    pid=$(sudo systemctl show -p MainPID --value validator 2>/dev/null || echo "0")
    if [[ -z "$pid" || "$pid" == "0" ]]; then
        echo "❌ validator MainPID is 0 — VC is not running"
        sudo journalctl -u validator --no-pager -n 40 || true
        exit 1
    fi
    cmdline=$(tr '\0' ' ' < "/proc/${pid}/cmdline" || true)
    if [[ -z "$cmdline" ]]; then
        echo "❌ could not read /proc/${pid}/cmdline"
        exit 1
    fi
    case "$VC_CLIENT" in
        Prysm)
            if [[ "$cmdline" == *"--enable-builder"* ]]; then
                echo "❌ running VC still has deprecated --enable-builder"
                echo "  cmdline: $cmdline"
                exit 1
            fi
            if [[ "$cmdline" != *"--proposer-settings-file"* ]]; then
                echo "❌ running VC is missing --proposer-settings-file"
                echo "  cmdline: $cmdline"
                exit 1
            fi
            echo "✅ running VC pid=${pid} has --proposer-settings-file and no --enable-builder"
            ;;
        Lodestar)
            if [[ "$cmdline" != *"--builder.urls"* ]]; then
                echo "❌ running VC is missing --builder.urls"
                echo "  cmdline: $cmdline"
                exit 1
            fi
            if [[ "$cmdline" == *"$SIDECAR"* ]]; then
                echo "❌ running VC --builder.urls still points at the MEV-Boost sidecar"
                echo "  cmdline: $cmdline"
                exit 1
            fi
            echo "✅ running VC pid=${pid} has --builder.urls (not sidecar)"
            ;;
    esac
}

# Client-specific unit checks after prepare (BN sidecar still present).
assert_prepare_units() {
    case "$VC_CLIENT" in
        Prysm)
            assert_unit_lacks "$VC_UNIT" "--enable-builder"
            assert_unit_has "$VC_UNIT" "--proposer-settings-file"
            assert_proposer_settings
            ;;
        Lodestar)
            assert_unit_has "$VC_UNIT" "--builder"
            assert_unit_has "$VC_UNIT" "--builder.urls"
            assert_unit_lacks "$VC_UNIT" "$SIDECAR"
            if ! grep -qE -- '--builder.minBid=[0-9]+' "$VC_UNIT"; then
                echo "❌ Lodestar VC --builder.minBid must be integer Gwei (not ETH decimal)"
                cat "$VC_UNIT"
                exit 1
            fi
            ;;
    esac
    assert_unit_has "$BN_UNIT" "$SIDECAR"
}

# Client-specific unit checks after complete (BN sidecar gone, VC relays kept).
assert_complete_units() {
    assert_unit_lacks "$BN_UNIT" "$SIDECAR"
    case "$VC_CLIENT" in
        Prysm)
            assert_unit_lacks "$VC_UNIT" "--enable-builder"
            assert_unit_has "$VC_UNIT" "--proposer-settings-file"
            assert_proposer_settings
            ;;
        Lodestar)
            assert_unit_has "$VC_UNIT" "--builder.urls"
            assert_unit_lacks "$VC_UNIT" "$SIDECAR"
            ;;
    esac
}

# Lodestar exits without keys unless --keymanager is set (warn instead of YargsError).
# Test-only: empty-wallet smoke; not part of operator prepare/complete.
enable_lodestar_empty_wallet() {
    local tmp
    tmp=$(mktemp)
    PYTHONPATH=/ethpillar "$py" - "$VC_UNIT" "$tmp" <<'PY'
import sys
from manage.epbs import upsert_flag
from manage.service_parse import normalize_cli_args, parse_unit, rebuild_service_content

src, dst = sys.argv[1], sys.argv[2]
text = open(src, encoding="utf-8").read()
unit = parse_unit(text)
args = upsert_flag(normalize_cli_args(unit.exec_args), "--keymanager")
open(dst, "w", encoding="utf-8").write(
    rebuild_service_content(text, unit.exec_start_index, unit.exec_start_end_index, args)
)
PY
    sudo cp "$tmp" "$VC_UNIT"
    rm -f "$tmp"
    echo "✅ test-only: added --keymanager so Lodestar can start with an empty wallet"
}

# daemon-reload then restart each listed systemd unit (exits 1 on failure).
reload_and_restart() {
    local svc
    echo "  [ePBS] daemon-reload + restart: $*"
    sudo systemctl daemon-reload
    for svc in "$@"; do
        if ! sudo systemctl restart "$svc"; then
            echo "❌ systemctl restart $svc failed"
            sudo journalctl -u "$svc" --no-pager -n 40 || true
            exit 1
        fi
    done
}

# Integrated Erigon-Caplin (no validator.service): real v3.7.1 prepare path.
run_caplin_epbs() {
    echo "  Client: Erigon-Caplin (integrated)"
    assert_mev_installed
    assert_unit_has "$EXEC_UNIT" "$SIDECAR"
    assert_unit_has "$EXEC_UNIT" "--caplin.discovery.quicport="

    if ! sudo systemctl is-active --quiet mevboost; then
        echo "❌ mevboost is not active before prepare"
        sudo systemctl status mevboost --no-pager -l || true
        exit 1
    fi
    echo "✅ Pre-migration: Erigon-Caplin + active MEV-Boost + sidecar URL"

    echo
    echo "--- prepare (write caplin-builders.json, keep MEV-Boost) ---"
    epbs_cli status
    epbs_cli prepare --apply --json
    if [[ ! -f "$CAPLIN_BUILDERS" ]]; then
        echo "❌ caplin-builders.json was not written (Erigon must be v3.7.1+)"
        epbs_cli status || true
        exit 1
    fi
    PYTHONPATH=/ethpillar "$py" - <<PY
import json
from pathlib import Path
data = json.loads(Path("$CAPLIN_BUILDERS").read_text(encoding="utf-8"))
urls = [e.get("url") for e in data.get("builders") or [] if isinstance(e, dict)]
assert urls, data
assert all("127.0.0.1:18550" not in u for u in urls), urls
assert data["builders"][0].get("max_execution_payment") == "0", data
print(f"✅ caplin-builders.json: {len(urls)} builder URL(s)")
PY
    assert_unit_has "$EXEC_UNIT" "$SIDECAR"
    if ! sudo systemctl is-active --quiet mevboost; then
        echo "❌ mevboost stopped during prepare (must stay up until complete)"
        exit 1
    fi
    echo "✅ prepare: builder list written; sidecar and MEV-Boost still present"

    reload_and_restart execution
    check_service_health execution
    pid=$(sudo systemctl show -p MainPID --value execution 2>/dev/null || echo "0")
    cmdline=$(tr '\0' ' ' < "/proc/${pid}/cmdline" || true)
    if [[ "$cmdline" != *"--caplin.mev-relay-url"* ]]; then
        echo "❌ running Erigon lost --caplin.mev-relay-url during prepare"
        echo "  cmdline: $cmdline"
        exit 1
    fi
    if [[ "$cmdline" != *"--caplin.discovery.quicport="* ]]; then
        echo "❌ running Erigon is missing --caplin.discovery.quicport"
        echo "  cmdline: $cmdline"
        exit 1
    fi
    echo "✅ execution pid=${pid} still has the sidecar and QUIC port"

    echo
    echo "--- complete (disable MEV-Boost, strip Caplin sidecar) ---"
    epbs_cli complete --apply --json
    assert_unit_lacks "$EXEC_UNIT" "$SIDECAR"
    assert_unit_lacks "$EXEC_UNIT" "--caplin.mev-relay-url"
    if [[ ! -f "$CAPLIN_BUILDERS" ]]; then
        echo "❌ complete removed caplin-builders.json"
        exit 1
    fi
    if sudo systemctl is-active --quiet mevboost; then
        echo "❌ mevboost is still active after complete"
        exit 1
    fi
    if sudo systemctl is-enabled --quiet mevboost; then
        echo "❌ mevboost is still enabled after complete"
        exit 1
    fi
    echo "✅ complete: sidecar gone; MEV-Boost stopped; builder list kept"

    reload_and_restart execution
    check_service_health execution \
        --ignore-journal-pattern "cannot connect to builder client"
    pid=$(sudo systemctl show -p MainPID --value execution 2>/dev/null || echo "0")
    cmdline=$(tr '\0' ' ' < "/proc/${pid}/cmdline" || true)
    if [[ "$cmdline" == *"--caplin.mev-relay-url"* || "$cmdline" == *"$SIDECAR"* ]]; then
        echo "❌ running Erigon still has the MEV-Boost sidecar after complete"
        echo "  cmdline: $cmdline"
        exit 1
    fi
    echo "✅ execution pid=${pid} started without the sidecar"
    epbs_cli status
    echo "========================================="
    echo " ePBS migration: Erigon-Caplin prepare/complete"
    echo "========================================="
}

echo "========================================="
echo " Starting ePBS Migration Integration Test"
echo "========================================="

if [[ -f "$EXEC_UNIT" ]] && grep -q 'Erigon-Caplin' "$EXEC_UNIT" && [[ ! -f "$VC_UNIT" ]]; then
    run_caplin_epbs
    exit 0
fi

if [[ ! -f "$VC_UNIT" ]]; then
    echo "❌ validator.service not found"
    exit 1
fi
VC_CLIENT=$(detect_vc_client)
echo "  VC client: ${VC_CLIENT:-unknown}"

assert_supported_vc
assert_mev_installed
assert_unit_has "$BN_UNIT" "$SIDECAR"

if ! sudo systemctl is-active --quiet mevboost; then
    echo "❌ mevboost is not active before prepare"
    sudo systemctl status mevboost --no-pager -l || true
    exit 1
fi
echo "✅ Pre-migration: ${VC_CLIENT} VC + active MEV-Boost + BN sidecar URL"

echo
echo "--- prepare (copy relays onto VC, keep MEV-Boost) ---"
epbs_cli status
epbs_cli prepare --apply --json
assert_prepare_units
if ! sudo systemctl is-active --quiet mevboost; then
    echo "❌ mevboost stopped during prepare (must stay up until complete)"
    sudo systemctl status mevboost --no-pager -l || true
    exit 1
fi
echo "✅ prepare: VC ePBS flags written; BN sidecar and MEV-Boost still present"

if [[ "$VC_CLIENT" == "Lodestar" ]]; then
    enable_lodestar_empty_wallet
fi

reload_and_restart validator
check_service_health validator --force-validator
assert_vc_process_has_epbs_flags
echo "✅ validator started after prepare (empty wallet, ePBS flags accepted)"

echo
echo "--- complete (disable MEV-Boost, strip BN sidecar) ---"
epbs_cli complete --apply --json
assert_complete_units
if sudo systemctl is-active --quiet mevboost; then
    echo "❌ mevboost is still active after complete"
    sudo systemctl status mevboost --no-pager -l || true
    exit 1
fi
if sudo systemctl is-enabled --quiet mevboost; then
    echo "❌ mevboost is still enabled after complete"
    sudo systemctl status mevboost --no-pager -l || true
    exit 1
fi
echo "✅ complete: BN sidecar gone; MEV-Boost stopped/disabled; VC flags kept"

reload_and_restart consensus validator
# Pre-Gloas Sepolia may log builder-client errors once the sidecar is gone.
# Unknown flags / EXEC failures are still fatal.
check_service_health consensus \
    --ignore-journal-pattern "cannot connect to builder client"
check_service_health validator --force-validator \
    --ignore-journal-pattern "cannot connect to builder client"
assert_vc_process_has_epbs_flags
epbs_cli status

echo "========================================="
echo " ePBS migration: ${VC_CLIENT} VC started with flags"
echo "========================================="
