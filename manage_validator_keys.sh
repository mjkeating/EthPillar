#!/bin/bash

# Author: coincashew.eth | coincashew.com
# License: GNU GPL
# Source: https://github.com/coincashew/ethpillar
#
# Made for home and solo stakers 🏠🥩

# Dir to install ethstaker-deposit-cli
STAKING_DEPOSIT_CLI_DIR=$HOME
# Path to deposit cli tool
DEPOSIT_CLI_PATH=$STAKING_DEPOSIT_CLI_DIR/ethstaker_deposit-cli
# Initialize variable
OFFLINE_MODE=false
isLido=""
# Base directory with scripts
BASE_DIR="${BASE_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
# Load functions
source "$BASE_DIR/functions.sh"
# Load Lido CSM withdrawal address and fee recipient
source "$BASE_DIR"/env

# Pinned version of ethstaker-deposit-cli
edc_version="1.1.0"
edc_hash="08f1e66"

# Get machine info
_platform=$(get_platform)
_arch=$(get_arch)

function downloadEthstakerDepositCli(){
    if [ -d "$DEPOSIT_CLI_PATH" ]; then
        edc_version_installed=$("$DEPOSIT_CLI_PATH"/deposit --version)
        if [[ "${edc_version_installed}" =~ .*"${edc_version}".* ]]; then
            echo "ethstaker_deposit-cli is up-to-date"
            return
        else
            rm "$DEPOSIT_CLI_PATH"/deposit
            echo "ethstaker_deposit-cli update available"
            echo "Updating to v${edc_version}"
            echo "from ${edc_version_installed}"
        fi
    fi
    ohai "Downloading ethstaker_deposit-cli v${edc_version}"
    #Install dependencies
    sudo apt install jq curl -y

    #Setup variables
    BINARIES_URL="https://github.com/ethstaker/ethstaker-deposit-cli/releases/download/v${edc_version}/ethstaker_deposit-cli-${edc_hash}-${_platform}-${_arch}.tar.gz"
    BINARY_FILE="ethstaker_deposit-cli.tar.gz"

    ohai "Downloading URL: $BINARIES_URL"
    # Make temporary directory and ensure cleanup
    TEMP_DIR=$(mktemp -d)
    trap 'rm -rf "$TEMP_DIR"' EXIT

    # Download
    if ! curl -fSL "$BINARIES_URL" -o "${TEMP_DIR}/${BINARY_FILE}"; then
        echo "ERROR: Unable to download $BINARIES_URL" >&2
        exit 1
    fi
    mkdir -p "$TEMP_DIR/extracted"
    tar -xzf "$TEMP_DIR/${BINARY_FILE}" -C "$TEMP_DIR/extracted"

    # Install path
    mkdir -p "$DEPOSIT_CLI_PATH"
    # Install binary
    mv "$TEMP_DIR"/extracted/ethstaker_deposit-cli-*/deposit "$DEPOSIT_CLI_PATH"
}

