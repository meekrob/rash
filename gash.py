#!/usr/bin/env python3
import paramiko
import time
from datetime import datetime
import os
import getpass

# --- Connection info ---
host = "riviera.colostate.edu"
username = "dking"

# --- SSH key authentication ---
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

# --- Open persistent shell ---
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
    ps1_output += channel.recv(4096).decode()
server_prompt = ps1_output.strip().splitlines()[-1]

# --- Determine remote home directory ---
stdin, stdout, stderr = ssh.exec_command("echo $HOME")
home_dir = stdout.read().decode().strip()
if not home_dir:
    raise RuntimeError("Could not determine remote home directory.")

# --- Create session directory ---
timestamp = datetime.now().strftime("%Y-%m-%d-%H%M-%S")
session_dir = f"{home_dir}/.gash/session-{timestamp}"
channel.send(f"mkdir -p {session_dir}\n".encode())
time.sleep(0.1)
print(f"Session directory: {session_dir}")

# --- SFTP for reading files efficiently ---
sftp = ssh.open_sftp()

# --- Read remote file with wait ---
def read_remote_file(remote_path, timeout=10.0):
    """Wait for the remote file to exist, then read and return its contents."""
    start = time.time()
    while True:
        try:
            with sftp.open(remote_path, "r") as f:
                return f.read().decode()
        except FileNotFoundError:
            if time.time() - start > timeout:
                raise TimeoutError(f"File {remote_path} did not appear within {timeout:.1f} sec")
            time.sleep(0.05)

# --- Run command using source history-cmd# ---
def run_command(
    cmd_number,
    description,
    command,
    expected_exit=None,
    expected_stdout=None,
    expected_stderr=None,
    test=False,  # verbose pass/fail checks if True
):
    hist_file   = f"{session_dir}/history-cmd{cmd_number}"
    stdout_file = f"{session_dir}/stdout-cmd{cmd_number}"
    stderr_file = f"{session_dir}/stderr-cmd{cmd_number}"
    status_file = f"{session_dir}/status-cmd{cmd_number}"
    sentinel    = f"__DONE_{cmd_number}__"

    if test:
        print(f"\n--- Test #{cmd_number}: {description} ---")

    # Write the user command to history
    escaped_cmd = command.replace('"', '\\"')
    channel.send(f'echo "{escaped_cmd}" > {hist_file}\n'.encode())
    time.sleep(0.05)

    # Execute the command
    exec_cmd = f"source {hist_file} > {stdout_file} 2> {stderr_file}; echo $? > {status_file}; echo {sentinel}\n"
    start_time = time.time()
    channel.send(exec_cmd.encode())

    # Wait until sentinel appears in the channel
    buffer = ""
    while True:
        if channel.recv_ready():
            buffer += channel.recv(4096).decode()
            if sentinel in buffer:
                break
        else:
            time.sleep(0.05)

    # Read outputs
    stdout_text = read_remote_file(stdout_file).strip()
    stderr_text = read_remote_file(stderr_file).strip()
    try:
        exit_status = int(read_remote_file(status_file).strip())
    except ValueError:
        exit_status = None

    duration = time.time() - start_time

    # Always print main outputs
    print(f"STDOUT:\n{stdout_text}")
    print(f"STDERR:\n{stderr_text}")
    print(f"Exit status: {exit_status}")
    print(f"Duration (including file reads): {duration:.2f} sec")

    # --- Automatic pass/fail checks (verbose) ---
    if test:
        test_passed = True
        if expected_exit is not None and exit_status != expected_exit:
            test_passed = False
            print(f"FAIL: Expected exit status {expected_exit}, got {exit_status}")
        if expected_stdout is not None and expected_stdout not in stdout_text:
            test_passed = False
            print(f"FAIL: Expected stdout to contain: {expected_stdout}")
        if expected_stderr is not None and expected_stderr not in stderr_text:
            test_passed = False
            print(f"FAIL: Expected stderr to contain: {expected_stderr}")
        if test_passed:
            print("PASS")

    return cmd_number + 1

# --- Example usage ---
cmd_number = 1
cmd_number = run_command(cmd_number, "Whoami", "whoami", expected_exit=0, test=True)
cmd_number = run_command(cmd_number, "Nonexistent file", "ls /nonexistent", expected_exit=2, expected_stderr="No such file", test=True)
cmd_number = run_command(cmd_number, "Echo test", "echo hello world", expected_stdout="hello world", test=True)

# --- Optional: check session directory size ---
stdin, stdout, stderr = ssh.exec_command(f"du -sh {session_dir}")
size_info = stdout.read().decode().strip()
print(f"\nTotal session directory size: {size_info}")

# --- Close SSH ---
sftp.close()
channel.close()
ssh.close()
