# EthPillar Deployment Flow

This document describes the orchestration logic for installing Ethereum nodes.

## Orchestration Flowchart

```mermaid
graph TD
    A[ethpillar.sh] -- "installNode() / Role Selection" --> B[deploy/install-node.sh]
    B -- "Forward Args" --> C[deploy/deploy-node.py]
    
    subgraph "Python Orchestrator (deploy/)"
    C -- "Query Configuration" --> D[orchestrator.py]
    D -- "Role Mapping / Flags" --> C
    D -- "Lido CSM Overrides" --> C
    D -- "Menu Data (EC/CC/VC)" --> C
    
    C -- "Execute Installation" --> E{Installation Logic}
    end

    E -- "Execution Client" --> F[ec_name.py]
    E -- "Consensus Client" --> G[cc_name.py]
    E -- "Validator Client" --> H[vc_name.py]
    E -- "MEV-Boost" --> I[mevboost.py]

    F & G & H & I -- "Generate Systemd" --> J[service_generators.py]
    J -- "Write Units" --> K[/etc/systemd/system/]

    K -- "Finalize Setup" --> L[common.py]
    L -- "finish_install()" --> M[Success / Logs]
```

## Setup Sequence

1.  **Network Selection**: (Mainnet, Holesky, Sepolia, etc.)
2.  **Role Selection**:
    *   **Solo Staking**: EC + CC + VC + MEV
    *   **CSM**: Solo Staking with Lido Overrides
    *   **Full Node**: EC + CC only
    *   **VC Only**: External BN + local VC
    *   **Custom**: Granular selection of all components
3.  **Client Selection**:
    *   If Custom: Pick EC, then CC, then VC.
    *   If Predefined: Pick from `PREDEFINED_COMBOS`.
4.  **Parameter Collection**: JWT, Fee Recipient, Graffiti, Sync URLs.
5.  **Execution**:
    *   `common.setup_node()`: JWT creation, user/group setup.
    *   Execution Client installation (download binary + systemd).
    *   Consensus Client installation.
    *   Validator Client installation.
    *   MEV-Boost installation.
    *   `common.finish_install()`: Service reload and completion report.