function generateNewValidatorKeys(){
    [[ $# -eq 1 ]] && local ARGUMENT=$1 && checkLido "$1" || ARGUMENT="default"
    if network_isConnected; then
        if whiptail --title "Offline Key Generation" --defaultno --yesno "$MSG_OFFLINE" 20 78; then
            network_down
            OFFLINE_MODE=true
            ohai "Network is offline mode"
        fi
    fi

    _getNetwork

    if [ -z "$NETWORK" ]; then exit; fi # pressed cancel
    if ! whiptail --title "Information on Secret Recovery Phrase Mnemonic" --yesno "$MSG_INTRO" 24 78; then exit; fi
    if network_isConnected; then whiptail --title "Warning: Internet Connection Detected" --msgbox "$MSG_INTERNET" 18 78; fi
    setConfig
    _getEthAddy
    _getValidatorType
    _getAmount

    NUMBER_NEW_KEYS=$(whiptail --title "# of New Keys" --inputbox "How many keys to generate?" 8 78 --ok-button "Submit" 3>&1 1>&2 2>&3)
    _setKeystorePassword

    cd "$DEPOSIT_CLI_PATH" || true
    KEYFOLDER="${DEPOSIT_CLI_PATH}/$(date +%F-%H%M%S)"
    mkdir -p "$KEYFOLDER"
    ./deposit --non_interactive new-mnemonic --chain "$NETWORK" --execution_address "$ETHADDRESS" --num_validators "$NUMBER_NEW_KEYS" --keystore_password "$_KEYSTOREPASSWORD" --folder "$KEYFOLDER" "$_VALIDATORTYPE" "$_AMOUNT"
    if [ $? -eq 0 ]; then
        #Update path
        KEYFOLDER="$KEYFOLDER/validator_keys"
        # $1 is argument for CSM Validator Plugin
        loadKeys "$ARGUMENT"
        if [ $OFFLINE_MODE == true ]; then
            network_up
            ohai "Network is online"
        fi
    else
        ohai "Error with staking-deposit-cli. Try again."
        exit
    fi
}

function _getEthAddy(){
    while true; do
        ETHADDRESS=$(whiptail --title "Ethereum Withdrawal Address" --inputbox "$MSG_ETHADDRESS" 15 78 --ok-button "Submit" 3>&1 1>&2 2>&3)
        if [ -z "$ETHADDRESS" ]; then exit; fi #pressed cancel
        if [[ "${ETHADDRESS}" =~ ^0x[a-fA-F0-9]{40}$ ]]; then
            break
        else
            whiptail --title "Error" --msgbox "Invalid ETH address. Try again." 8 78
        fi
    done
}

function _getNetwork(){
    NETWORK=$(whiptail --title "Network" --menu \
          "For which network are you generating validator keys?" 10 78 4 \
          "mainnet" "Ethereum - Real ETH. Real staking rewards." \
          "ephemery" "short term Testnet - Ideal for staking practice. Monthly resets." \
          "hoodi" "long term Testnet - Suitable for staking practice." \
          "holesky" "long term Testnet - Deprecated" \
          3>&1 1>&2 2>&3)
}

function _getValidatorType(){
    if [[ $isLido ]]; then
        _VALIDATORTYPE="--regular-withdrawal"
        return
    fi
    _VALIDATORTYPE=$(whiptail --title "Validator Type" --menu \
          "Type of Validator?" 10 90 2 \
          "compounding" "Accumulating. Up to 2048 ETH max balance. 0x02 withdrawal credentials" \
          "regular-withdrawal" "Distributing. 32 ETH max balance. 0x01 withdrawal credentials" \
          3>&1 1>&2 2>&3)
    if [ -z "$_VALIDATORTYPE" ]; then exit; fi # pressed cancel
    _VALIDATORTYPE="--$_VALIDATORTYPE"
}

function _getAmount(){
    if [[ $isLido ]] || [[ "$_VALIDATORTYPE" == "--regular-withdrawal" ]]; then
        _AMOUNT="--amount=32"
        return
    fi
    while true; do
        _AMOUNT=$(whiptail --title "Validator Amount" --inputbox "Please enter the amount of ETH you wish to deposit to these validator(s).\nRequires at least 32 ETH or at max 2048 ETH." 15 78 --ok-button "Submit" 3>&1 1>&2 2>&3)
        if [ -z "$_AMOUNT" ]; then exit; fi #pressed cancel
        if [[ "${_AMOUNT}" -ge 32 ]] && [[ "${_AMOUNT}" -le 2048 ]]; then
            _AMOUNT="--amount=${_AMOUNT}"
            break
        else
            whiptail --title "Error" --msgbox "Invalid Amount. Amount must be between 32 and 2048." 8 78
        fi
    done
}

function importCharonKeyShares(){
    # Optional: pass "skip_confirm" to run after Import .charon already asked.
    local skip_confirm="${1:-}"
    local CHARON_KEYS
    CHARON_KEYS="$(getCharonValidatorKeysDir)"
    if ! charonKeysharesPresent; then
        whiptail --title "${OBOL_CHARON_KEY_SHARES}" --msgbox \
"No Charon key shares found at:
${CHARON_KEYS}

Copy your CDVN (or DKG) .charon folder first:
  EthPillar → ${OBOL_CHARON_DV} → Import .charon cluster folder
  (or: sudo cp -a /path/to/.charon/. $(getCharonClusterDir)/
       sudo chown -R charon:charon $(getCharonClusterDir))

Then import the key shares into the validator client." 18 78
        return
    fi
    getClientVC
    if [[ -z "${VC:-}" ]]; then
        getValidatorClient >/dev/null 2>&1 || true
        VC="${VALIDATOR_CLIENT:-}"
    fi
    if [[ -z "${VC:-}" ]]; then
        whiptail --title "${OBOL_CHARON_KEY_SHARES}" --msgbox \
"Could not detect the validator client from validator.service.
Install a signer VC first, then retry." 10 70
        return 1
    fi
    if [[ "$skip_confirm" != "skip_confirm" ]]; then
        if ! whiptail --title "${OBOL_IMPORT_KEY_SHARES}" --yesno \
"Import EIP-2335 key shares from:
${CHARON_KEYS}

Signer VC: ${VC}

These are cluster key shares (not full solo keys). Import uses DKG
keystore-*.txt passphrases (no password prompt). After import, Charon
is started, then the validator client.

Continue?" 18 78; then
            return
        fi
    fi
    ohai "Importing ${OBOL_MARK} Obol Charon key shares into ${VC}…"
    if [[ "$VC" == "Grandine" ]]; then
        sudo systemctl stop consensus 2>/dev/null || true
    else
        sudo systemctl stop validator 2>/dev/null || true
    fi
    set +e
    PYTHONPATH="${BASE_DIR}" python3 - <<PY
from deploy.charon import sync_charon_keyshares_to_vc
result = sync_charon_keyshares_to_vc("${VC}", force=True)
print(result)
status = result.get("status")
if status == "failed":
    raise SystemExit(1)
if status == "skipped" and not str(result.get("reason", "")).startswith("destination already has"):
    raise SystemExit(1)
if status == "unsupported":
    raise SystemExit(1)
PY
    local rc=$?
    set -e
    if [[ $rc -ne 0 ]]; then
        whiptail --title "${OBOL_CHARON_KEY_SHARES}" --msgbox \
"Key share import into ${VC} failed. Check the terminal output.
Do not use solo-key Import (loadKeys) for Charon shares." 12 70
        return 1
    fi
    ensureCharonBeforeValidator
    if [[ "$VC" == "Grandine" ]]; then
        sudo systemctl start consensus
    else
        sudo systemctl start validator
    fi
    whiptail --title "${OBOL_CHARON_KEY_SHARES}" --msgbox \
"Imported Charon key shares into ${VC}.
Start order: Charon, then the validator client." 10 70
    promptViewLogs "default"
}

function importValidatorKeys(){
    [[ $# -eq 1 ]] && local ARGUMENT=$1 && checkLido "$1" || ARGUMENT="default"
    KEYFOLDER=$(whiptail --title "Import Validator Keys from Offline Generation or Backup" --inputbox "$MSG_PATH" 16 78 --ok-button "Submit" 3>&1 1>&2 2>&3)
    if [ -d "$KEYFOLDER" ]; then
        _getNetwork

        if [ -z "$NETWORK" ]; then exit; fi # pressed cancel
        setConfig

        if whiptail --title "Important Information" --defaultno --yesno "$MSG_IMPORT" 20 78; then
            _KEYSTOREPASSWORD=""
            # $1 is argument for CSM Validator Plugin
            loadKeys "$ARGUMENT"
        fi
    else
        ohai "$KEYFOLDER does not exist. Try again."
        exit
    fi
}

function addRestoreValidatorKeys(){
    [[ $# -eq 1 ]] && local ARGUMENT=$1 && checkLido "$1" || ARGUMENT="default"
    if whiptail --title "Offline Key Generation" --defaultno --yesno "$MSG_OFFLINE" 20 78; then
        network_down
        OFFLINE_MODE=true
        ohai "Network is down"
    fi
    _getNetwork

    if [ -z "$NETWORK" ]; then exit; fi # pressed cancel
    if ! whiptail --title "Information on Secret Recovery Phrase Mnemonic" --yesno "$MSG_INTRO" 24 78; then exit; fi
    if network_isConnected; then whiptail --title "Warning: Internet Connection Detected" --msgbox "$MSG_INTERNET" 18 78; fi
    setConfig
    _getEthAddy
    _getValidatorType
    _getAmount

    NUMBER_NEW_KEYS=$(whiptail --title "# of New Keys" --inputbox "How many keys to generate?" 8 78 --ok-button "Submit" 3>&1 1>&2 2>&3)
    START_INDEX=$(whiptail --title "# of Existing Keys" --inputbox "How many validator keys were previously made? Also known as the starting index." 10 78 --ok-button "Submit" 3>&1 1>&2 2>&3)
    whiptail --title "Keystore Password" --msgbox "Reminder to use the same keystore password as existing validators." 10 78
    _setKeystorePassword

    cd "$DEPOSIT_CLI_PATH" || true
    KEYFOLDER="${DEPOSIT_CLI_PATH}/$(date +%F-%H%M%S)"
    mkdir -p "$KEYFOLDER"
    ./deposit --non_interactive existing-mnemonic --chain "$NETWORK" --execution_address "$ETHADDRESS" --folder "$KEYFOLDER" --keystore_password "$_KEYSTOREPASSWORD" --validator_start_index "$START_INDEX" --num_validators "$NUMBER_NEW_KEYS" "$_VALIDATORTYPE" "$_AMOUNT"
    if [ $? -eq 0 ]; then
        #Update path
        KEYFOLDER="$KEYFOLDER/validator_keys"
        # $1 is argument for CSM Validator Plugin
        loadKeys "$ARGUMENT"
        if [ $OFFLINE_MODE == true ]; then
            network_up
            ohai "Network is online"
        fi
    else
        ohai "Error with staking-deposit-cli. Try again."
        exit
    fi
}

function _setKeystorePassword(){
    while true; do
        # Get keystore password
        _KEYSTOREPASSWORD=$(whiptail --title "Keystore Password" --inputbox "Enter your validator's keystore password, must be at least 12 chars. " 12 78 --ok-button "Submit" 3>&1 1>&2 2>&3)
        if [[ ${#_KEYSTOREPASSWORD} -ge 12 ]]; then
            _VERIFY_PASS=$(whiptail --title "Verify Password" --inputbox "Confirm your keystore password" 12 78 --ok-button "Submit" 3>&1 1>&2 2>&3)
            if [[ "${_KEYSTOREPASSWORD}" = "${_VERIFY_PASS}" ]]; then
                ohai "Password is same."
                break
            else
                whiptail --title "Error" --msgbox "Passwords not the same. Try again." 8 78
            fi
        else
            whiptail --msgbox "The keystore password must be at least 8 characters long." 8 78
        fi
    done
}

function promptForKeystorePasswordForImport(){
    while true; do
        _KEYSTOREPASSWORD=$(whiptail --title "Keystore Password" --inputbox "Enter your keystore password" 10 78 --ok-button "Submit" 3>&1 1>&2 2>&3)
        local VERIFY_PASS
        VERIFY_PASS=$(whiptail --title "Verify Password" --inputbox "Confirm your keystore password" 10 78 --ok-button "Submit" 3>&1 1>&2 2>&3)
        if [[ "${_KEYSTOREPASSWORD}" = "${VERIFY_PASS}" ]]; then
            ohai "Password confirmed."
            break
        else
            whiptail --title "Error" --msgbox "Passwords do not match. Try again." 8 78
        fi
    done
}
function setConfig(){
    case $NETWORK in
          mainnet)
            LAUNCHPAD_URL="https://launchpad.ethereum.org"
            LAUNCHPAD_URL_LIDO=${LAUNCHPAD_URL_LIDO_MAINNET}
            CSM_WITHDRAWAL_ADDRESS=${CSM_WITHDRAWAL_ADDRESS_MAINNET}
            CSM_SENTINEL_URL="https://t.me/CSMSentinel_bot"
            FAUCET=""
            HOMEPAGE="https://ethereum.org"
            EXPLORER="https://beaconcha.in"
          ;;
          holesky)
            # holesky.launchpad.ethstaker.cc is unreachable; use official EF launchpad.
            LAUNCHPAD_URL="https://holesky.launchpad.ethereum.org"
            LAUNCHPAD_URL_LIDO=${LAUNCHPAD_URL_LIDO_HOLESKY}
            CSM_WITHDRAWAL_ADDRESS=${CSM_WITHDRAWAL_ADDRESS_HOLESKY}
            CSM_SENTINEL_URL="https://t.me/CSMSentinelHolesky_bot"
            FAUCET="https://holesky-faucet.pk910.de"
            HOMEPAGE="https://holesky.ethpandaops.io"
            EXPLORER="https://holesky.beaconcha.in"
          ;;
          hoodi)
            # hoodi.launchpad.ethstaker.cc redirects to hoodi.console.ethstaker.org;
            # use official EF launchpad for consistency with mainnet.
            LAUNCHPAD_URL="https://hoodi.launchpad.ethereum.org"
            LAUNCHPAD_URL_LIDO=${LAUNCHPAD_URL_LIDO_HOODI}
            CSM_WITHDRAWAL_ADDRESS=${CSM_WITHDRAWAL_ADDRESS_HOODI}
            CSM_SENTINEL_URL="https://t.me/CSMSentinelHoodi_bot"
            FAUCET="https://hoodi-faucet.pk910.de"
            HOMEPAGE="https://hoodi.ethpandaops.io"
            EXPLORER="https://hoodi.cloud.blockscout.com"
          ;;
          ephemery)
            # Lido launchpad and CSM withdrawal address reuse the HOODI values;
            # the CSM sentinel URL is a placeholder
            LAUNCHPAD_URL="https://launchpad.ephemery.dev"
            LAUNCHPAD_URL_LIDO=${LAUNCHPAD_URL_LIDO_HOODI}
            CSM_WITHDRAWAL_ADDRESS=${CSM_WITHDRAWAL_ADDRESS_HOODI}
            CSM_SENTINEL_URL="https://t.me/CSMSentinelTBD"
            FAUCET="https://faucet.bordel.wtf"
            HOMEPAGE="https://ephemery.dev"
            EXPLORER="https://beaconlight.ephemery.dev"
          ;;
    esac

    # Check if Lido CSM Validator
    if [[ $isLido ]]; then
        # Update message for Lido
        MSG_ETHADDRESS="\nSet this to Lido's CSM Withdrawal Vault Address.
\n${NETWORK}: ${CSM_WITHDRAWAL_ADDRESS}
\nIn checksum format, enter the Withdrawal Address:"
    fi
}

function checkLido(){
    [[ $# -eq 1 ]] && local ARGUMENT=$1 || ARGUMENT="default"
    if [[ $(grep --ignore-case -oE "${CSM_FEE_RECIPIENT_ADDRESS_MAINNET}" /etc/systemd/system/validator.service) ||
          $(grep --ignore-case -oE "${CSM_FEE_RECIPIENT_ADDRESS_HOLESKY}" /etc/systemd/system/validator.service) ||
          $(grep --ignore-case -oE "${CSM_FEE_RECIPIENT_ADDRESS_HOODI}" /etc/systemd/system/validator.service) ||
          "$ARGUMENT" == "plugin_csm_validator" ]]; then
      isLido="1"
    fi
}

# Load validator keys into validator client
function loadKeys(){
   case $1 in
       default)
        getClientVC
        if [[ "$VC" == "Grandine" ]]; then
            sudo systemctl stop consensus
        else
            sudo systemctl stop validator
        fi
        ;;
      plugin_csm_validator)
        VC="Nimbus"
        local __DATA_DIR=${DATA_DIR}
        local __BINARY_PATH="${PLUGIN_INSTALL_PATH}"
        local __SERVICE_USER="${SERVICE_ACCOUNT}"
        local __SERVICE_NAME="${SERVICE_NAME}"
        sudo systemctl stop ${__SERVICE_NAME}
        ;;
   esac

   ohai "Loading PubKeys into $VC Validator"
   ohai "Stopping validator to import keys"

   case $VC in
      Lighthouse)
        [[ -d /var/lib/lighthouse_validator ]] && vc_path="/var/lib/lighthouse_validator" || vc_path="/var/lib/lighthouse/validators"
        LH_BIN=$(get_systemd_exec_path "/etc/systemd/system/consensus.service" "/usr/local/bin/lighthouse")
        sudo "$LH_BIN" account validator import \
          --datadir "$vc_path" \
          --directory="$KEYFOLDER" \
          --reuse-password
        sudo chown -R validator:validator "$vc_path"
        sudo chmod 700 "$vc_path"
      ;;

     Lodestar)
        promptForKeystorePasswordForImport
        echo "$_KEYSTOREPASSWORD" > "$HOME"/validators-password.txt

        [[ -d /var/lib/lodestar_validator ]] && vc_path="/var/lib/lodestar_validator" || vc_path="/var/lib/lodestar/validators"
        LODESTAR_BIN=$(get_systemd_exec_path "/etc/systemd/system/consensus.service" "/usr/local/bin/lodestar")

        sudo "$LODESTAR_BIN" validator import \
            --dataDir="$vc_path" \
            --keystore="$KEYFOLDER" \
            --passphraseFile="$HOME/validators-password.txt"

        sudo chown -R validator:validator "$vc_path"
        sudo chmod 700 "$vc_path"
        rm -f "$HOME"/validators-password.txt
      ;;

     Teku)
        promptForKeystorePasswordForImport
        echo "$_KEYSTOREPASSWORD" > "$HOME"/validators-password.txt

        for f in "$KEYFOLDER"/keystore*.json; do
            sudo cp "$HOME"/validators-password.txt "$KEYFOLDER"/"$(basename "$f" .json)".txt
        done

        sudo mkdir -p /var/lib/teku_validator/validator_keys
        sudo cp "$KEYFOLDER"/keystore* /var/lib/teku_validator/validator_keys
        sudo chown -R validator:validator /var/lib/teku_validator
        sudo chmod -R 700 /var/lib/teku_validator
        rm -f "$HOME"/validators-password.txt
      ;;

     Nimbus)
        if [[ "$1" = "plugin_csm_validator" ]]; then
            sudo "${__BINARY_PATH}"/nimbus_beacon_node deposits import \
                --data-dir="${__DATA_DIR}" "$KEYFOLDER"
            sudo chown -R "${__SERVICE_USER}":"${__SERVICE_USER}" "${__DATA_DIR}"
            sudo chmod -R 700 "${__DATA_DIR}"
        else
            NIMBUS_BIN=$(get_systemd_exec_path "/etc/systemd/system/consensus.service" "/usr/local/bin/nimbus_beacon_node")
            sudo "$NIMBUS_BIN" deposits import \
                --data-dir=/var/lib/nimbus_validator "$KEYFOLDER"
            sudo chown -R validator:validator /var/lib/nimbus_validator
            sudo chmod -R 700 /var/lib/nimbus_validator
        fi
      ;;

     Prysm)
        PRYSM_VC=$(get_systemd_exec_path "/etc/systemd/system/validator.service" "/usr/local/bin/prysm-validator")
        sudo "$PRYSM_VC" accounts import \
          --accept-terms-of-use \
          --wallet-dir=/var/lib/prysm_validator/validator_keys \
          --keys-dir="$KEYFOLDER"
        sudo chown -R validator:validator /var/lib/prysm_validator
        sudo chmod -R 700 /var/lib/prysm_validator
      ;;

     Grandine)
        promptForKeystorePasswordForImport
        echo "$_KEYSTOREPASSWORD" > "$HOME"/validators-password.txt

        for f in "$KEYFOLDER"/keystore*.json; do
            sudo cp "$HOME"/validators-password.txt "$KEYFOLDER"/"$(basename "$f" .json)".txt
        done

        sudo mkdir -p /var/lib/grandine/validator_keys
        sudo cp "$KEYFOLDER"/keystore* /var/lib/grandine/validator_keys
        sudo chown -R consensus:consensus /var/lib/grandine/validator_keys
        sudo chmod -R 700 /var/lib/grandine/validator_keys
        rm -f "$HOME"/validators-password.txt
      ;;
   esac

   ohai "Starting validator"
   if [[ $1 == "default" ]]; then
        ensureCharonBeforeValidator
        if [[ "$VC" == "Grandine" ]]; then
            sudo systemctl start consensus
        else
            sudo systemctl start validator
        fi
   fi

   [[ $1 == "plugin_csm_validator" ]] && sudo systemctl start "${__SERVICE_NAME}"

   queryEntryQueue
   setLaunchPadMessage
   whiptail --title "Next Steps: Upload JSON Deposit Data File" --msgbox "$MSG_LAUNCHPAD" 25 95
   whiptail --title "Tips: Things to Know" --msgbox "$MSG_TIPS" 24 78
   ohai "Finished loading keys"
   promptViewLogs "$1"
}

