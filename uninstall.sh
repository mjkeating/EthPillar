#!/bin/bash

# Author: coincashew.eth | coincashew.com
# License: GNU GPL
# Source: https://github.com/coincashew
#
# Made for home and solo stakers 🏠🥩

# uninstall.sh is executed as its own process (runScript); resolve repo root.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_DIR="${BASE_DIR:-$SCRIPT_DIR}"

# Retire a systemd unit via the shared helper (stop+disable+*.service.bak+
# daemon-reload+verify absent). Prefer this over `rm` so units cannot
# accidentally restart via boot or Wants=.
retireSystemdUnit(){
	local unit="${1:?unit name required}"
	local unit_dir="${SYSTEMD_UNIT_DIR:-/etc/systemd/system}"
	[[ -f "${unit_dir}/${unit}.service" ]] || return 0
	if ! PYTHONPATH="${BASE_DIR}" python3 -m deploy.systemd_units retire "${unit}.service" --unit-dir "${unit_dir}"; then
		echo "ERROR: Failed to retire ${unit}.service (refusing to continue uninstall)" >&2
		exit 1
	fi
}

function promptYesNo(){
    if whiptail --title "Uninstall Staking Node" --defaultno --yesno "This will remove all data and files related to this staking node.\nAre you sure you want to remove all files?\n(consensus/execution/validator/charon/ssv/mevboost)" 9 78; then
  		uninstallCL
  		uninstallEL
  		uninstallCharon
  		uninstallWeb3Signer
  		uninstallSsv
  		uninstallVC
  		uninstallMevboost
		cleanupMisc
		uninstallPlugins
  		whiptail --title "Uninstall finished" --msgbox "You have uninstalled this staking node and all validator keys." 8 78
	else
		echo "Cancelled uninstall." && return 1
	fi
}

function uninstallPlugins(){
	if [[ -d /opt/ethpillar/plugin-csm ]]; then
		retireSystemdUnit csm_nimbusvalidator
		sudo userdel csm_nimbus_validator 2>/dev/null || true
		sudo rm -rf /opt/ethpillar/plugin-csm
	fi
	if [[ -d /opt/ethpillar/plugin-sentinel ]]; then
		sudo docker stop csm-sentinel
		sudo docker rm csm-sentinel
		sudo docker rmi csm-sentinel
		sudo docker volume rm csm-sentinel-persistent
		sudo rm -rf /opt/ethpillar/plugin-sentinel
	fi
	if [[ -d /opt/ethpillar/plugin-dora ]]; then
		retireSystemdUnit dora
		sudo userdel dora 2>/dev/null || true
		sudo rm -rf /opt/ethpillar/plugin-dora
	fi
	if [[ -d /opt/ethpillar/plugin-client-stats ]]; then
		retireSystemdUnit client-stats
		sudo userdel client-stats 2>/dev/null || true
		sudo rm -rf /opt/ethpillar/plugin-client-stats
	fi
	if [[ -d /opt/ethpillar/plugin-contributoor ]]; then
		retireSystemdUnit contributoor
		sudo userdel contributoor 2>/dev/null || true
		sudo rm -rf /opt/ethpillar/plugin-contributoor
	fi
	if [[ -d /opt/ethpillar/aztec ]]; then
	    cd /opt/ethpillar/aztec 2>/dev/null && docker compose down || true
	    sudo docker rm -f aztec-sequencer
	    TAG=$(grep "DOCKER_TAG" /opt/ethpillar/aztec/.env | sed "s/^DOCKER_TAG=\(.*\)/\1/")
	    sudo docker rmi -f aztecprotocol/aztec:"$TAG"
	    if [[ -f /opt/ethpillar/aztec/.cast_installed_by_plugin && -f /usr/local/bin/cast ]]; then
	      sudo rm /usr/local/bin/cast
	    fi
	    sudo rm -rf /opt/ethpillar/aztec
	fi
}

