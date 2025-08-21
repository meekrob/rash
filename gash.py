#!/usr/bin/env python3
import paramiko
import time
from datetime import datetime
import os
import getpass

# --- Connection info ---
host = "riviera.colostate.edu"
username = "dking"

# --- SSH key auth ---
key_file = os.path.expanduser("~/.ssh/id_rsa")
pkey = None
if os.path.exists(key_file):
    try:
        pkey = paramiko.RSAKey.from_private_key_file(key_file)
    except paramiko.PasswordRequiredException:
        passphrase = getpass.getpass(f"Enter passphrase for {key_file}: ")
        pkey = paramiko.RSAKey.from_private_key_file(key_file, password=passphrase)

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
if pkey:
    ssh.connect(host, username=username, pkey=pkey)
else:
    password = getpass.getpass("Password: ")
    ssh.connect(host, username=username, password=password)

# --- Open interactive shell ---
channel = ssh.get_transport().open_session()
channel.get_pty()
channel.invoke_shell()
channel.settimeout(0.1)

# --- Detect server prompt ---
time.sleep(0.2)
channel.send('echo "$PS1"\n')
time.sleep(0.2)
ps1_output = ""
while channel.recv_ready():
    ps1_output += channel.recv(1024).decode()
server_prompt = ps1_output.strip().splitlines()[-1]

# --- Create session directory ---
timestamp = datetime.now().strftime("%Y-%m-%d-%H%M-%S")
session_dir = f"~/.gash/session-{timestamp}"
channel.send(f"mkdir -p {session_dir}\n".encode())
time.sleep(0.2)
print(f"Session directory: {session_dir}")

# --- Helper functions ---
def wait_for_status_file(ssh, status_file):
    while True:
        stdin, stdout, stderr = ssh.exec_command(f"test -f {status_file} && echo ready || echo wait")
        if stdout.read().decode().strip() == "ready":
            break
        time.sleep(0.1)

def read_file(ssh, remote_file):
    stdin, stdout, stderr = ssh.exec_command(f"cat {remote_file}")
    return stdout.read().decode()

def run_test(cmd_number, description, command, expected_stdout=None, expected_stderr=None, expected_exit=None):
    print(f"\n--- Test #{cmd_number}: {description} ---")
    
    hist_file = f"{session_dir}/history-cmd{cmd_number}"
    stdout_file = f"{session_dir}/stdout-cmd{cmd_number}"
    stderr_file = f"{session_dir}/stderr-cmd{cmd_number}"
    status_file = f"{session_dir}/status-cmd{cmd_number}"

    # Measure start time
    start_time = time.time()

    # Write command to history
    escaped_cmd = command.replace('"', '\\"')
    channel.send(f'echo "{escaped_cmd}" > {hist_file}\n'.encode())
    time.sleep(0.1)

    # Execute command via source
    channel.send(f"source {hist_file} > {stdout_file} 2> {stderr_file}; echo $? > {status_file}\n".encode())

    # Wait for completion
    wait_for_status_file(ssh, status_file)

    # Measure duration
    duration = time.time() - start_time

    # Read outputs
    stdout_text = read_file(ssh, stdout_file).strip()
    stderr_text = read_file(ssh, stderr_file).strip()
    try:
        exit_status = int(read_file(ssh, status_file).strip())
    except ValueError:
        exit_status = None

    # Print outputs
    print(f"STDOUT:\n{stdout_text}")
    print(f"STDERR:\n{stderr_text}")
    print(f"Exit status: {exit_status}")
    print(f"Duration: {duration:.2f} sec")

    # Automatic checks
    passed = True
    if expected_stdout is not None and stdout_text != expected_stdout:
        print(f"FAIL: Expected stdout '{expected_stdout}'")
        passed = False
    if expected_stderr is not None and stderr_text != expected_stderr:
        print(f"FAIL: Expected stderr '{expected_stderr}'")
        passed = False
    if expected_exit is not None and exit_status != expected_exit:
        print(f"FAIL: Expected exit status {expected_exit}")
        passed = False

    if passed:
        print("PASS")

    return cmd_number + 1

# --- Define tests ---
tests = [
    {"desc": "Check whoami", "cmd": "whoami", "expected_exit": 0},
    {"desc": "Check working directory", "cmd": "pwd", "expected_exit": 0},
    {"desc": "Nonexistent file listing (stderr test)", "cmd": "ls /nonexistent", "expected_exit": 2},
    {"desc": "Create directory testdir", "cmd": "mkdir -p testdir", "expected_exit": 0},
    {"desc": "Change into testdir", "cmd": "cd testdir", "expected_exit": 0},
    {"desc": "Print pwd inside testdir", "cmd": "pwd", "expected_exit": 0},
    {"desc": "Return to parent directory", "cmd": "cd ..", "expected_exit": 0},
    {"desc": "Print pwd after return", "cmd": "pwd", "expected_exit": 0},
    {"desc": "Export env variable MYVAR", "cmd": 'export MYVAR="hello world"', "expected_exit": 0},
    {"desc": "Echo env variable MYVAR", "cmd": "echo $MYVAR", "expected_stdout": "hello world", "expected_exit": 0},
    {"desc": "Stdout redirect test", "cmd": 'echo "This is stdout" > out.txt', "expected_exit": 0},
    {"desc": "Stderr test", "cmd": 'echo "This is stderr" 1>&2', "expected_exit": 0},
    {"desc": "Read stdout file", "cmd": "cat out.txt", "expected_stdout": "This is stdout", "expected_exit": 0},
    {"desc": "Command with failure exit status", "cmd": "grep 'needle' /dev/null", "expected_exit": 1},
]

# --- Run tests ---
cmd_number = 1
for test in tests:
    cmd_number = run_test(
        cmd_number,
        description=test["desc"],
        command=test["cmd"],
        expected_stdout=test.get("expected_stdout"),
        expected_stderr=test.get("expected_stderr"),
        expected_exit=test.get("expected_exit")
    )

# --- Session directory size ---
stdin, stdout, stderr = ssh.exec_command(f"du -sh {session_dir}")
size_info = stdout.read().decode().strip()
print(f"\nTotal session directory size: {size_info}")

# --- Close SSH ---
channel.close()
ssh.close()