function setLaunchPadMessage(){
    MSG_FAUCET="" && MSG_HOMEPAGE="" && MSG_EXPLORER=""
    [[ -n ${FAUCET} ]] && MSG_FAUCET=">> Faucet Available: $FAUCET"
    [[ -n ${HOMEPAGE} ]] && MSG_HOMEPAGE=">> Network Homepage: $HOMEPAGE"
    [[ -n ${EXPLORER} ]] && MSG_EXPLORER=">> Explorer:         $EXPLORER"
    MSG_LAUNCHPAD="1) For new validator keys, visit the Launchpad: $LAUNCHPAD_URL
\n2) Upload your deposit_data-#########.json found in the directory:
\n$KEYFOLDER
\n3) Connect the Launchpad with your wallet, review and accept terms.
\n4) Complete your ETH deposit transaction(s).
\n5) Wait for validators to become active. $MSG_VALIDATOR_QUEUE
\n🔥 REMINDER: Importing already ACTIVE Validator(s)? Skip above steps.
\nUseful links:
$MSG_HOMEPAGE
$MSG_EXPLORER
$MSG_FAUCET"

    MSG_TIPS=" - Wait for Node Sync: Before making a deposit, ensure your EL/CL client is synced to avoid missing rewards.
\n - Timing of Validator Activation: After depositing, it takes about 15 hours for a validator to be activated unless there's a long entry queue.
\n - Backup Keystore Files: Keep copies on offline USB storage.
   Location: $KEYFOLDER
