#!/bin/bash

# Author: coincashew.eth | coincashew.com
# License: GNU GPL
# Source: https://github.com/coincashew
#
# Made for home and solo stakers 🏠🥩

BASE_DIR=$(pwd)
source $BASE_DIR/functions.sh

function getClient(){
    EL=$(cat /etc/systemd/system/execution.service | grep Description= | awk -F'=' '{print $2}' | awk '{print $1}')
}

function promptYesNo(){
	case $EL in
	  Nethermind)
		MSG="This will take a few hours."
	    ;;
	  *)
		MSG="This will about a day or so."
	    ;;
	  esac
    if whiptail --title "Resync Execution - $EL" --yesno "$MSG\nConsider configuring a RescueNode to minimize downtime.\nAre you sure you want to resync $EL?" 10 78; then
  		resyncClient
  		promptViewLogs
	fi
}

function promptViewLogs(){
    if whiptail --title "Resync $EL started" --yesno "Would you like to view logs and confirm everything is running properly?" 8 78; then
		sudo bash -c 'journalctl -fu execution | ccze -A'
    fi
}

function resyncClient(){
	clear
	case $EL in
	  Nethermind)
		sudo systemctl stop execution
		sudo rm -rf /var/lib/nethermind/*
		sudo systemctl restart execution
	    ;;
	  Besu)
		sudo systemctl stop execution
		sudo rm -rf /var/lib/besu/*
		sudo systemctl restart execution
	    ;;
	  Geth)
		sudo systemctl stop execution
		sudo rm -rf /var/lib/geth/*
		sudo systemctl restart execution
		;;
	  Erigon)
		sudo systemctl stop execution
		sudo rm -rf /var/lib/erigon/*
		sudo systemctl restart execution
	    ;;
  	  Reth)
		getNetwork
		getExecutionDatadir
		getExecutionStaticFiles

		_datadir=${DATADIR:-/var/lib/reth}
		_static_files_arg=""
		
		if [ -n "$STATIC_FILES" ]; then
		    _static_files_arg="--datadir.static-files=$STATIC_FILES"
		fi

		case $NETWORK in
		  Holesky)   _chain="holesky" ;;
		  Hoodi)     _chain="hoodi" ;;
		  Sepolia)   _chain="sepolia" ;;
		  *)         _chain="mainnet" ;;
		esac
		sudo systemctl stop execution
		sudo rm -rf $_datadir/*
		if [ -n "$STATIC_FILES" ]; then
		    sudo rm -rf $STATIC_FILES/*
		fi

		# Reth doesn't serve official snapshots for other chains without a custom provider URL
		if [ "$_chain" == "mainnet" ]; then
			read -r -d '' _prompt_msg <<-'EOF' || true
			Would you like to perform a snapshot download?
			This is much faster than a standard P2P full sync.

			Note: You will enter an interactive menu to choose download components.
			EOF

			if whiptail --title "Reth Snapshot Download" --yesno "$_prompt_msg" 12 78; then
				sudo reth download --chain="$_chain" --datadir=$_datadir $_static_files_arg    # enters the 'reth download' TUI, allowing user to select snapshot components to download
				sudo chown -R execution:execution $_datadir
				if [ -n "$STATIC_FILES" ]; then
				    sudo chown -R execution:execution $STATIC_FILES
				fi
			fi
		fi

		sudo systemctl restart execution
	    ;;
	  esac
}

getClient
promptYesNo