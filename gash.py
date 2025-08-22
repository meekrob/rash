#!/usr/bin/env python3
import paramiko
import time
from datetime import datetime
import os,sys
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

# --- Open persistent shell ---
channel = ssh.get_transport().open_session()
channel.get_pty()
channel.invoke_shell()
channel.settimeout(0.1)

# --- Detect server prompt ---
time.sleep(0.2)
channel.send('echo "$PS1"\n'.encode())
time.sleep(0.2)
ps1_output = ""
while channel.recv_ready():
    ps1_output += channel.recv(4096).decode()
server_prompt = ps1_output.strip().splitlines()[-1]

# --- Determine remote home directory dynamically ---
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

def wait_for_file(remote_path, timeout=10.0):
    """Wait until the remote file exists or timeout occurs."""
    print(f"Waiting for {remote_path}...", file=sys.stderr)
    start = time.time()
    while True:
        try:
            sftp.stat(remote_path)
            return
        except FileNotFoundError:
            if time.time() - start > timeout:
                raise TimeoutError(f"File {remote_path} did not appear within {timeout:.1f} sec")
            time.sleep(0.05)


def read_remote_file(remote_path):

    wait_for_file(remote_path)

    with sftp.open(remote_path, "r") as f:
        return f.read().decode()

# --- Run command using source history-cmd# ---
def run_command(cmd_number, description, command):
    print(f"\n--- Test #{cmd_number}: {description} ---")

    hist_file = f"{session_dir}/history-cmd{cmd_number}"
    stdout_file = f"{session_dir}/stdout-cmd{cmd_number}"
    stderr_file = f"{session_dir}/stderr-cmd{cmd_number}"
    status_file = f"{session_dir}/status-cmd{cmd_number}"
    sentinel = f"__DONE_{cmd_number}__"

    # Write command to history
    escaped_cmd = command.replace('"', '\\"')
    channel.send(f'echo "{escaped_cmd}" > {hist_file}\n'.encode())
    time.sleep(0.05)

    # Execute command, redirect outputs, write exit status, append sentinel
    exec_cmd = f"source {hist_file} > {stdout_file} 2> {stderr_file}; echo $? > {status_file}; sync; echo {sentinel}\n"
    start_time = time.time()
    channel.send(exec_cmd.encode())

    # Wait until sentinel appears
    buffer = ""
    while True:
        if channel.recv_ready():
            data = channel.recv(4096).decode()
            buffer += data
            if sentinel in buffer:
                break
        else:
            time.sleep(0.05)

    # Read files once via SFTP
    stdout_text = read_remote_file(stdout_file).strip()
    stderr_text = read_remote_file(stderr_file).strip()
    try:
        exit_status = int(read_remote_file(status_file).strip())
    except ValueError:
        exit_status = None

    duration = time.time() - start_time

    # Print results
    print(f"STDOUT:\n{stdout_text}")
    print(f"STDERR:\n{stderr_text}")
    print(f"Exit status: {exit_status}")
    print(f"Duration (including file reads): {duration:.2f} sec")

    return cmd_number + 1

# --- Example tests ---
cmd_number = 1
cmd_number = run_command(cmd_number, "Whoami", "whoami")
cmd_number = run_command(cmd_number, "List root dir (stderr expected)", "ls /nonexistent")
cmd_number = run_command(cmd_number, "Echo test", "echo hello world")
cmd_number = run_command(cmd_number, "Change dir", "mkdir -p testdir && cd testdir && pwd")

# --- Session directory size ---
stdin, stdout, stderr = ssh.exec_command(f"du -sh {session_dir}")
size_info = stdout.read().decode().strip()
print(f"\nTotal session directory size: {size_info}")

# --- Close SSH ---
sftp.close()
channel.close()
ssh.close()