\n - Generate Voluntary Exit Message: Once active and assigned an index #, generate your validator's VEM. To stop validator duties, broadcast VEM."

    MSG_LAUNCHPAD_LIDO="1) Visit Lido CSM: $LAUNCHPAD_URL_LIDO
\n2) Connect your wallet on the correct network, review and accept terms.
\n3) Copy JSON from your deposit_data-#########.json
\nTo view JSON, run command:
cat $KEYFOLDER/deposit*
\n4) Provide the ~2 ETH/stETH bond per validator.
\n5) Lido will deposit the 32ETH. Wait for your validators to become active. $MSG_VALIDATOR_QUEUE
\nUseful links:
$MSG_HOMEPAGE
$MSG_EXPLORER
$MSG_FAUCET"

    MSG_TIPS_LIDO=" - DO NOT DEPOSIT 32ETH YOURSELF: Lido will deposit for you.
\n - Wait for Node Sync: Before making the ~2ETH bond deposit, ensure your EL/CL client is synced to avoid missing rewards.
\n - Timing of Validator Activation: After depositing, it takes about 15 hours for a validator to be activated unless there's a long entry queue.
\n - Backup Keystore Files: Keep copies on offline USB storage.
   Location: $KEYFOLDER
\n - Subscribe to CSM Sentinel Bot: Provides your CSM Node Operator events via telegram $CSM_SENTINEL_URL
\n - Generate Voluntary Exit Message: Once active and assigned an index #, generate your validator's VEM. To stop validator duties, broadcast VEM."

    if [[ $isLido ]]; then
       # Update message for Lido
       MSG_LAUNCHPAD="${MSG_LAUNCHPAD_LIDO}"
       MSG_TIPS="$MSG_TIPS_LIDO"
    fi
}

function queryEntryQueue(){
    #Variables
    BEACONCHAIN_VALIDATOR_QUEUE_API_URL="/api/v1/validators/queue"
    declare -A BEACONCHAIN_URLS=(
        ["mainnet"]="https://beaconcha.in"
        ["holesky"]="https://holesky.beaconcha.in"
        ["hoodi"]="https://hoodi.beaconcha.in"
        ["ephemery"]="https://beaconchain.ephemery.dev"
    )

    # Validate network mapping
    if [[ -z "${BEACONCHAIN_URLS["${NETWORK}"]}" ]]; then
        echo "Error: Unsupported Network '${NETWORK}' for validator queue queries." >&2
        return 1
    fi

    # Pectra churn values
    local CHURN_LIMIT_PER_DAY=57600

    # Query for data
    local json
    if ! json=$(curl -fsSL "${BEACONCHAIN_URLS["${NETWORK}"]}"${BEACONCHAIN_VALIDATOR_QUEUE_API_URL}); then
        echo "ERROR: Beaconchain Entry Queue API request failed." >&2
        MSG_VALIDATOR_QUEUE=""
        return 1
    fi

    # Parse JSON using jq and print data
    if echo "$json" | jq -e 'has("data") and .data.beaconchain_entering != null' > /dev/null; then
        entering=$(echo "$json" | jq -r '.data.beaconchain_entering')
      if (( entering > 0 )); then
        wait_time=$(calculate_days_hours_and_minutes "$(echo "scale=6; $entering / $CHURN_LIMIT_PER_DAY" | bc)")
      else
        wait_time="No wait"
      fi
        MSG_VALIDATOR_QUEUE="For ${NETWORK}, currently $entering ETH waiting to join. ETA: $wait_time"
    else
      echo "DEBUG: Unable to query beaconcha.in for $NETWORK validator queue data."
      MSG_VALIDATOR_QUEUE=""
    fi
}

function getClientVC(){
    if [ -f /etc/systemd/system/validator.service ]; then
        VC=$(grep "Description=" /etc/systemd/system/validator.service | awk -F'=' '{print $2}' | awk '{print $1}')
    else
        # VC is integrated into Grandine BN. So check for Grandine.
        if grep -q "Grandine" /etc/systemd/system/consensus.service 2>/dev/null; then
            VC="Grandine"
        fi
    fi
}

function promptViewLogs(){
    if whiptail --title "Validator Keys Imported - $VC" --yesno "Would you like to view logs and confirm everything is running properly?" 8 78; then
        case $1 in
            default)
               view_journal_logs -fu validator ;;
            plugin_csm_validator)
               view_journal_logs -fu "${SERVICE_NAME}" ;;
        esac
    fi
}

