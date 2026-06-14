#!/bin/bash

# Author: coincashew.eth | coincashew.com
# License: GNU GPL
# Source: https://github.com/coincashew/ethpillar
#
# Made for home and solo stakers 🏠🥩

BASE_DIR="${BASE_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"

# Colors
g="\033[32m" # Green
r="\033[31m" # Red
nc="\033[0m" # No-color
bold="\033[1m"

function info {
  echo -e "${g}INFO: $1${nc}"
}

function error {
  echo -e "${r}${bold}ERROR: $1${nc}"
}

# Location of plugin files
PLUGIN_INSTALL_PATH=/opt/ethpillar/aztec

# Get current version
# shellcheck disable=SC1091
[[ -f $PLUGIN_INSTALL_PATH/current_version ]] && VERSION=$(cat $PLUGIN_INSTALL_PATH/current_version)

# Update Disk Usage
DISK_USAGE=$(du -sh "$PLUGIN_INSTALL_PATH" | awk '{print $1}')
[[ -n $DISK_USAGE ]] || error "Unable to get disk usage."

function set_public_ip() {
    # sanitize with grep
    P2P_IP=$(grep -m 1 -oE '^[0-9]{1,3}(\.[0-9]{1,3}){3}$' <<< "$(wget -T 10 -t 1 -4qO- "ipv4.icanhazip.com" || curl -m 10 -4Ls "http://ip1.dynupdate.no-ip.com")")
    # shellcheck disable=SC2015
    [[ -n $P2P_IP ]] && sudo sed -i "s/^P2P_IP.*$/P2P_IP=${P2P_IP}/" $PLUGIN_INSTALL_PATH/.env || error "Unable to set P2P_IP. Update .env manually."
}

function startCommand(){
    #set_public_ip
    docker compose --env-file "$PLUGIN_INSTALL_PATH"/.env up -d || error "Error starting command"
}

function buildMenuText(){
    # shellcheck disable=SC1091
    MENUTEXT="\nChoose one of the following options:"
}

function healthChecks(){
    function rpcStatus(){
      local rpc_remote=https://aztec-alpha-testnet-fullnode.zkv.xyz
      local rpc_local=http://localhost:8080
      local remote_execution_l1_rpc=https://ethereum-sepolia-rpc.publicnode.com
      local remote_consensus_beacon_rpc=https://ethereum-sepolia-beacon-api.publicnode.com
      local consensus_beacon_rpc execution_l1_rpc remote_block local_block peerid enr latest_block_number latest_slot

      # Load RPC URLs from .env
      consensus_beacon_rpc=$(grep ^L1_CONSENSUS_HOST_URLS "$PLUGIN_INSTALL_PATH"/.env | sed 's/L1_CONSENSUS_HOST_URLS=//g') #http://localhost:5052
      execution_l1_rpc=$(grep ^ETHEREUM_HOSTS "$PLUGIN_INSTALL_PATH"/.env | sed 's/ETHEREUM_HOSTS=//g') #http://localhost:8545

      # Remap http://host.docker.internal to localhost
      [[ $consensus_beacon_rpc == "http://host.docker.internal:5052" ]] && consensus_beacon_rpc="http://localhost:5052"
      [[ $execution_l1_rpc == "http://host.docker.internal:8545" ]] && execution_l1_rpc="http://localhost:8545"

      # If there's a list of comma separated rpc nodes, use the first node
      consensus_beacon_rpc=${consensus_beacon_rpc%%,*}
      execution_l1_rpc=${execution_l1_rpc%%,*}

      # Check RPC node availablity
      echo -e "${bold}\n🔎 Checking Sepolia Execution L1 RPC: $execution_l1_rpc${nc}"
      if curl -s --connect-timeout 5 --max-time 10 "$execution_l1_rpc" >/dev/null 2>&1; then echo "✅ Sepolia Execution L1 RPC is up"; else echo "❌ Sepolia Execution L1 RPC is down or unreachable"; fi

      echo -e "${bold}\n🔎 Checking Consensus Beacon Node RPC: $consensus_beacon_rpc${nc}"
      if curl -s --connect-timeout 5 --max-time 10 "$consensus_beacon_rpc" >/dev/null 2>&1; then echo "✅ Consensus Beacon Node RPC is up"; else echo "❌ Consensus Beacon Node RPC is down or unreachable"; fi

      # Check execution latest blocks
      latest_block_number=$(curl -s --connect-timeout 3 --max-time 5 --fail -X POST -H "Content-Type: application/json" --data '{"jsonrpc":"2.0","method":"eth_blockNumber","params":[],"id":1}' "${execution_l1_rpc}" | jq -r '.result' || error "Unable to query latest block")
      if [[ -n "$latest_block_number" && "$latest_block_number" != "0x0" ]]; then latest_block_number=$(printf '%d' "$latest_block_number"); else latest_block_number="N/A"; fi
      latest_remote_block=$(curl -s --connect-timeout 3 --max-time 5 --fail -X POST -H "Content-Type: application/json" --data '{"jsonrpc":"2.0","method":"eth_blockNumber","params":[],"id":1}' "${remote_execution_l1_rpc}" | jq -r '.result' || error "Unable to query latest block")
      if [[ -n "$latest_remote_block" && "$latest_remote_block" != "0x0" ]]; then latest_remote_block=$(printf '%d' "$latest_remote_block"); else latest_remote_block="N/A"; fi

      # Check consensus client latest slot
      latest_slot=$(curl -s --connect-timeout 3 --max-time 5 --fail -X GET "${consensus_beacon_rpc}/eth/v1/node/syncing" -H "accept: application/json" | jq -r '.data.head_slot' || error "Unable to query latest slot")
      [[ -n "$latest_slot" ]] || latest_slot="N/A"
      latest_remote_slot=$(curl -s --connect-timeout 3 --max-time 5 --fail -X GET "${remote_consensus_beacon_rpc}/eth/v1/node/syncing" -H "accept: application/json" | jq -r '.data.head_slot' || error "Unable to query latest slot")
      [[ -n "$latest_remote_slot" ]] || latest_remote_slot="N/A"

      echo -e "${bold}\n🔗 Ethereum Execution Client status:${nc}"
      echo "   🌍 Remote block:  $latest_remote_block [$remote_execution_l1_rpc]"
      echo "   🧱 Local block:   $latest_block_number [$execution_l1_rpc]"
      exec_pct="N/A"
      [[ $latest_remote_block != "N/A" && $latest_block_number != "N/A" ]] && exec_pct=$(echo "scale=2; $latest_block_number * 100 / $latest_remote_block" | bc -l 2>/dev/null || error "Unable to calculate %")
      if [[ "$exec_pct" != "N/A" ]]; then
        echo "   📈 Progress: ${exec_pct}%"
        [[ "$exec_pct" == "100.00" ]] && echo "   ✅ Execution is synced." || echo "   ❌ Execution is not synced."
      else
        echo "   📈 Progress: N/A"
        echo "   ℹ️ Unable to determine sync status."
      fi

      echo -e "${bold}\n🧠 Beacon Consensus Client Status:${nc}"
      echo "   🌍 Remote slot:  $latest_remote_slot [$remote_consensus_beacon_rpc]"
      echo "   🧱 Local slot:   $latest_slot [$consensus_beacon_rpc]"
      beacon_pct="N/A"
      [[ $latest_remote_slot != "N/A" && $latest_slot != "N/A" ]] && beacon_pct=$(echo "scale=2; $latest_slot * 100 / $latest_remote_slot" | bc -l 2>/dev/null || error "Unable to calculate %")
      if [[ "$beacon_pct" != "N/A" ]]; then
        echo "   📈 Progress: ${beacon_pct}%"
        [[ "$beacon_pct" == "100.00" ]] && echo "   ✅ Beacon is synced." || echo "   ❌ Beacon is not synced." 
      else
        echo "   📈 Progress: N/A"
        echo "   ℹ️ Unable to determine sync status."
      fi

      # Check block height of remote and local node
      remote_block=$(curl -s --connect-timeout 3 --max-time 5 --fail -X POST -H 'Content-Type: application/json' -d '{"jsonrpc":"2.0","method":"node_getL2Tips","params":[],"id":67}' "$rpc_remote")
      local_block=$(curl -s --connect-timeout 3 --max-time 5 --fail -X POST -H 'Content-Type: application/json' -d '{"jsonrpc":"2.0","method":"node_getL2Tips","params":[],"id":67}' "$rpc_local")

      if [[ -z "$remote_block" || "$remote_block" == "null" ]]; then
        remote_block="N/A"
      else
        remote_block=$(echo "$remote_block" | jq -r ".result.proven.number")
      fi

      if [[ -z "$local_block" || "$local_block" == "null" ]]; then
        local_block="N/A"
      else
        local_block=$(echo "$local_block" | jq -r ".result.proven.number")
      fi

      echo -e "${bold}\n🖥️ Aztec L2 Node Sync Status:${nc}"
      echo "   🌍 Remote block: $remote_block [$rpc_remote]"
      echo "   🧱 Local block:  $local_block [$rpc_local]"
      aztec_pct="N/A"
      [[ $remote_block != "N/A" && $local_block != "N/A" ]] && aztec_pct=$(echo "scale=2; $local_block * 100 / $remote_block" | bc -l 2>/dev/null || error "Unable to calculate %")
      if [[ "$aztec_pct" != "N/A" ]]; then
        echo "   📈 Progress: ${aztec_pct}%"
        [[ "$aztec_pct" == "100.00" ]] && echo "   ✅ Aztec node is synced." || echo "   ❌ Aztec node is not synced."
      else
        echo "   📈 Progress: N/A"
        echo "   ℹ️ Unable to determine sync status."
      fi

      # Check status endpoint
      echo -e "${bold}\n▶️ Aztec Status Endpoint (http://localhost:8080/status):${nc}"
      echo "   ℹ️ Status: $(curl -s http://localhost:8080/status)"

      # Check sync proof
      proof=$(curl -s --connect-timeout 3 --max-time 5 --fail -X POST -H 'Content-Type: application/json' -d "{\"jsonrpc\":\"2.0\",\"method\":\"node_getArchiveSiblingPath\",\"params\":[\"$local_block\",\"$local_block\"],\"id\":67}" "$rpc_local" | jq -r .result)
      echo -e "${bold}\n⏳ Sync Proof: $proof${nc}"
    }

    function dockerChecks(){
      # Check if we have a Peer ID
      peerid=$(docker logs aztec-sequencer 2>&1 | grep --max-count 1 "peerId" | sed 's/.*"peerId":"\([^"]*\)".*/\1/')
      echo -e "${bold}\n📋 Peer ID: Verify at https://aztec.nethermind.io/explore${nc}"
      [[ -n $peerid ]] && echo "   ✅ $peerid" || echo "   ❌ Unable to get Peer ID. Is node running?"

      # Check how many peers are connected
      peercount=$(docker logs aztec-sequencer -n 100 2>&1 | grep 'peer_manager' | tail -n1 | sed "s/.*Connected to \([0-9]*\).*/\1/")
      echo -e "${bold}\n👥 Peer count:"
      [[ -n $peercount ]] && echo "   ✅ $peercount peers connected" || echo "   ❌ Unable to get # of connected peers. Is node running?"

      # Check if we have an ENR
      enr=$(docker logs aztec-sequencer 2>&1 | grep --max-count 1 "enrTcp" | sed 's/.*"enrTcp":"\([^"]*\)".*/\1/')
      echo -e "${bold}\n⚙️ ENR:${nc}"
      [[ -n $enr ]] && echo "   ✅ $enr" || echo "   ❌ Unable to get ENR. Is node running?"

      # Check docker processes
      echo -e "${bold}\n🔎 Docker Process Running:${nc}"
      docker compose -f "$PLUGIN_INSTALL_PATH"/docker-compose.yml ps || error "Unable to list docker ps"

      # Check docker is in ROOTLESS mode
      echo -e "${bold}\n🧱 ROOTLESS Docker Mode:${nc}"
      if docker info 2>&1 | grep -q "rootless"; then
          echo -e "${g}   ✅ ROOTLESS Docker mode is active${nc}"
      else
          echo -e "${r}   ⚠️ Container is running as root. Re-install Docker with non-root user.${nc}"
      fi
    }

    function openPorts(){
      # Check for open ports
      open_ports=0
      tcp_ports="40400"
      udp_ports="40400"

      # Check TCP ports
      checker_url="https://eth2-client-port-checker.vercel.app/api/checker?ports="
      tcp_check_ok=false
      tcp_json=$(curl -s --connect-timeout 3 --max-time 5 --fail "${checker_url}${tcp_ports}") || true

      # Check UDP ports using netcat
      udp_open_ports=0
      open_udp_ports=()
      for port in $(echo "$udp_ports" | tr ',' ' '); do
          if nc -z -u localhost "$port" &>/dev/null; then
              ((udp_open_ports++))
              open_udp_ports+=("$port")
              tcp_check_ok=true
          fi
      done

      # Parse JSON using jq and check if any open ports exist
      echo -e "${bold}\n🔎 Open ports found:${nc}"
      if echo "$tcp_json" | jq -e '.open_ports[]' > /dev/null 2>&1; then
          echo "$tcp_json" | jq -r '.open_ports[]' | while read -r port; do echo "$port(TCP)"; done
          tcp_open_ports=$(echo "$tcp_json" | jq '.open_ports | length')
          open_ports=$((tcp_open_ports + udp_open_ports))
      else
        echo "   ℹ️ Remote TCP port checker unavailable; skipping remote reachability."
      fi

      # Show UDP ports
      for port in "${open_udp_ports[@]}"; do
          echo "$port(UDP)"
      done

      # Compare expected vs actual number of open ports
      expected_tcp_ports=$(echo "$tcp_ports" | tr ',' '\n' | wc -l)
      expected_udp_ports=$(echo "$udp_ports" | tr ',' '\n' | wc -l)
      include_tcp=0
      if echo "$tcp_json" | jq -e '.open_ports | length > 0' >/dev/null 2>&1; then
          include_tcp=1
      fi
      expected_ports=$(( (include_tcp == 1 ? expected_tcp_ports : 0) + expected_udp_ports ))

      if [ "$expected_ports" -ne "$open_ports" ]; then
          echo -e "${r}   ❌ Ports ${tcp_ports} (TCP) and ${udp_ports} (UDP) not all open or reachable. Expected ${expected_ports}. Actual $open_ports. Check firewall or port forwarding on router.${nc}"
      else
          echo -e "${g}   ✅ P2P Ports fully open on ${tcp_ports} (TCP) and ${udp_ports} (UDP)${nc}"
      fi
    }

    function check_firewall() {
      echo -e "${bold}\n🔥🧱 UFW Firewall Status:${nc}"
      if sudo ufw status | grep -q "Status: active"; then
          sudo ufw status numbered
          echo -e "${g}   ✅ Firewall is active${nc}"
      else
          echo -e "${r}   ❌ Firewall is not active. Install found in Security & Node Checks.${nc}"
      fi
    }

    function show_status() {
      UPTIME=$(uptime -p)
      CONTAINER_STARTED_AT=$(docker inspect -f '{{.State.StartedAt}}' aztec-sequencer 2>/dev/null | cut -d'.' -f1)
      START_FORMAT=$(test -n "$CONTAINER_STARTED_AT" && date -d "$CONTAINER_STARTED_AT" +"%Y-%m-%d %H:%M:%S" 2>/dev/null || echo "N/A")
      CPU=$(LANG=C top -bn1 | grep "Cpu(s)" | awk '{print $2+$4}')
      MEM=$(free -m | awk 'NR==2{printf "%.2f GiB",$3/1024}')
      echo -e "${bold}\n🖥️ Node Status:${nc}"
      echo -e "${bold}   ⌚ Uptime:      ${g}$UPTIME${nc}"
      echo -e "${bold}   ⚡ Started:     ${g}$START_FORMAT${nc}"
      echo -e "${bold}   🚀 CPU:         ${r}$CPU%${nc}"
      echo -e "${bold}   💾 Memory:      ${g}$MEM${nc}"
      if df -h / &> /dev/null; then
          FREE_GB=$(df -h / | awk 'NR==2 {print $4}')
          USED_PERCENT=$(df -h / | awk 'NR==2 {print $5}' | sed 's/%//')
          if [ "$USED_PERCENT" -ge 90 ]; then
              echo -e "${bold}   💽 Disk Space:${nc}${r} ⚠️ / has only $((100 - USED_PERCENT))% free space left ($FREE_GB).${nc}"
          else
              echo -e "${bold}   💽 Disk Space:${nc}${g} ✅ / has sufficient free space: $FREE_GB ($((100 - USED_PERCENT))% free).${nc}"
          fi
      fi
    }

    echo -e "\n${g}${bold}=== Health Checks Begin: This will take a few seconds... ===${nc}"
    rpcStatus
    dockerChecks
    openPorts
    check_firewall
    show_status
    echo -e "\n${g}${bold}=== Health Checks Complete: Press enter to exit ===${nc}"
    read -r
}

function registerValidatorSepolia(){
    # shellcheck disable=SC1091
    source "$PLUGIN_INSTALL_PATH"/.env
    local MSG="⚠️ Ensure your Aztec node is fully synced before proceeding.
⚠️ Ensure your VALIDATOR_ADDRESS (.env file) has at least 0.01 sepoliaETH.
\nTo register your validator:
\n1) Visit https://testnet.aztec.network/add-validator
\n2) Complete ZKPassport humanity verification.
\n3) Connect your validator wallet [$VALIDATOR_ADDRESS]
\n4) Register on the network (follow the instructions).
\n5) You'll join the registration queue and receive the Explorer Discord role.
\n6) Verify your verifying validator status at:\nhttps://testnet.aztecscan.xyz/l1/validators/${VALIDATOR_ADDRESS}"

  # Register screen
  whiptail --title "Register Validator Information" --msgbox "$MSG" 24 83
}

