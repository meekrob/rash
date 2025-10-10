#!/usr/bin/env python3
"""
This script is a prototype script for a terminal emulator / GUI hybrid. 
It handles the shell connections and backend only.

Set connection information credentials in main()

Usage:
    python rash.py

Dependencies:
    paramiko, flask, prompt_toolkit

    See environment.yml for full list.

"""
import os
import sys
import time
from datetime import datetime
from collections import defaultdict
import getpass
# libs to get fingerprint from publickey
import hashlib
import base64
from typing import Any,TypedDict,Optional
import paramiko
from prompt_toolkit import PromptSession
from prompt_toolkit.history import InMemoryHistory


DO_TESTS_ON_CONNECT = False

def fingerprint_from_pubkey_file(pub_file: str) -> Optional[str]:
    """
    Compute a SHA256-style fingerprint from any OpenSSH public key file.
    Works for ssh-ed25519, ssh-rsa, ecdsa-sha2-*, ssh-dss, etc.
    """
    try:
        with open(pub_file, "r", encoding="ascii") as f:
            parts = f.read().strip().split()
            if len(parts) < 2:
                raise ValueError("Invalid public key format")

            key_type, key_b64 = parts[:2]
            print(f"Detected key type {key_type} from {pub_file}", file=sys.stderr)
            key_data = base64.b64decode(key_b64.encode("ascii"))
            digest = hashlib.sha256(key_data).digest()
            fingerprint = base64.b64encode(digest).decode("ascii").rstrip("=")
            return f"SHA256:{fingerprint}"

    except FileNotFoundError:
        print(f"Public key file not found: {pub_file}", file=sys.stderr)
    except (ValueError, IndexError, base64.binascii.Error) as e:
        print(f"Failed to parse {pub_file}: {e}", file=sys.stderr)

    return None

def find_public_key_file(private_key_file: str) -> str:
    """
    Given the private_key file path, assume the public key is the same with ".pub
    """
    private_key_path = os.path.expanduser(private_key_file)
    public_key_path = private_key_path + ".pub"

    if os.path.exists(public_key_path):
        return public_key_path

    raise FileNotFoundError

def find_agent_key_by_pub_fingerprint(private_file: str) -> paramiko.AgentKey | None:
    """
    See if the user's provided key file matches a key in the
    SSH agent so they don't have to provide a passphrase
    """
    pub_file = find_public_key_file(private_file)
    target_fp = fingerprint_from_pubkey_file(pub_file)
    if not target_fp:
        return None

    agent = paramiko.Agent()
    for key in agent.get_keys():
        digest = base64.b64encode(
            hashlib.sha256(
                key.asbytes()).digest()).decode("ascii").rstrip("=")
        agent_fp = f"SHA256:{digest}"
        if agent_fp == target_fp:
            return key
    return None

def main():
    """
    Set connection info, optionally run shell tests, and enter interactive session
    """

    # --- Connection info ---
    host = "riviera.colostate.edu"
    username = "dking"
    # passwordless login
    private_key_file = "" # "~/.ssh/id_ed25519"
    channel,ssh = open_connection(host, username, private_key_file)
    session_vars = initialize_session(channel, ssh)

    # extract vars for session
    sftp = session_vars['sftp']
    session_dir = session_vars['session_dir']
    #home_dir = session_vars['home_dir']
    #server_prompt = session_vars['server_prompt']


    # --- TESTS of basic operation ---
    cmd_number = 1
    if DO_TESTS_ON_CONNECT:
        for shell_test in SHELL_TESTS:
            cmd_number = run_command(cmd_number, session_vars,
                                    command=shell_test['cmd'],
                                    test=shell_test)


    # INTERACTIVE LOOP
    interactive_loop(cmd_number, session_vars)

    # --- Optional: check session directory size ---
    _,stdout,_ = ssh.exec_command(f"du -sh {session_dir}")

    size_info = stdout.read().decode().strip()
    print(f"\nTotal session directory size: {size_info}")

    # --- Close SSH ---
    sftp.close()
    channel.close()
    ssh.close()