function setMessage(){
    MSG_INTRO="During this step, your Secret Recovery Phrase (also known as a \"mnemonic\") and an accompanying set of validator keys will be generated specifically for you. For comprehensive information regarding these keys, please refer to: https://kb.beaconcha.in/ethereum-staking/ethereum-2-keys
\nThe importance of safeguarding both the Secret Recovery Phrase and the validator keys cannot be overstated, as they are essential for accessing your funds. Exposure of these keys may lead to theft. To learn how to securely store them, visit: https://www.ledger.com/blog/how-to-protect-your-seed-phrase
\nFor enhanced security, it is strongly recommended that you create the Wagyu Key Gen (https://wagyu.gg) application on an entirely disconnected offline machine. A viable approach to this includes transferring the application onto a USB stick, connecting it to an isolated offline computer, and running it from there. Afterwards, copy your keys back to this machine and import. Continue?"
    MSG_OFFLINE="To ensure maximum security of your secret recovery phrase, it's important to operate this tool in an offline environment.
\nBe certain that your secret recovery phrase remains offline from the internet throughout the process.
\nDisconnecting from the internet might cut off computer access. Ensure you can recover access to this machine or VPS.
\nWould you like to disable the internet while generating keys for enhanced security?"
    MSG_INTERNET="Being connected to the internet while using this tool drastically increases the risk of exposing your Secret Recovery Phrase.
\nYou can avoid this risk by having a live OS such as Tails installed on a USB drive and run on a computer with network capabilities disabled.
\nYou can visit https://tails.net/install/ for instructions on how to download, install, and run Tails on a USB device.
\nIf you have any questions you can get help at https://dsc.gg/ethstaker"
    MSG_PATH="Enter the path to your keystore files.
\nDirectory contains keystore-m.json file(s).
\nExample: $DEPOSIT_CLI_PATH/YYYY-MM-DD-NNNNNN/validator_keys"
    MSG_ETHADDRESS="Ensure that you have control over this address.
\nETH address secured by a hardware wallet is recommended.
\nIn checksum format, enter your Withdrawal Address:"
    MSG_IMPORT="Importing validator keys:
\n1) I acknowledge that if migrating from another node, I must wait for at least two epochs (12 mins 48 sec) before continuing.
\n2) I acknowledge that if migrating from another node, I have deleted the keys from the previous machine. This ensures that the keys will NOT inadvertently restart and run in two places.
\n3) Lastly, these validator keys are NOT operational on any other machine (such as a cloud hosting service or DVT).
\nContinue?"
}

# ---------------------------------------------------------------------------
# Keymanager API helpers (deploy/keymanager.py — Lighthouse/Lodestar/Nimbus/Prysm)
# ---------------------------------------------------------------------------

# True if VC is supported for Keymanager API Phase 1.
keymanagerClientSupported() {
    local c
    c=$(echo "${1:-}" | tr '[:upper:]' '[:lower:]')
    case "$c" in
        lighthouse|lodestar|nimbus|prysm) return 0 ;;
        *) return 1 ;;
    esac
}

# Normalize NETWORK (Mainnet/Hoodi/…) to keymanager lowercase slug.
keymanagerNetwork() {
    local n
    n=$(echo "${NETWORK:-mainnet}" | tr '[:upper:]' '[:lower:]' | tr ' ' '-')
    case "$n" in
        ""|"network-syncing"|"custom-network") echo "mainnet" ;;
        *) echo "$n" ;;
    esac
}

# Resolve VC + network for keymanager actions. Sets KM_CLIENT, KM_NETWORK.
keymanagerResolveContext() {
    getClientVC
    if [[ -z "${VC:-}" ]]; then
        getValidatorClient >/dev/null 2>&1 || true
    fi
    if [[ -z "${VC:-}" ]]; then
        whiptail --title "Keymanager API" --msgbox \
            "No validator client detected.\n\nInstall a separate validator service first." 10 70
        return 1
    fi
    if ! keymanagerClientSupported "$VC"; then
        whiptail --title "Keymanager API" --msgbox \
            "Keymanager API helpers support Lighthouse, Lodestar, Nimbus, and Prysm only.\n\nDetected: ${VC}\n\nUse the classic import/generate options for this client." 12 70
        return 1
    fi
    # Prefer live network detection when available; fall back to mainnet.
    if [[ -z "${NETWORK:-}" || "${NETWORK}" == "Network Syncing" || "${NETWORK}" == "Custom Network" ]]; then
        if declare -f getNetwork >/dev/null 2>&1 && declare -f initializeRpcEndpoints >/dev/null 2>&1; then
            initializeRpcEndpoints 2>/dev/null || true
            getNetwork 2>/dev/null || true
        fi
    fi
    KM_CLIENT="$VC"
    KM_NETWORK="$(keymanagerNetwork)"
    return 0
}

# Run deploy.keymanager CLI; prints JSON on stdout. Ensures venv deps first.
# Usage: keymanagerPy <subcommand> [extra args…]
# Globals: KM_CLIENT, KM_NETWORK
# Note: bootstrap messages go to stderr so command substitution stays clean JSON.
keymanagerPy() {
    local cmd="$1"
    shift || true
    ensure_python_deps >&2
    local py="${ETHPILLAR_PYTHON:-python3}"
    PYTHONPATH="${BASE_DIR}" "$py" -m deploy.keymanager \
        --client "$KM_CLIENT" \
        --network "$KM_NETWORK" \
        "$cmd" "$@"
}

# Print a field from JSON output (uses the last element if an array; if
# several JSON values are given, the last match wins).
keymanagerJsonField() {
    local json="$1"
    local field="$2"
    echo "$json" | jq -r --arg f "$field" '
      (if type=="array" then .[-1] else . end)
      | if type=="object" then .[$f] // empty else empty end
    ' 2>/dev/null | tail -n1
}

# Resolve which systemd unit hosts validator duties.
# Sets KM_VALIDATOR_MODE (none|separate|integrated_grandine) and KM_VALIDATOR_UNIT
# (short name: validator|consensus). Returns 1 if nothing to start.
keymanagerResolveValidatorUnit() {
    local mode
    if declare -f getValidatorMode >/dev/null 2>&1; then
        mode=$(getValidatorMode)
    else
        if [[ -f "${VALIDATOR_SERVICE_FILE:-/etc/systemd/system/validator.service}" ]]; then
            mode="separate"
        elif [[ -f "${CONSENSUS_SERVICE_FILE:-/etc/systemd/system/consensus.service}" ]] \
            && grep -q 'keystore-dir' "${CONSENSUS_SERVICE_FILE:-/etc/systemd/system/consensus.service}" 2>/dev/null; then
            mode="integrated_grandine"
        else
            mode="none"
        fi
    fi
    KM_VALIDATOR_MODE="$mode"
    case "$mode" in
        separate)
            KM_VALIDATOR_UNIT="validator"
            ;;
        integrated_grandine)
            KM_VALIDATOR_UNIT="consensus"
            ;;
        *)
            KM_VALIDATOR_UNIT=""
            return 1
            ;;
    esac
    return 0
}

# True if systemd unit *unit* is active (short name, e.g. validator).
keymanagerValidatorIsActive() {
    local unit="${1:-}"
    [[ -n "$unit" ]] || return 1
    systemctl is-active --quiet "$unit" 2>/dev/null
}

# Probe Keymanager status up to ~10s. Sets KM_STATUS_JSON on success.
# Returns 0 when available=true (includes Prysm empty-wallet available).
keymanagerWaitForApi() {
    local i json available
    for i in 1 2 3 4 5; do
        if json=$(keymanagerPy status 2>/dev/null); then
            available=$(keymanagerJsonField "$json" "available")
            if [[ "$available" == "true" ]]; then
                KM_STATUS_JSON="$json"
                return 0
            fi
        fi
        [[ "$i" -lt 5 ]] && sleep 2
    done
    return 1
}