function claimGuardianRole(){
    local MSG="Running a Sequencer Node with good uptime allows you to claim the Guardian role. Follow these steps:
\n1) Access the Upgrade-Role Channel: Navigate to the dedicated channel for role upgrades. https://discord.gg/aztec
\n2) Check Your Eligibility: Type /checkip in the channel and provide your IP address and node address when prompted.
\n3) Claim the Role: If you meet the requirements, you will be eligible to claim the Guardian role. Instructions on how to do so will be provided.
\n4) Next Snapshot: If you are not currently eligible, continue running your Sequencer Node with good uptime and wait for the next snapshot."

  # Register screen
  whiptail --title "Claiming the Guardian Role" --msgbox "$MSG" 22 78
}

function nextSteps(){
    local MSG="
  official 🌐:
    https://aztec.network/network

  wallet 👛:
    https://azguardwallet.io
    https://app.obsidion.xyz
  
  block explorer 👀:
    https://aztecscan.xyz
    https://aztecexplorer.xyz

  bridge 🌉:
    https://bridge.human.tech

  validator dashboard 📈:
    https://dashtec.xyz

  public RPCs ✨:
    https://node.testnet.azguardwallet.io
    https://aztec-alpha-testnet-fullnode.zkv.xyz
"    

  # Register screen
  whiptail --title "🔒 Everything is gmAztec" --msgbox "$MSG" 26 78
}