function cleanupMisc(){
	if [[ -f /etc/systemd/system/ethereum-metrics-exporter.service ]]; then
	   sudo rm /etc/apt/sources.list.d/grafana.list
	   retireSystemdUnit ethereum-metrics-exporter
	   sudo rm -f /usr/local/bin/ethereum-metrics-exporter
	   sudo systemctl disable grafana-server prometheus prometheus-node-exporter 2>/dev/null || true
	   sudo systemctl stop grafana-server prometheus prometheus-node-exporter 2>/dev/null || true
	   sudo apt remove -y grafana prometheus prometheus-node-exporter
	fi
	if [[ -f /usr/local/bin/eth-duties ]]; then sudo rm /usr/local/bin/eth-duties; fi
	if [[ -f /usr/local/bin/ethdo ]]; then sudo rm /usr/local/bin/ethdo; fi
	if [[ -f "$BASE_DIR/.env.overrides" ]]; then sudo rm -f "$BASE_DIR/.env.overrides"; fi
	if [[ -d /opt/ethpillar/testnet ]]; then sudo rm -rf /opt/ethpillar/testnet; fi
	if [[ -d /opt/ethpillar/patches ]]; then sudo rm -rf /opt/ethpillar/patches; fi
}

function uninstallCL(){
	if [[ -f /etc/systemd/system/consensus.service ]]; then
		retireSystemdUnit consensus

		#Lighthouse
		sudo rm -rf /usr/local/bin/lighthouse
		sudo rm -rf /var/lib/lighthouse

		#Lodestar
		sudo rm -rf /usr/local/bin/lodestar
		sudo rm -rf /var/lib/lodestar

		#Teku
		sudo rm -rf /usr/local/bin/teku
		sudo rm -rf /var/lib/teku

		#Nimbus
		sudo rm -rf /usr/local/bin/nimbus_beacon_node
		sudo rm -rf /var/lib/nimbus

		#Prysm from Binaries
		sudo rm -rf /usr/local/bin/prysm-beacon-chain
		#Prysm from Build from Source
		sudo rm -rf /usr/local/bin/prysm
		sudo rm -rf /var/lib/prysm

		#Grandine
		sudo rm -rf /usr/local/bin/grandine
		sudo rm -rf /var/lib/grandine

		sudo userdel consensus 2>/dev/null || true
	fi
}

function uninstallEL(){
	if [[ -f /etc/systemd/system/execution.service ]]; then
		retireSystemdUnit execution

		#Nethermind
		sudo rm -rf /usr/local/bin/nethermind
		sudo rm -rf /var/lib/nethermind

		#Besu
		sudo rm -rf /usr/local/bin/besu
		sudo rm -rf /var/lib/besu

		#Geth
		sudo rm -rf /usr/local/bin/geth
		sudo rm -rf /var/lib/geth

		#Erigon
		sudo rm -rf /usr/local/bin/erigon
		sudo rm -rf /var/lib/erigon

		#Reth
		sudo rm -rf /usr/local/bin/reth
		sudo rm -rf /var/lib/reth

		#Ethrex
		sudo rm -rf /usr/local/bin/ethrex
		sudo rm -rf /var/lib/ethrex

		sudo userdel execution 2>/dev/null || true
	fi
}

function uninstallWeb3Signer(){
	if [[ -f /etc/systemd/system/web3signer.service ]]; then
		retireSystemdUnit web3signer
		sudo rm -rf /usr/local/bin/web3signer
		sudo rm -rf /var/lib/web3signer
		sudo userdel web3signer 2>/dev/null || true
	fi
}

function uninstallCharon(){
	if [[ -f /etc/systemd/system/charon.service ]]; then
		retireSystemdUnit charon
		sudo rm -f /usr/local/bin/charon
		sudo rm -rf /var/lib/charon
		sudo userdel charon 2>/dev/null || true
	fi
}