# If the validator unit is inactive, offer to start it and re-probe Keymanager.
# Args: optional base_url for messaging.
# Returns 0 only if Keymanager becomes available after a successful start.
keymanagerOfferStartValidator() {
    local base_url="${1:-}"
    local unit

    if ! keymanagerResolveValidatorUnit; then
        return 1
    fi
    unit="$KM_VALIDATOR_UNIT"

    if keymanagerValidatorIsActive "$unit"; then
        return 1
    fi

    if ! whiptail --title "Validator Not Running" --yesno \
        "Validator service is not running. Keymanager requires it${base_url:+ (expected ${base_url})}.\n\nStart ${unit}.service now?" \
        12 72; then
        return 1
    fi

    ohai "Starting ${unit}.service…"
    if declare -f startValidatorService >/dev/null 2>&1; then
        startValidatorService || true
    else
        sudo systemctl daemon-reload 2>/dev/null || true
        sudo systemctl start "$unit" 2>/dev/null || true
    fi

    # Confirm the unit came up (direct start as fallback).
    if ! keymanagerValidatorIsActive "$unit"; then
        sudo systemctl start "$unit" 2>/dev/null || true
    fi
    if ! keymanagerValidatorIsActive "$unit"; then
        whiptail --title "Start Failed" --msgbox \
            "Could not start ${unit}.service.\n\nCheck:\n  systemctl status ${unit}\n  journalctl -u ${unit} -n 50" \
            12 72
        return 1
    fi

    ohai "Waiting for Keymanager API after starting ${unit}…"
    if keymanagerWaitForApi; then
        return 0
    fi
    # Process started but API still not answering — caller may offer Enable.
    return 1
}

# Show API status; return 0 if available, 1 otherwise.
# If unreachable, offer to start a stopped validator, then optionally Enable.
# Sets KM_STATUS_JSON on success path.
keymanagerEnsureAvailable() {
    local json available msg base_url
    if ! keymanagerResolveContext; then
        return 1
    fi

    ohai "Checking Keymanager API for ${KM_CLIENT} (${KM_NETWORK})…"
    if ! json=$(keymanagerPy status 2>/tmp/ethpillar_km_err.$$); then
        msg=$(cat /tmp/ethpillar_km_err.$$ 2>/dev/null || true)
        rm -f /tmp/ethpillar_km_err.$$
        # Still try to parse JSON error body on stdout if present
        if [[ -n "$json" ]]; then
            msg=$(keymanagerJsonField "$json" "error")
        fi
        # Status command hard-failed — still try starting a stopped VC once.
        base_url=""
        if keymanagerOfferStartValidator "$base_url"; then
            return 0
        fi
        whiptail --title "Keymanager API" --msgbox \
            "Failed to check Keymanager API status.\n\n${msg:-Unknown error}" 12 70
        return 1
    fi
    rm -f /tmp/ethpillar_km_err.$$
    KM_STATUS_JSON="$json"
    available=$(keymanagerJsonField "$json" "available")

    if [[ "$available" == "true" ]]; then
        return 0
    fi

    base_url=$(keymanagerJsonField "$json" "base_url")

    # Common case: flags/wallet OK but validator.service never started.
    if keymanagerOfferStartValidator "$base_url"; then
        return 0
    fi

    if whiptail --title "Keymanager API Not Available" --yesno \
        "The Keymanager API is not reachable for ${KM_CLIENT}.\n\nExpected near: ${base_url}\n\nEnable the Keymanager API now? (updates validator.service and restarts the validator)" \
        14 70; then
        keymanagerEnableApi
        # Re-check (enable may have restarted; also try start if still down)
        if keymanagerWaitForApi; then
            return 0
        fi
        if keymanagerOfferStartValidator "$base_url"; then
            return 0
        fi
        whiptail --title "Keymanager API" --msgbox \
            "Keymanager API is still not available after enable attempt.\n\nCheck validator logs and that keymanager flags are present in validator.service.\n\n  systemctl status validator\n  journalctl -u validator -n 50" 14 72
        return 1
    fi
    return 1
}