def stream_command_output(sftp, stdout_file, stderr_file, sentinel_file, poll_interval=0.1):
    """Stream stdout/stderr while command is running."""
    stdout_seen = 0
    stderr_seen = 0

    while True:
        # Read new stdout
        try:
            with sftp.open(stdout_file, "r") as f:
                f.seek(stdout_seen)
                new_stdout = f.read().decode()
                if new_stdout:
                    print(new_stdout, end="", flush=True)
                    stdout_seen += len(new_stdout.encode())
        except FileNotFoundError:
            pass

        # Read new stderr
        try:
            with sftp.open(stderr_file, "r") as f:
                f.seek(stderr_seen)
                new_stderr = f.read().decode()
                if new_stderr:
                    print(new_stderr, end="", flush=True, file=sys.stderr)
                    stderr_seen += len(new_stderr.encode())
        except FileNotFoundError:
            pass

        # Check if sentinel/status file exists (command finished)
        try:
            sftp.stat(sentinel_file)
            break
        except FileNotFoundError:
            pass

        time.sleep(poll_interval)


def read_channel_with_timeout(channel, sentinel, timeout=5.0):
    """Read from channel until no more data or timeout expires."""
    start_time = time.time()
    buffer = ""
    while True:
        if channel.recv_ready():
            buffer += channel.recv(4096).decode()
            start_time = time.time()  # reset timeout after new data
            if sentinel in buffer:
                return buffer
        elif time.time() - start_time > timeout:
            break
        else:
            time.sleep(0.05)
    return buffer

class ShellTest(TypedDict):
    """
    Wrapper for a test of a shell command
    """
    desc: str
    cmd: str
    expected_exit: int
    expected_stdout: Optional[str]
    expected_stderr: Optional[str]

def formulate_command(command:str, session_dir: str, cmd_number: int) -> dict:
    """
    formulate_command - resolve paths of history/output files and write command strings
    """
    hist_file   = f"{session_dir}/history-cmd{cmd_number}"
    stdout_file = f"{session_dir}/stdout-cmd{cmd_number}"
    stderr_file = f"{session_dir}/stderr-cmd{cmd_number}"
    status_file = f"{session_dir}/status-cmd{cmd_number}"
    sentinel    = f"__DONE_{cmd_number}__"

    # Write the user command to history
    escaped_cmd = command.replace('"', '\\"')
    history_cmd = f'echo "{escaped_cmd}" > {hist_file}\n'


    exec_cmd = ''.join([ f"source {hist_file} > {stdout_file}",
                         f" 2> {stderr_file}; ",
                         f"echo $? > {status_file}; ",
                         f"echo {sentinel}\n"])

    return {'history': history_cmd.encode(),
            'exec': exec_cmd.encode(),
            'stdout_file': stdout_file,
            'stderr_file': stderr_file,
            'status_file': status_file,
            'sentinel': sentinel
            }

