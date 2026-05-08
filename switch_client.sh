#!/bin/bash

# Author: coincashew.eth | coincashew.com
# License: GNU GPL
# Source: https://github.com/mjkeating
#

BASE_DIR=$(pwd)
source $BASE_DIR/functions.sh

function switchClient(){
    local TARGET_CLIENT=$1
    if [ "$TARGET_CLIENT" == "execution" ]; then
        SERVICE="execution.service"
        CURRENT_CLIENT=$EL
    elif [ "$TARGET_CLIENT" == "consensus" ]; then
        SERVICE="consensus.service"
        CURRENT_CLIENT=$CL
    else
        echo "Invalid client type to switch."
        exit 1
    fi

    local SYSTEMD_DIR=${SYSTEMD_DIR:-/etc/systemd/system}
    local BASE_DATA_DIR=${BASE_DATA_DIR:-/var/lib}

    # 1) Backup systemd file
    if [ -f ${SYSTEMD_DIR}/${SERVICE} ]; then
        if whiptail --title "Switch $TARGET_CLIENT Client" --yesno "Backup existing ${SERVICE} file to ${SERVICE}.bak?" 8 78; then
            sudo cp ${SYSTEMD_DIR}/${SERVICE} ${SYSTEMD_DIR}/${SERVICE}.bak
        fi
    fi

    # 2) Remove existing data directory
    DATADIR=""
    # Find data directory based on current client
    case $CURRENT_CLIENT in
        Nethermind) DATADIR="${BASE_DATA_DIR}/nethermind" ;;
        Besu) DATADIR="${BASE_DATA_DIR}/besu" ;;
        Geth) DATADIR="${BASE_DATA_DIR}/geth" ;;
        Erigon) DATADIR="${BASE_DATA_DIR}/erigon" ;;
        Reth) 
            getExecutionDatadir
            DATADIR=${DATADIR:-${BASE_DATA_DIR}/reth}
            ;;
        Lighthouse) DATADIR="${BASE_DATA_DIR}/lighthouse" ;;
        Nimbus) DATADIR="${BASE_DATA_DIR}/nimbus" ;;
        Teku) DATADIR="${BASE_DATA_DIR}/teku" ;;
        Lodestar) DATADIR="${BASE_DATA_DIR}/lodestar" ;;
        Caplin) DATADIR="${BASE_DATA_DIR}/erigon" ;;
        Grandine) DATADIR="${BASE_DATA_DIR}/grandine" ;;
    esac

    if [ -n "$DATADIR" ] && [ -d "$DATADIR" ]; then
        if whiptail --title "Switch $TARGET_CLIENT Client" --yesno "Remove existing client data directory ($DATADIR)?\n\nSelecting Yes will free up disk space. If you select No, the data will be preserved in case you want to switch back later." 10 78; then
            sudo systemctl stop ${TARGET_CLIENT}
            sudo rm -rf $DATADIR
        fi
    fi

    # 3) Get network before stopping anything (requires EL to be running if switching CC)
    getNetwork
    if [ "$NETWORK" == "Network Syncing" ] || [ -z "$NETWORK" ]; then
        # Fallback: scrape from existing systemd file
        if [ -f ${SYSTEMD_DIR}/${SERVICE} ]; then
            NETWORK=$(grep "Description=" "${SYSTEMD_DIR}/${SERVICE}" | grep -oEi "(MAINNET|HOLESKY|SEPOLIA|HOODI|EPHEMERY)" | head -1)
        fi
        # If still not found, try the other service file
        if [ -z "$NETWORK" ]; then
            OTHER_SERVICE="execution.service"
            if [ "$SERVICE" == "execution.service" ]; then OTHER_SERVICE="consensus.service"; fi
            if [ -f ${SYSTEMD_DIR}/${OTHER_SERVICE} ]; then
                NETWORK=$(grep "Description=" "${SYSTEMD_DIR}/${OTHER_SERVICE}" | grep -oEi "(MAINNET|HOLESKY|SEPOLIA|HOODI|EPHEMERY)" | head -1)
            fi
        fi
    fi

    NETWORK_ARG=""
    if [ "$NETWORK" != "Network Syncing" ] && [ -n "$NETWORK" ]; then
        NETWORK_ARG="--network $NETWORK"
    fi

    # 4) Stop the service before installing the new one
    sudo systemctl stop ${TARGET_CLIENT} > /dev/null 2>&1

    # 5) Launch the python script to select new client and install
    if [ "$TARGET_CLIENT" == "execution" ]; then
        runScript deploy/install-node.sh deploy/deploy-node.py --switch_client execution --cc "$CL" $NETWORK_ARG
    elif [ "$TARGET_CLIENT" == "consensus" ]; then
        runScript deploy/install-node.sh deploy/deploy-node.py --switch_client consensus --ec "$EL" $NETWORK_ARG
    fi
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    getClient
    switchClient $1
fi