keymanagerListKeys() {
    local json count keys_text empty_hint msg
    if ! keymanagerEnsureAvailable; then
        return 1
    fi
    if ! json=$(keymanagerPy list 2>/tmp/ethpillar_km_err.$$); then
        local err
        err=$(keymanagerJsonField "${json:-}" "error")
        [[ -z "$err" ]] && err=$(cat /tmp/ethpillar_km_err.$$ 2>/dev/null || echo "list failed")
        rm -f /tmp/ethpillar_km_err.$$
        whiptail --title "List Keys Failed" --msgbox "$err" 12 70
        return 1
    fi
    rm -f /tmp/ethpillar_km_err.$$
    count=$(keymanagerJsonField "$json" "key_count")
    keys_text=$(echo "$json" | jq -r '
      (if type=="array" then .[-1] else . end)
      | (.keys // [])
      | if length==0 then "(no keys)"
        else
          to_entries
          | map("\(.key+1). \(.value.validating_pubkey // .value.pubkey // "unknown")")
          | join("\n")
        end
    ' 2>/dev/null)

    empty_hint=""
    if [[ "${count:-0}" -eq 0 ]]; then
        empty_hint=$'\n\nNo keys loaded in the Keymanager API.'
        # Prysm returns this state before the first keystore import.
        if [[ "$(echo "${KM_CLIENT:-}" | tr '[:upper:]' '[:lower:]')" == "prysm" ]] \
            || [[ "$(keymanagerJsonField "$json" "prysm_empty_wallet")" == "true" ]] \
            || [[ "$(keymanagerJsonField "$json" "empty_uninitialized")" == "true" ]]; then
            empty_hint+=$'\nImport keystores first (Import Keys), or use prysm-validator accounts import.'
        fi
    fi

    msg="Client: ${KM_CLIENT}  Network: ${KM_NETWORK}\nEndpoint: $(keymanagerJsonField "$json" "base_url")\n\n${keys_text}${empty_hint}"
    whiptail --title "Keymanager API — Keys (${count:-0})" \
        --scrolltext \
        --msgbox \
        "$msg" \
        18 105
}


keymanagerImportKeystores() {
    local key_dir password verify json err count statuses_text
    if ! keymanagerEnsureAvailable; then
        return 1
    fi

    if ! whiptail --title "Import via Keymanager API" --defaultno --yesno \
        "Import EIP-2335 keystores through the live Keymanager API (no validator stop required for most clients).\n\n${MSG_IMPORT}" 20 78; then
        return 1
    fi

    key_dir=$(whiptail --title "Keystore Directory" --inputbox "$MSG_PATH" 16 78 --ok-button "Submit" 3>&1 1>&2 2>&3) || return 1
    if [[ ! -d "$key_dir" ]]; then
        whiptail --title "Error" --msgbox "Directory does not exist:\n$key_dir" 10 70
        return 1
    fi
    if ! compgen -G "${key_dir}/keystore*.json" >/dev/null \
        && ! compgen -G "${key_dir}/*.json" >/dev/null; then
        whiptail --title "Error" --msgbox "No keystore JSON files found in:\n$key_dir" 10 70
        return 1
    fi

    password=$(whiptail --title "Keystore Password" --passwordbox "Enter keystore password (used for all files in this directory)" 10 70 3>&1 1>&2 2>&3) || return 1
    verify=$(whiptail --title "Confirm Password" --passwordbox "Re-enter keystore password" 10 70 3>&1 1>&2 2>&3) || return 1
    if [[ "$password" != "$verify" ]]; then
        whiptail --title "Error" --msgbox "Passwords do not match." 8 50
        return 1
    fi

    ohai "Importing keystores via Keymanager API…"
    if ! json=$(keymanagerPy import --dir "$key_dir" --password "$password" 2>/tmp/ethpillar_km_err.$$); then
        err=$(keymanagerJsonField "${json:-}" "error")
        [[ -z "$err" ]] && err=$(cat /tmp/ethpillar_km_err.$$ 2>/dev/null || echo "import failed")
        rm -f /tmp/ethpillar_km_err.$$
        whiptail --title "Import Failed" --msgbox "$err" 14 70
        return 1
    fi
    rm -f /tmp/ethpillar_km_err.$$
    count=$(keymanagerJsonField "$json" "imported")
    statuses_text=$(echo "$json" | jq -r '
      (if type=="array" then .[-1] else . end)
      | (.statuses // [])
      | to_entries
      | map("\(.key+1). \(.value.status // "?") \(.value.message // "")")
      | join("\n")
    ' 2>/dev/null)

    whiptail --title "Import Complete" --msgbox \
        "Imported ${count:-?} keystore(s) via Keymanager API.\n\n${statuses_text}" 18 78
}

# Truncate a pubkey for display: first 10 chars + "…" + last 6 (e.g. 0xb1da40f8…a3f9af).
keymanagerTruncatePubkey() {
    local pk="$1"
    local len=${#pk}
    if [[ "$len" -le 18 ]]; then
        echo "$pk"
        return
    fi
    echo "${pk:0:10}…${pk: -6}"
}

keymanagerDeleteKeys() {
    local json count confirm err response_text
    local selection sel_clean idx display_list selected_full selected_display
    local num already existing pubkeys_csv
    local -a ALL_PUBKEYS=()
    local -a SELECTED_PUBKEYS=()
    local -a _nums=()
    if ! keymanagerEnsureAvailable; then
        return 1
    fi

    if ! json=$(keymanagerPy list 2>/tmp/ethpillar_km_err.$$); then
        err=$(keymanagerJsonField "${json:-}" "error")
        [[ -z "$err" ]] && err=$(cat /tmp/ethpillar_km_err.$$ 2>/dev/null || echo "list failed")
        rm -f /tmp/ethpillar_km_err.$$
        whiptail --title "List Keys Failed" --msgbox "$err" 12 70
        return 1
    fi
    rm -f /tmp/ethpillar_km_err.$$
    count=$(keymanagerJsonField "$json" "key_count")
    if [[ "${count:-0}" -eq 0 ]]; then
        whiptail --title "Delete Keys" --msgbox "No keys are loaded in the Keymanager API." 8 60
        return 1
    fi

    # Full pubkeys (1-based line number → ALL_PUBKEYS[n-1])
    mapfile -t ALL_PUBKEYS < <(echo "$json" | jq -r '
      (if type=="array" then .[-1] else . end)
      | (.keys // [])
      | .[]
      | (.validating_pubkey // .pubkey // empty)
    ' 2>/dev/null)
    if [[ ${#ALL_PUBKEYS[@]} -eq 0 ]]; then
        whiptail --title "Delete Keys" --msgbox "No keys are loaded in the Keymanager API." 8 60
        return 1
    fi
    count=${#ALL_PUBKEYS[@]}

    display_list=""
    for i in "${!ALL_PUBKEYS[@]}"; do
        display_list+="$((i + 1)). $(keymanagerTruncatePubkey "${ALL_PUBKEYS[$i]}")"$'\n'
    done

    selection=$(whiptail --title "Delete Keystores (Keymanager API)" --inputbox \
        "Keys currently loaded (${count}):\n\n${display_list}\nEnter line number(s) to DELETE, comma-separated (e.g. 1,3 or 2).\nThis removes them from the local validator only." \
        22 78 --ok-button "Continue" 3>&1 1>&2 2>&3) || return 1
    # Allow spaces around commas: "1, 3" → "1,3"
    sel_clean=$(echo "$selection" | tr -d '[:space:]')
    if [[ -z "$sel_clean" ]]; then
        return 1
    fi
    # Only digits and commas
    if [[ ! "$sel_clean" =~ ^[0-9]+(,[0-9]+)*$ ]]; then
        whiptail --title "Invalid Selection" --msgbox \
            "Enter comma-separated line numbers only (e.g. 1,3).\n\nGot: ${selection}" 10 70
        return 1
    fi

    SELECTED_PUBKEYS=()
    selected_full=""
    selected_display=""
    IFS=',' read -ra _nums <<< "$sel_clean"
    for num in "${_nums[@]}"; do
        if [[ -z "$num" || "$num" -lt 1 || "$num" -gt "$count" ]]; then
            whiptail --title "Invalid Selection" --msgbox \
                "Line number '${num}' is out of range (1–${count})." 10 70
            return 1
        fi
        idx=$((num - 1))
        # Skip duplicate selections
        already=0
        for existing in "${SELECTED_PUBKEYS[@]}"; do
            if [[ "$existing" == "${ALL_PUBKEYS[$idx]}" ]]; then
                already=1
                break
            fi
        done
        if [[ "$already" -eq 1 ]]; then
            continue
        fi
        SELECTED_PUBKEYS+=("${ALL_PUBKEYS[$idx]}")
        selected_full+="${ALL_PUBKEYS[$idx]}"$'\n'
        selected_display+="${num}. $(keymanagerTruncatePubkey "${ALL_PUBKEYS[$idx]}")"$'\n'
    done
    if [[ ${#SELECTED_PUBKEYS[@]} -eq 0 ]]; then
        whiptail --title "Delete Keys" --msgbox "No keys selected." 8 50
        return 1
    fi

    if ! whiptail --title "⚠️ CONFIRM DELETE" --defaultno --yesno \
        "You are about to DELETE these validating keys from the local client:\n\n${selected_full}\nThis cannot be undone from this menu (restore from keystore backup if needed).\n\nProceed?" \
        20 78; then
        return 1
    fi

    confirm=$(whiptail --title "Type DELETE to confirm" --inputbox \
        "Selected: ${selected_display}\nType DELETE (all caps) to permanently remove the selected keys from this validator." \
        14 78 3>&1 1>&2 2>&3) || return 1
    if [[ "$confirm" != "DELETE" ]]; then
        whiptail --title "Cancelled" --msgbox "Confirmation text did not match. No keys were deleted." 8 60
        return 1
    fi

    # Comma-separated full pubkeys for the API
    pubkeys_csv=$(IFS=,; echo "${SELECTED_PUBKEYS[*]}")

    ohai "Deleting keys via Keymanager API…"
    if ! json=$(keymanagerPy delete --pubkeys "$pubkeys_csv" 2>/tmp/ethpillar_km_err.$$); then
        err=$(keymanagerJsonField "${json:-}" "error")
        [[ -z "$err" ]] && err=$(cat /tmp/ethpillar_km_err.$$ 2>/dev/null || echo "delete failed")
        rm -f /tmp/ethpillar_km_err.$$
        whiptail --title "Delete Failed" --msgbox "$err" 14 70
        return 1
    fi
    rm -f /tmp/ethpillar_km_err.$$
    response_text=$(echo "$json" | jq -r '
      (if type=="array" then .[-1] else . end)
      | (.response.data // .response // .)
      | if type=="array" then
          to_entries | map("\(.key+1). \(.value.status // .value)") | join("\n")
        else tostring end
    ' 2>/dev/null)

    whiptail --title "Delete Complete" --msgbox \
        "Delete request finished.\n\n${response_text}" 16 78
}

keymanagerEnableApi() {
    local dry_json real_json msg flags err
    if ! keymanagerResolveContext; then
        return 1
    fi

    ohai "Planning Keymanager API enablement for ${KM_CLIENT}…"
    if ! dry_json=$(keymanagerPy enable --dry-run 2>/tmp/ethpillar_km_err.$$); then
        err=$(keymanagerJsonField "${dry_json:-}" "error")
        [[ -z "$err" ]] && err=$(cat /tmp/ethpillar_km_err.$$ 2>/dev/null || echo "dry-run failed")
        rm -f /tmp/ethpillar_km_err.$$
        whiptail --title "Enable Failed" --msgbox "$err" 14 70
        return 1
    fi
    rm -f /tmp/ethpillar_km_err.$$

    flags=$(echo "$dry_json" | jq -r '
      (if type=="array" then .[-1] else . end)
      | "Service: \(.service_path // "?")\n"
        + "Flags to add: \((.flags_added // []) | join(" "))\n"
        + "Already present: \((.flags_already_present // []) | join(" "))\n"
        + "Changed: \(.changed // false)\n"
        + "Message: \(.message // "")"
    ' 2>/dev/null)

    local base_url
    base_url=$(keymanagerJsonField "$dry_json" "base_url")

    if [[ "$(keymanagerJsonField "$dry_json" "changed")" != "true" ]]; then
        whiptail --title "Keymanager API" --msgbox \
            "No service changes needed — keymanager flags already present.\n\n${flags}" 14 78
        # Flags alone are not enough if the VC process never started.
        if keymanagerWaitForApi; then
            return 0
        fi
        if keymanagerOfferStartValidator "${base_url}"; then
            whiptail --title "Keymanager API" --msgbox \
                "Keymanager API is available after starting the validator service.\n\nEndpoint: ${base_url}" 10 70
            return 0
        fi
        # Unit may already be active but API still down — not necessarily a hard failure.
        return 0
    fi

    if ! whiptail --title "Enable Keymanager API" --defaultno --yesno \
        "Apply these changes to the systemd unit and restart the validator?\n\n${flags}\n\nA backup of the service file will be created first." \
        18 78; then
        return 1
    fi

    ohai "Enabling Keymanager API (backup + write + restart)…"
    if ! real_json=$(keymanagerPy enable 2>/tmp/ethpillar_km_err.$$); then
        err=$(keymanagerJsonField "${real_json:-}" "error")
        [[ -z "$err" ]] && err=$(cat /tmp/ethpillar_km_err.$$ 2>/dev/null || echo "enable failed")
        rm -f /tmp/ethpillar_km_err.$$
        whiptail --title "Enable Failed" --msgbox "$err" 14 70
        return 1
    fi
    rm -f /tmp/ethpillar_km_err.$$

    base_url=$(keymanagerJsonField "$real_json" "base_url")
    # Ensure the unit is actually listening after enable/restart.
    if ! keymanagerWaitForApi; then
        keymanagerOfferStartValidator "${base_url}" || true
    fi

    msg=$(echo "$real_json" | jq -r '
      (if type=="array" then .[-1] else . end)
      | "\(.message // "Done")\n\n"
        + "Backup: \(.backup_path // "n/a")\n"
        + "Endpoint: \(.base_url // "?")\n"
        + "Token found: \(if .token then "yes" else "no" end)"
    ' 2>/dev/null)

    whiptail --title "Keymanager API Enabled" --msgbox "$msg" 16 78
}

menuKeymanager() {
    local OPTIONS CHOICE
    OPTIONS=(
      1 "List Keys (Keymanager API)"
      2 "Import Keystores (Keymanager API)"
      3 "Delete Keystores (Keymanager API)"
      4 "Enable Keymanager API"
      - ""
      99 "Back"
    )
    while true; do
        CHOICE=$(whiptail --clear --cancel-button "Back" \
          --backtitle "Public Goods by Coincashew.eth" \
          --title "EthPillar - Keymanager API" \
          --menu "Local keystores only (Lighthouse, Lodestar, Nimbus, Prysm).\nClassic import methods remain available in the previous menu." \
          0 72 0 \
          "${OPTIONS[@]}" \
          3>&1 1>&2 2>&3) || break
        case $CHOICE in
          1) keymanagerListKeys ;;
          2) keymanagerImportKeystores ;;
          3) keymanagerDeleteKeys ;;
          4) keymanagerEnableApi ;;
          99) break ;;
        esac
    done
}

menuMain(){
# Solo key tooling is unsafe for Charon DV shares — hide it when Charon is installed.
buildValidatorKeyMenuOptions
OPTIONS=("${VALIDATOR_KEY_MENU_OPTIONS[@]}")

while true; do
    # Display the main menu and get the user's choice
    CHOICE=$(whiptail --clear --cancel-button "Back"\
      --backtitle "Public Goods by Coincashew.eth" \
      --title "EthPillar - Validator Key Management" \
      --menu "Choose a category:" \
      0 52 0 \
      "${OPTIONS[@]}" \
      3>&1 1>&2 2>&3)
    if [ $? -gt 0 ]; then # user pressed <Cancel> button
        break
    fi

    # Handle the user's choice
    case $CHOICE in
      1)
        if isCharonEnabled; then
          importCharonKeyShares
        else
          generateNewValidatorKeys
        fi
        ;;
      2)
        importValidatorKeys
       ;;
      3)
        addRestoreValidatorKeys
        ;;
      10)
        keymanagerListKeys
        ;;
      11)
        keymanagerImportKeystores
        ;;
      12)
        keymanagerDeleteKeys
        ;;
      13)
        keymanagerEnableApi
        ;;
      99)
        break
        ;;
    esac
done
}

# Populate VALIDATOR_KEY_MENU_OPTIONS for menuMain (and bats).
# Charon installs hide solo Generate/Import/Restore.
buildValidatorKeyMenuOptions(){
  VALIDATOR_KEY_MENU_OPTIONS=()
  if isCharonEnabled; then
    VALIDATOR_KEY_MENU_OPTIONS+=(
      1 "${OBOL_IMPORT_KEY_SHARES} ($(getCharonClusterDir))"
    )
  else
    VALIDATOR_KEY_MENU_OPTIONS+=(
      1 "Generate new validator keys"
      2 "Import validator keys from offline key generation or backup"
      3 "Add new or regenerate existing validator keys from Secret Recovery Phrase"
    )
  fi
  VALIDATOR_KEY_MENU_OPTIONS+=(
    - ""
    10 "List Keys (Keymanager API)"
    11 "Import Keystores (Keymanager API)"
    12 "Delete Keystores (Keymanager API)"
    13 "Enable Keymanager API"
    - ""
    99 "Exit"
  )
}

# Args:
#   (none)              → full key management menu
#   true | plugin_*     → skip menu (CSM plugin source mode)
#   helpers-only        → define functions only (no download/menu; for unit tests)
#   keymanager          → open Keymanager API submenu only
#   charon-import       → import Obol Charon key shares only
#   charon-import-yes   → same, skip the yes/no confirm (caller already asked)
_skip_or_mode="${1:-}"
setMessage
if [[ "$_skip_or_mode" == "keymanager" ]]; then
    checkLido
    menuKeymanager
elif [[ "$_skip_or_mode" == "charon-import" ]]; then
    downloadEthstakerDepositCli
    checkLido
    importCharonKeyShares
elif [[ "$_skip_or_mode" == "charon-import-yes" ]]; then
    checkLido
    importCharonKeyShares skip_confirm
elif [[ "$_skip_or_mode" == "helpers-only" ]]; then
    # Unit tests / pure source — do not download deposit-cli or open menus.
    :
elif [[ -n "$_skip_or_mode" ]]; then
    # Sourced/invoked by CSM plugin with a skip flag — download deposit-cli
    # and run checkLido, but do not open the main menu.
    downloadEthstakerDepositCli
    checkLido
else
    downloadEthstakerDepositCli
    checkLido
    menuMain
fi