# --- Run command using source history-cmd# ---
def run_command(
    cmd_number:int,
    session_vars:dict,
    command:str,
    test: ShellTest|None
):
    """
    Basic function for running a command
    """

    channel = session_vars['channel']
    sftp = session_vars['sftp']

    if test is not None:
        print(f"\n--- Test #{cmd_number}: {test['desc']} ---")

    # transfer user command to history file, make 'source' command
    commands = formulate_command(command, session_vars['session_dir'], cmd_number)

    # write history
    channel.send(commands['history'])
    time.sleep(.05)

    # Execute the command
    start_time = time.time()
    channel.send(commands['exec'])

    # Stream output while running
    stream_command_output(sftp, commands['stdout_file'],
                          commands['stderr_file'],
                          commands['status_file'])

    # Wait until sentinel appears in the channel
    timeout=10.0
    #command_timedout = False
    buffer = read_channel_with_timeout(channel, commands['sentinel'], timeout=timeout)
    if commands['sentinel'] not in buffer:
        print(f"[WARNING] Command output exceeded {timeout}")
        #command_timedout = True

    # Read outputs
    stdout_text = read_remote_file(sftp, commands['stdout_file']).strip()
    stderr_text = read_remote_file(sftp, commands['stderr_file']).strip()
    try:
        exit_status = int(read_remote_file(sftp, commands['status_file']).strip())
    except ValueError:
        exit_status = None

    # Always print main outputs
    print(f"STDOUT[{len(stdout_text)} bytes]")
    print(f"STDERR[{len(stderr_text)} bytes]")
    print(f"Exit status: {exit_status}")
    print(f"Duration (including file reads): {time.time() - start_time:.2f} sec")

    # --- Automatic pass/fail checks (verbose) ---
    if test is not None:
        tested = defaultdict(lambda: None, test)
        test_passed = True
        if tested['expected_exit'] is not None and exit_status != tested['expected_exit']:
            test_passed = False
            print(f"FAIL: Expected exit status {tested['expected_exit']}, got {exit_status}")
        if tested['expected_stdout'] is not None and tested['expected_stdout'] not in stdout_text:
            test_passed = False
            print(f"FAIL: Expected stdout to contain: {tested['expected_stdout']}")
        if tested['expected_stderr'] is not None and tested['expected_stderr'] not in stderr_text:
            test_passed = False
            print(f"FAIL: Expected stderr to contain: {tested['expected_stderr']}")
        if test_passed:
            print("PASS")

    return cmd_number + 1


def open_connection(host:str,
               username:str,
               private_key_path:str|None) -> tuple[paramiko.Channel, paramiko.SSHClient]:
    """
    open_connection - connect using password or key file
    """
    # --- SSH key authentication ---
    #key_file = os.path.expanduser("~/.ssh/id_rsa")
    if private_key_path is not None:
        private_key_path = os.path.expanduser(private_key_path)

    pkey:paramiko.AgentKey|None = None

    if private_key_path is not None and os.path.exists(private_key_path):
        try:
            pkey = paramiko.Ed25519Key.from_private_key_file(private_key_path)
        except paramiko.PasswordRequiredException:
            # first, try to determine if there is a corresponding key in the key chain,
            # using the given path + ".pub"
            pkey = find_agent_key_by_pub_fingerprint(private_key_path)
            if pkey is None: # none found or there was a problem
                passphrase = getpass.getpass(f"Enter passphrase for {private_key_path}: ")
                pkey = paramiko.Ed25519Key.from_private_key_file(private_key_path,
                                                                 password=passphrase)

    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    if pkey:
        ssh.connect(host, username=username, pkey=pkey)
    else:
        password = getpass.getpass("Password (no pkey): ")
        ssh.connect(host, username=username, password=password)

    # --- Open persistent shell ---
    transport = ssh.get_transport()
    if transport is None:
        raise AttributeError("Ssh was unable to get_transport()")

    channel = transport.open_session()
    channel.get_pty()
    channel.invoke_shell()
    channel.settimeout(0.1)
    return channel, ssh


def initialize_session(channel:paramiko.Channel, ssh:paramiko.SSHClient) -> dict[str, Any]:
    """
    initialize_session - Initialize backend setup after successful login.
    """
    # --- Detect server prompt ---
    time.sleep(0.2)
    channel.send('echo "$PS1"\n'.encode())
    time.sleep(0.2)
    ps1_output = ""
    while channel.recv_ready():
        ps1_output += channel.recv(4096).decode()
    server_prompt = ps1_output.strip().splitlines()[-1]

    # --- Determine remote home directory ---
    _, stdout, _ = ssh.exec_command("echo $HOME")
    home_dir = stdout.read().decode().strip()
    if not home_dir:
        raise RuntimeError("Could not determine remote home directory.")

    # --- Create session directory ---
    timestamp = datetime.now().strftime("%Y-%m-%d-%H%M-%S")
    session_dir = f"{home_dir}/.rash/session-{timestamp}"
    channel.send(f"mkdir -p {session_dir}\n".encode())
    time.sleep(0.1)
    print(f"Session directory: {session_dir}")

    # --- SFTP for reading files efficiently ---
    sftp = ssh.open_sftp()

    return {'sftp':sftp,
            'session_dir': session_dir,
            'home_dir': home_dir,
            'server_prompt': server_prompt,
            'channel':channel,
            'ssh':ssh}

