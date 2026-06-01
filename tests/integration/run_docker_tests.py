import os
import sys
import time
import asyncio
import subprocess
import argparse
from datetime import datetime

try:
    from rich.live import Live
    from rich.console import Console, Group
    from rich.panel import Panel
    from rich.text import Text
    from rich.table import Table
except ImportError:
    Live = Console = Group = Panel = Text = Table = None

# Matrices
combos = [
    "Caplin-Erigon",
    "Lighthouse-Reth",
    "Lodestar-Besu",
    "Nimbus-Nethermind",
    "Teku-Besu",
]

variations = [
    "--network HOODI --mev --config 'Solo Staking Node'",
    "--network SEPOLIA --config 'Full Node Only'",
]

custom_tests = [
    ("Geth-Lighthouse-Custom-Setup-SEPOLIA", "python3 /ethpillar/tests/integration/run_inside_docker.py deploy/deploy-node.py --ec Geth --cc Lighthouse --vc Lighthouse --network SEPOLIA --mev --config 'Custom Setup'"),
    ("Nethermind-Grandine-Custom-Setup-SEPOLIA", "python3 /ethpillar/tests/integration/run_inside_docker.py deploy/deploy-node.py --ec Nethermind --cc Grandine --vc Lighthouse --network SEPOLIA --mev --config 'Custom Setup'"),
    ("Prysm-Reth-Custom-Setup-SEPOLIA", "python3 /ethpillar/tests/integration/run_inside_docker.py deploy/deploy-node.py --ec Reth --cc Prysm --vc Prysm --network SEPOLIA --mev --config 'Custom Setup'"),
    ("Teku-Besu-VC-Only-HOODI", "python3 /ethpillar/tests/integration/run_inside_docker.py deploy/deploy-node.py --combo Teku-Besu --network HOODI --config 'Validator Client Only' --vc_only_bn_address http://192.168.1.123:5052"),
]

upgrade_tests = [
    ("Upgrade-Reth-Lighthouse", "python3 /ethpillar/tests/integration/run_inside_docker.py deploy/deploy-node.py --ec Reth --cc Lighthouse --network SEPOLIA --config 'Full Node Only' --test-updates"),
    ("Upgrade-Besu-Teku", "python3 /ethpillar/tests/integration/run_inside_docker.py deploy/deploy-node.py --ec Besu --cc Teku --network SEPOLIA --config 'Full Node Only' --test-updates"),
    ("Upgrade-Nethermind-Nimbus", "python3 /ethpillar/tests/integration/run_inside_docker.py deploy/deploy-node.py --ec Nethermind --cc Nimbus --network SEPOLIA --config 'Full Node Only' --test-updates"),
    ("Upgrade-Erigon-Caplin", "python3 /ethpillar/tests/integration/run_inside_docker.py deploy/deploy-node.py --ec Erigon --cc Caplin --network SEPOLIA --config 'Full Node Only' --test-updates"),
    ("Upgrade-Geth-Lodestar", "python3 /ethpillar/tests/integration/run_inside_docker.py deploy/deploy-node.py --ec Geth --cc Lodestar --network SEPOLIA --config 'Full Node Only' --test-updates"),
]

switch_tests = [
    ("Switch-Reth-Lighthouse-to-Besu-Teku", "python3 /ethpillar/tests/integration/run_inside_docker.py deploy/deploy-node.py --ec Reth --cc Lighthouse --network SEPOLIA --config 'Full Node Only' --test-switching"),
]

class TestTask:
    def __init__(self, label, cmd, display_var, log_suffix=""):
        self.label = label
        self.cmd = cmd
        self.display_var = display_var
        self.log_suffix = log_suffix
        
        # Derive a safe file name
        base_name = "".join([c if c.isalnum() or c == '-' else '_' for c in label])
        import re
        base_name = re.sub(r'_+', '_', base_name)
        if log_suffix:
            base_name = f"{base_name}_{log_suffix}"
            
        self.log_name = base_name
        self.log_file = None
        self.container_name = f"ep-test-{base_name[:60]}"
        self.status = "PENDING"
        self.duration = 0
        self.start_time = 0

def generate_tests():
    tests = []
    import re
    for combo in combos:
        for var in variations:
            actual_var = var
                
            match = re.search(r'--network\s+(\S+)', actual_var)
            local_network = match.group(1) if match else ""
            
            cmd = f"python3 /ethpillar/tests/integration/run_inside_docker.py deploy/deploy-node.py --combo \"{combo}\" {actual_var}"
            tests.append(TestTask(combo, cmd, actual_var, local_network))

    for label, cmd in custom_tests:
        tests.append(TestTask(label, cmd, "Custom"))
    for label, cmd in upgrade_tests:
        tests.append(TestTask(label, cmd, "Upgrade"))
    for label, cmd in switch_tests:
        tests.append(TestTask(label, cmd, "Switch"))
        
    return tests