# Copy SSV operator secrets/config (and optionally db/) to a root-only
# timestamped directory outside the live datadir. Prints the path on success.
# Args: include_db (0|1). Returns non-zero if the backup cannot be written.
backupSsvDatadir(){
	local include_db="${1:-0}"
	local src="${SSV_DATA_DIR:-/var/lib/ssv}"
	local parent="${SSV_BACKUP_PARENT:-${HOME:-/root}}"
	[[ -z "$parent" || "$parent" == "/" ]] && parent="/root"
	local ts
	ts="$(date +%Y%m%d-%H%M%S)"
	local dest="${parent}/ssv-backup-${ts}"
	local f
	local copied=0

	# /var/lib/ssv is typically 700 ssv:ssv — every probe/copy must use sudo.
	if ! sudo test -d "$src"; then
		echo "ERROR: SSV datadir missing ($src); nothing to back up" >&2
		return 1
	fi
	if ! sudo mkdir -p "$dest"; then
		echo "ERROR: Failed to create SSV backup directory $dest" >&2
		return 1
	fi
	# Mode is mandatory; root ownership is best-effort (sudo normally succeeds).
	if ! sudo chmod 700 "$dest"; then
		echo "ERROR: Failed to lock down SSV backup directory $dest" >&2
		sudo rm -rf "$dest" 2>/dev/null || true
		return 1
	fi
	sudo chown root:root "$dest" 2>/dev/null || true

	for f in encrypted_private_key.json password config.yaml operator.yaml; do
		if sudo test -e "${src}/${f}"; then
			if ! sudo cp -a "${src}/${f}" "${dest}/${f}"; then
				echo "ERROR: Failed to back up ${src}/${f}" >&2
				sudo rm -rf "$dest" 2>/dev/null || true
				return 1
			fi
			sudo chmod 600 "${dest}/${f}" 2>/dev/null || true
			sudo chown root:root "${dest}/${f}" 2>/dev/null || true
			copied=1
		fi
	done

	if [[ "$include_db" == "1" ]] && sudo test -d "${src}/db"; then
		if ! sudo cp -a "${src}/db" "${dest}/db"; then
			echo "ERROR: Failed to back up ${src}/db" >&2
			sudo rm -rf "$dest" 2>/dev/null || true
			return 1
		fi
		sudo chmod -R u+rwX,go-rwx "${dest}/db" 2>/dev/null || true
		sudo chown -R root:root "${dest}/db" 2>/dev/null || true
		copied=1
	fi

	# Fail closed: never report success without the operator keystore.
	if [[ "$copied" -eq 0 ]] || ! sudo test -e "${dest}/encrypted_private_key.json"; then
		echo "ERROR: SSV backup missing operator keystore (nothing copied from ${src}?)" >&2
		sudo rm -rf "$dest" 2>/dev/null || true
		return 1
	fi

	# Path on stdout for callers; human-readable notice on stderr.
	echo "SSV backup written to: ${dest}" >&2
	printf '%s\n' "${dest}"
}