# --- Read remote file with wait ---
def read_remote_file(sftp, remote_path, timeout=10.0):
    """Wait for the remote file to exist, then read and return its contents."""
    start = time.time()
    time.sleep(0.05) # slight delay to let things sync up
    while True:
        try:
            with sftp.open(remote_path, "r") as f:
                return f.read().decode()
        except FileNotFoundError as exc:
            if time.time() - start > timeout:
                raise TimeoutError(f"File {remote_path} not found in {timeout:.1f} sec") from exc
            time.sleep(0.05)

SHELL_TESTS = [
    {"desc": "Check whoami",                        "cmd": "whoami",            "expected_exit": 0},
    {"desc": "Check working directory",             "cmd": "pwd",               "expected_exit": 0},
    {"desc": "Nonexist. file list (stderr test)",   "cmd": "ls /nonexistent",   "expected_exit": 2},

    ## --- test directory creation, cd, removal ---
    {"desc": "Create directory testdir",            "cmd": "mkdir -p testdir",  "expected_exit": 0},
    {"desc": "Change into testdir",                 "cmd": "cd testdir",        "expected_exit": 0},
    {"desc": "Print pwd inside testdir",            "cmd": "basename $(pwd)",   "expected_exit": 0,
                                                                                "expected_stdout":
                                                                                         "testdir"},
    {"desc": "Return to parent directory",          "cmd": "cd ..",             "expected_exit": 0},
    {"desc": "Remove testdir",                      "cmd": "rmdir testdir",     "expected_exit": 0},
    {"desc": "Print pwd after return",              "cmd": "pwd",               "expected_exit": 0},

    ## --- test creating and accessing a variable ---
    {"desc": "Export env variable MYVAR",           "cmd": 'export MYVAR="hello world"',
                                                                                "expected_exit": 0},
    {"desc": "Echo env variable MYVAR",             "cmd": "echo $MYVAR",       "expected_stdout":
                                                                                    "hello world",
                                                                                "expected_exit": 0},

    ## --- test redirection ---
    {"desc": "Stdout redirect test",                "cmd": 'echo "This is stdout" > out.txt',
                                                                                "expected_exit": 0},
    {"desc": "Stderr test",                         "cmd": 'echo "This is stderr" 1>&2',
                                                                                "expected_exit": 0},
    {"desc": "Read stdout file",                    "cmd": "cat out.txt",       "expected_stdout":
                                                                                "This is stdout",
                                                                                "expected_exit": 0},

    ## --- test exit status ---
    {"desc": "Command with failure exit status",
     "cmd": "grep 'needle' /dev/null",   "expected_exit": 1},
]


def interactive_loop(cmd_number, session_vars):
    """
    interactive_loop - take user text input, detect exit or send it to run_command 
    """
    # --- Interactive loop ---
    session = PromptSession(history=InMemoryHistory())
    print("\nEntering interactive mode. Type 'exit' or press CTRL-D to quit.\n")
    while True:
        try:
            user_cmd = session.prompt("> ")
        except EOFError:
            print("\n[EOF] CTRL-D received. Exiting.")
            break
        except KeyboardInterrupt:
            print("\nKeyboardInterrupt. Type 'exit' to quit.")
            continue

        if not user_cmd.strip():
            continue
        if user_cmd.strip().lower() in ("exit", "quit", "logout"):
            print("Exiting session...")
            break

        # Reuse run_command; no test expectations in interactive mode
        cmd_number = run_command(
            cmd_number,
            session_vars,
            command=user_cmd,
            test=False
        )

if __name__ == "__main__":
    main()