def tail_file(filepath, lines=20):
    if not filepath or not os.path.exists(filepath):
        return "Waiting for logs..."
    try:
        with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
            content = f.readlines()
            return "".join(content[-lines:]).strip()
    except Exception as e:
        return f"Error reading log: {e}"

async def run_test(task: TestTask, results_dir: str, semaphore: asyncio.Semaphore):
    async with semaphore:
        task.status = "RUNNING"
        task.log_file = os.path.join(results_dir, f"{task.log_name}.log")
        
        # Ensure log file exists so we can tail it immediately
        with open(task.log_file, "w") as f:
            f.write(f"Starting test {task.label}...\n")
            
        task.start_time = time.time()
        
        try:
            # Cleanup any orphaned container
            subprocess.run(f"docker rm -f {task.container_name}", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            
            flags = "--privileged --cgroupns=host --tmpfs /run --tmpfs /run/lock"
            cwd = os.getcwd()
            run_cmd = f"docker run -d --name {task.container_name} {flags} -v {cwd}:/ethpillar ethpillar-rebuild"
            
            proc = await asyncio.create_subprocess_shell(run_cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
            await proc.wait()
            
            if proc.returncode != 0:
                task.status = "FAIL"
                task.duration = int(time.time() - task.start_time)
                with open(task.log_file, "a") as f:
                    f.write(f"\nFailed to start container. Exit code {proc.returncode}\n")
                return
                
            await asyncio.sleep(3) # Wait for systemd to initialize
            
            # Exec the actual test
            exec_cmd = f"docker exec {task.container_name} {task.cmd} >> {task.log_file} 2>&1"
            proc = await asyncio.create_subprocess_shell(exec_cmd)
            await proc.wait()
            
            task.status = "PASS" if proc.returncode == 0 else "FAIL"
            task.duration = int(time.time() - task.start_time)
        finally:
            subprocess.run(f"docker rm -f {task.container_name}", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

async def ui_loop(tasks: list[TestTask]):
    if Console is None:
        print("The 'rich' library is required. Install it using: pip install rich")
        sys.exit(1)
    console = Console()
    with Live(refresh_per_second=2, console=console, screen=True, transient=False) as live:
        # Build the completed table ONCE with all rows pre-populated.
        # screen=True uses the alternate terminal buffer — no flicker.
        status_lookup = {}
        dur_lookup = {}
        var_lookup = {}
        for t in tasks:
            var_lookup[t.label] = t.display_var
            status_lookup[t.label] = "[dim]PENDING[/dim]"
            dur_lookup[t.label] = "—"

        def build_table():
            table = Table(title="Completed Tests", expand=True, show_lines=False)
            table.add_column("Test", style="cyan", no_wrap=True)
            table.add_column("Variation", style="magenta", no_wrap=True)
            table.add_column("Status", justify="center", no_wrap=True)
            table.add_column("Duration", justify="right", no_wrap=True)
            for t in tasks:
                table.add_row(t.label, var_lookup[t.label], status_lookup[t.label], dur_lookup[t.label])
            return table

        while True:
            running_tasks = [t for t in tasks if t.status == "RUNNING"]
            pending_tasks = [t for t in tasks if t.status == "PENDING"]

            for t in tasks:
                if t.status == "PASS":
                    status_lookup[t.label] = "[green]PASS[/green]"
                    dur_lookup[t.label] = f"{t.duration}s"
                elif t.status == "FAIL":
                    status_lookup[t.label] = "[red]FAIL[/red]"
                    dur_lookup[t.label] = f"{t.duration}s"

            renderables = [build_table()]

            for t in running_tasks:
                table_height = len(tasks) + 5
                available = max(6, console.size.height - table_height - 4)
                panel_height = min(14, available)
                log_text = tail_file(t.log_file, max(5, panel_height - 4))
                dur = int(time.time() - t.start_time)
                panel = Panel(
                    Text.from_ansi(log_text),
                    title=f"[yellow]RUNNING: {t.label} ({dur}s)[/yellow]",
                    border_style="yellow",
                    height=panel_height
                )
                renderables.append(panel)

            if not running_tasks and not pending_tasks:
                renderables.append(Text("All tests complete.", style="green bold"))

            if pending_tasks:
                next_names = ", ".join(t.log_name for t in pending_tasks[:3])
                suffix = "..." if len(pending_tasks) > 3 else ""
                renderables.append(Text(f"Pending ({len(pending_tasks)}): {next_names}{suffix}", style="blue"))

            live.update(Group(*renderables))

            if not running_tasks and not pending_tasks:
                break
            await asyncio.sleep(0.5)

def generate_html_report(tasks, results_dir, total_duration, timestamp):
    html_path = os.path.join(results_dir, "index.html")
    passed = len([t for t in tasks if t.status == "PASS"])
    failed = len([t for t in tasks if t.status == "FAIL"])
    skipped = len([t for t in tasks if t.status == "SKIPPED"])
    
    html = f"""<!DOCTYPE html>
<html>
<head>
    <title>Integration Test Report - {timestamp}</title>
    <style>
        body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; margin: 40px; background: #f8f9fa; color: #333; }}
        .container {{ max-width: 1200px; margin: auto; background: white; padding: 30px; border-radius: 8px; box-shadow: 0 4px 6px rgba(0,0,0,0.1); }}
        h1 {{ color: #007bff; border-bottom: 2px solid #eee; padding-bottom: 10px; }}
        .summary {{ margin-bottom: 20px; font-weight: bold; }}
        table {{ border-collapse: collapse; width: 100%; margin-top: 20px; }}
        th, td {{ border: 1px solid #dee2e6; padding: 12px; text-align: left; }}
        th {{ background-color: #f1f3f5; color: #495057; }}
        tr:hover {{ background-color: #f8f9fa; }}
        .status-pass {{ color: #28a745; font-weight: bold; }}
        .status-fail {{ color: #dc3545; font-weight: bold; }}
        .status-skipped {{ color: #ffc107; font-weight: bold; }}
        a {{ color: #007bff; text-decoration: none; }}
        a:hover {{ text-decoration: underline; }}
        .footer {{ margin-top: 30px; font-size: 0.9em; color: #6c757d; text-align: center; }}
    </style>
</head>
<body>
    <div class="container">
        <h1>Integration Test Report</h1>
        <div class="summary">
            Run Time: {timestamp} <br>
            Total Duration: {total_duration}s <br>
            Total Tests: {len(tasks)} <br>
            Passed: {passed} <br>
            Failed: {failed} <br>
            Skipped: {skipped}
        </div>
        <table>
            <thead>
                <tr>
                    <th>Script</th>
                    <th>Variation</th>
                    <th>Status</th>
                    <th>Duration</th>
                    <th>Log</th>
                </tr>
            </thead>
            <tbody>
"""
    for t in tasks:
        status_class = f"status-{t.status.lower()}"
        log_link = f"<a href='{os.path.basename(t.log_file)}' target='_blank'>View Log</a>" if t.log_file else "-"
        html += f"                <tr><td>{t.label}</td><td>{t.display_var}</td><td class='{status_class}'>{t.status}</td><td>{t.duration}s</td><td>{log_link}</td></tr>\n"
        
    html += """            </tbody>
        </table>
        <div class="footer">
            EthPillar Integration Suite
        </div>
    </div>
</body>
</html>
"""
    with open(html_path, "w") as f:
        f.write(html)
    return html_path

async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--parallel", type=int, default=1, help="Max concurrent tests (must be 1)")
    args = parser.parse_args()
    if args.parallel != 1:
        print("Concurrency > 1 is not yet supported with the Rich UI. Defaulting to 1.")
        args.parallel = 1
    
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    results_dir = os.path.join(os.getcwd(), "tests", "integration", "results", f"run_{timestamp}")
    os.makedirs(results_dir, exist_ok=True)
    
    # Cache cleanup
    cache_dir = os.path.join(os.getcwd(), "tests", "integration", "cache")
    if os.path.exists(cache_dir):
        print("Cleaning up orphaned cache temp files...")
        subprocess.run("find tests/integration/cache -name 'tmp*' -type f -delete 2>/dev/null; sudo find tests/integration/cache -name 'tmp*' -type f -delete 2>/dev/null", shell=True)
        # Remove empty extracted cache directories (poison pills from failed prior runs)
        subprocess.run("find tests/integration/cache -name 'extracted_*' -type d -empty -delete 2>/dev/null; sudo find tests/integration/cache -name 'extracted_*' -type d -empty -delete 2>/dev/null", shell=True)
        
    print("Rebuilding Docker image...")
    res = subprocess.run("docker build -t ethpillar-rebuild -f tests/integration/Dockerfile.test .", shell=True)
    if res.returncode != 0:
        print("Failed to build Docker image.")
        sys.exit(1)
        
    tasks = generate_tests()
    semaphore = asyncio.Semaphore(args.parallel)
    
    start_time = time.time()
    
    # Start UI and execution
    exec_coros = [run_test(t, results_dir, semaphore) for t in tasks]
    
    await asyncio.gather(ui_loop(tasks), *exec_coros)
    
    total_duration = int(time.time() - start_time)
    
    html_path = generate_html_report(tasks, results_dir, total_duration, timestamp)
    print(f"\n✅ Report generated: {html_path}")
    print(f"⏱️ Total runtime: {total_duration}s")
    
    failed_count = len([t for t in tasks if t.status == "FAIL"])
    if failed_count > 0:
        print(f"❌ {failed_count} tests failed.")
        sys.exit(1)
    else:
        print("✅ All integration tests passed!")
        sys.exit(0)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nTests aborted by user.")
        sys.exit(1)