function uninstallSsv(){
	local ssv_data="${SSV_DATA_DIR:-/var/lib/ssv}"
	local unit_dir="${SYSTEMD_UNIT_DIR:-/etc/systemd/system}"
	local bin_dir="${INSTALL_DIR:-/usr/local/bin}"
	local include_db=0
	local backup_path=""

	# Units are always retired (stop/disable/.bak/verify), independent of wipe.
	if [[ -f "${unit_dir}/ssv-dkg.service" ]]; then
		retireSystemdUnit ssv-dkg
	fi
	if [[ -f "${unit_dir}/ssv-node.service" ]]; then
		retireSystemdUnit ssv-node
	fi

	# Datadir is often 700 ssv:ssv — use sudo for existence probes.
	if sudo test -d "$ssv_data"; then
		# Strong confirm; default No — keep operator secrets/DB unless explicitly wiped.
		if whiptail --title "Wipe SSV datadir?" --defaultno --yesno \
			"Permanently DELETE ${ssv_data}?\n\nThis destroys the operator keystore, password, configs, DKG data, and DB (slashing protection).\n\nUnits are already being retired. Default: No (keep datadir)." 14 78; then
			# Backup prompt first (default Yes) before any destructive wipe.
			if whiptail --title "Back up SSV before wipe?" --yesno \
				"Back up operator key material and config before wiping ${ssv_data}?\n\nAlways included when present:\n  • encrypted_private_key.json\n  • password\n  • config.yaml\n  • operator.yaml (DKG)\n\nYou will next choose whether to include db/.\n\nDefault: Yes" 18 78; then
				if sudo test -d "${ssv_data}/db"; then
					if whiptail --title "Include SSV DB in backup?" --yesno \
						"Include ${ssv_data}/db in the backup?\n(Slashing-critical. Default: Yes)" 10 78; then
						include_db=1
					fi
				fi
				if ! backup_path="$(backupSsvDatadir "$include_db")"; then
					whiptail --title "SSV backup failed" --msgbox \
						"Backup failed. Aborting wipe of ${ssv_data}.\n\nUnits were already retired; binaries will still be removed.\nDatadir left intact." 12 78
					echo "ERROR: SSV backup failed; refusing to wipe ${ssv_data}" >&2
				elif [[ -z "$backup_path" ]] || ! sudo test -d "$backup_path"; then
					whiptail --title "SSV backup failed" --msgbox \
						"Backup path missing after backupSsvDatadir. Aborting wipe of ${ssv_data}." 10 78
					echo "ERROR: SSV backup path invalid; refusing to wipe ${ssv_data}" >&2
				elif ! sudo test -e "${backup_path}/encrypted_private_key.json"; then
					whiptail --title "SSV backup failed" --msgbox \
						"Backup is missing the operator keystore. Aborting wipe of ${ssv_data}." 10 78
					echo "ERROR: SSV backup missing keystore; refusing to wipe ${ssv_data}" >&2
				else
					whiptail --title "SSV backup complete" --msgbox \
						"SSV backup saved (root-only) to:\n\n${backup_path}" 11 78
					echo "Wiping ${ssv_data}"
					sudo rm -rf "$ssv_data"
				fi
			else
				echo "Wiping ${ssv_data} without backup (user declined backup)"
				sudo rm -rf "$ssv_data"
			fi
		else
			echo "Keeping ${ssv_data} (wipe declined)"
		fi
	fi

	sudo rm -f "${bin_dir}/ssvnode" "${bin_dir}/ssv-dkg"
	sudo userdel ssv 2>/dev/null || true
}

function uninstallVC(){
	if [[ -f /etc/systemd/system/validator.service ]]; then
		retireSystemdUnit validator

		#Lighthouse
		sudo rm -rf /var/lib/lighthouse/validators
		sudo rm -rf /var/lib/lighthouse_validator

		#Lodestar
		sudo rm -rf /var/lib/lodestar/validators
		sudo rm -rf /var/lib/lodestar_validator

		#Teku, if running Standalone Teku Validator
		sudo rm -rf /var/lib/teku_validator

		#Nimbus, if running standalone Nimbus Validator
		sudo rm -rf /var/lib/nimbus_validator
		sudo rm -rf /usr/local/bin/nimbus_validator_client

		#Prysm from Binaries
		sudo rm -rf /usr/local/bin/prysm-validator
		sudo rm -rf /var/lib/prysm_validator
		sudo rm -rf /var/lib/prysm/validators

		sudo userdel validator 2>/dev/null || true
	fi
}

function uninstallMevboost(){
	if [[ -f /etc/systemd/system/mevboost.service ]]; then
		retireSystemdUnit mevboost
		sudo rm /usr/local/bin/mev-boost
		sudo userdel mevboost 2>/dev/null || true
	fi
}

# Interactive entry when executed directly (not when sourced by tests).
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
	promptYesNo
fi