function registerValidator(){
  # Load values
  # shellcheck disable=SC1091
  source "$PLUGIN_INSTALL_PATH"/.env
  # If there's a list of comma separated nodes, use the first node
  ETHEREUM_HOSTS=${ETHEREUM_HOSTS%%,*}  

  local MSG="⚠️ Ensure your node is fully synced before proceeding.

Prerequisites for Smooth Registration:

Existing Validators:

- Hold 0.2–0.5 Sepolia ETH in your new validator address for gas fees.
- Ensure your previous wallet also has some Sepolia ETH.
- Have access to your old sequencer private key.

New Validators:

 - Contact the team with your EVM address to receive the required 200,000 STK.
 - You need 0.2–0.5 Sepolia ETH in your new validator address for gas fees."

  # Register screen
  whiptail --title "Register Validator Information" --msgbox "$MSG" 22 78
  # Confirmation to run command
  if whiptail --title "Register as Verifying Validator" --defaultno --yesno "I understand the prerequisites and have the requirements. Continue?" 9 78; then

    __SELECT=$(whiptail --title "🔧 Validator type" --menu \
          "$__MSG" 9 78 2 \
          "EXISTING" "| I have my old validator info" \
          "NEW     " "| I have 200k STK from the team" \
          3>&1 1>&2 2>&3)
    if [ -z "$__SELECT" ]; then exit; fi # pressed cancel

    if [[ -f /opt/ethpillar/aztec/keystore/key1.json ]]; then
      if ! whiptail --title "⚠️ You might have already registered" --defaultno --yesno "key1.json already exists.\n\n⚠️ BACKUP YOUR CURRENT KEYS FIRST!\n\nOverwrite and continue?" 11 78; then
        exit
      fi
    fi

    info "⚠️ ✏️ BE READY to write down your private key both ETH and BLS and your ETH address."
    read -p "   Press [Enter] to generate your new keys..."

    # Runs as root. Workaround for aztec issue: Due to how we containerize our applications, we require your working directory to be somewhere within /root.
    export __SELECT
    sudo bash -c "bash '$BASE_DIR/plugins/aztec/helper_root.sh'"

    whiptail --title "Registration complete" --msgbox "Verify your verifying validator status at:\n\nhttps://testnet.aztecscan.xyz/l1/validators/${VALIDATOR_ADDRESS}" 9 83
  fi
}

while true; do
    #get_disk_usage
    buildMenuText
    # Define the options for the submenu
    SUBOPTIONS=(
      🔍 "View Logs"
      🔧 "Edit .env configuration"
      📦 "Edit docker-compose.yml"
      🔒 "Edit keystore file"
      ✅ "Start"
      🛑 "Stop"
      🔄 "Restart"
      🛠️ "Update docker image"
      🗑️ "Uninstall plugin"
      - ""
      🛡️ "Troubleshooting: Run Health Checks"
      💻 "Claim Guardian Role: Run a Node with good uptime"
      🚀 "Register Validator: Become a Verifying Validator"
      🌐 "Next Steps: Useful Aztec Links"
      - ""
      👋 "Back to main menu"
    )

    # Display the submenu and get the user's choice
    SUBCHOICE=$(whiptail --clear --cancel-button "Back" \
      --backtitle "$BACKTITLE" \
      --title "🪿 Aztec-Sequencer $VERSION | Disk Use: $DISK_USAGE" \
      --menu "$MENUTEXT"\
      0 0 0 \
      "${SUBOPTIONS[@]}" \
      3>&1 1>&2 2>&3)

    # shellcheck disable=SC2181
    if [ $? -gt 0 ]; then # user pressed <Cancel> button
        break
    fi

    cd "$PLUGIN_INSTALL_PATH" || error "Unable to cd into install path"

    # Handle the user's choice from the submenu
    case $SUBCHOICE in
      🔍)
        docker compose logs -f --tail=233
        ;;
      🔧)
        sudo "${EDITOR}" "$PLUGIN_INSTALL_PATH"/.env
        ;;
      📦)
        sudo "${EDITOR}" "$PLUGIN_INSTALL_PATH"/docker-compose.yml
        ;;
      🔒)
        if [[ -f /opt/ethpillar/aztec/keystore/key1.json ]]; then
          sudo "${EDITOR}" /opt/ethpillar/aztec/keystore/key1.json
        else
          info "No key store. To create keystore, use Register Validator: Become a Verifying Validator"
          sleep 3
        fi
        ;;
      ✅)
        startCommand
        ;;
      🛑)
        docker compose stop
        ;;
      🔄)
        docker compose restart
        ;;
      🛠️)
        whiptail --msgbox "⚠️ Change the version tag found in the .env file on the following line\n\nDOCKER_TAG=$VERSION" 10 78
        sudo "${EDITOR}" "$PLUGIN_INSTALL_PATH"/.env
        TAG=$(grep "DOCKER_TAG" $PLUGIN_INSTALL_PATH/.env | sed "s/^DOCKER_TAG=\(.*\)/\1/")
        if [[ "$TAG" != "$VERSION" ]]; then
          if docker compose pull; then echo "$TAG" | sudo tee $PLUGIN_INSTALL_PATH/current_version; fi
          startCommand
        else
          whiptail --msgbox "No version changes were made. Staying on $VERSION" 8 78
        fi
        ;;
      🗑️)
        # shellcheck disable=SC2164
        exec "$BASE_DIR/plugins/aztec/plugin_aztec.sh" -r
        ;;
      🛡️)
        healthChecks
        ;; 
      💻)
        claimGuardianRole
        ;;
      🚀)
        registerValidator
        ;;
      🌐)
        nextSteps
        ;;
      👋)
        break
        ;;
    esac
done
