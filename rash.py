#!/usr/bin/env python3
import paramiko
import time
from datetime import datetime
import os,sys
import getpass
from prompt_toolkit import PromptSession
from prompt_toolkit.history import InMemoryHistory
from typing import Any

import time

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


# --- Run command using source history-cmd# ---
def run_command(
    cmd_number:int,
    session_vars,
    command:str,
    test: bool = False,
    description: str|None =     None,
    expected_exit: int|None =   None,
    expected_stdout: str|None = None,
    expected_stderr: str|None = None,
):
    
    session_dir = session_vars['session_dir']
    channel = session_vars['channel']
    sftp = session_vars['sftp']

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

    # Stream output while running
    stream_command_output(sftp, stdout_file, stderr_file, status_file)

    # Wait until sentinel appears in the channel
    timeout=10.0
    command_timedout = False
    buffer = read_channel_with_timeout(channel, sentinel, timeout=timeout)
    if sentinel not in buffer:
        print(f"[WARNING] Command output exceeded {timeout}")
        command_timedout = True

    # Read outputs
    stdout_text = read_remote_file(sftp, stdout_file).strip()
    stderr_text = read_remote_file(sftp, stderr_file).strip()
    try:
        exit_status = int(read_remote_file(sftp, status_file).strip())
    except ValueError:
        exit_status = None

    duration = time.time() - start_time

    # Always print main outputs
    print(f"STDOUT[{len(stdout_text)} bytes]")
    print(f"STDERR[{len(stderr_text)} bytes]")
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


def connection(host:str, username:str, key_file:str|None) -> tuple[paramiko.Channel, paramiko.SSHClient]: 
    # --- SSH key authentication ---
    #key_file = os.path.expanduser("~/.ssh/id_rsa")
    if key_file is not None:
        key_file = os.path.expanduser(key_file)
    pkey = None
    if key_file is not None and os.path.exists(key_file):
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
    transport = ssh.get_transport()
    if transport is None:
        raise Exception("Ssh was unable to get_transport()")

    channel = transport.open_session()
    channel.get_pty()
    channel.invoke_shell()
    channel.settimeout(0.1)
    return channel, ssh


def initialize_session(channel:paramiko.Channel, ssh:paramiko.SSHClient) -> dict[str, Any]:

    # --- Detect server prompt ---
    time.sleep(0.2)
    channel.send('echo "$PS1"\n'.encode())
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
        except FileNotFoundError:
            if time.time() - start > timeout:
                raise TimeoutError(f"File {remote_path} did not appear within {timeout:.1f} sec")
            time.sleep(0.05)


SHELL_TESTS = [
    {"desc": "Check whoami",                        "cmd": "whoami",            "expected_exit": 0},
    {"desc": "Check working directory",             "cmd": "pwd",               "expected_exit": 0},
    {"desc": "Nonexist. file list (stderr test)",   "cmd": "ls /nonexistent",   "expected_exit": 2},

    ## --- test directory creation, cd, removal ---
    {"desc": "Create directory testdir",            "cmd": "mkdir -p testdir",  "expected_exit": 0},
    {"desc": "Change into testdir",                 "cmd": "cd testdir",        "expected_exit": 0},
    {"desc": "Print pwd inside testdir",            "cmd": "basename $(pwd)",   "expected_exit": 0,
                                                                                "expected_stdout": "testdir"},
    {"desc": "Return to parent directory",          "cmd": "cd ..",             "expected_exit": 0},
    {"desc": "Remove testdir",                      "cmd": "rmdir testdir",     "expected_exit": 0},
    {"desc": "Print pwd after return",              "cmd": "pwd",               "expected_exit": 0},

    ## --- test creating and accessing a variable --- 
    {"desc": "Export env variable MYVAR",           "cmd": 'export MYVAR="hello world"', "expected_exit": 0},
    {"desc": "Echo env variable MYVAR",             "cmd": "echo $MYVAR",       "expected_stdout": "hello world", 
                                                                                "expected_exit": 0},

    ## --- test redirection ---
    {"desc": "Stdout redirect test",                "cmd": 'echo "This is stdout" > out.txt', "expected_exit": 0},
    {"desc": "Stderr test",                         "cmd": 'echo "This is stderr" 1>&2', "expected_exit": 0},
    {"desc": "Read stdout file",                    "cmd": "cat out.txt",        "expected_stdout": "This is stdout", 
                                                                                 "expected_exit": 0},

    ## --- test exit status ---
    {"desc": "Command with failure exit status",    "cmd": "grep 'needle' /dev/null",   "expected_exit": 1},
]


def interactive_loop(cmd_number, session_vars):
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
            description="interactive",
            command=user_cmd,
            test=False
        )


def main():
    # --- Connection info ---
    host = "riviera.colostate.edu"
    username = "dking"
    channel,ssh = connection(host, username, "~/.ssh/id_rsa")
    session_vars = initialize_session(channel, ssh)

    # extract vars for session
    sftp = session_vars['sftp']
    session_dir = session_vars['session_dir']
    home_dir = session_vars['home_dir']
    server_prompt = session_vars['server_prompt']


    # --- TESTS of basic operation ---
    cmd_number = 1
    for shell_test in SHELL_TESTS:
        cmd_number = run_command(cmd_number, session_vars, 
                                 description=shell_test['desc'], 
                                 command=shell_test['cmd'], 
                                 expected_stdout=shell_test.get("expected_stdout"),
                                 expected_stderr=shell_test.get("expected_stderr"),
                                 expected_exit=shell_test.get("expected_exit"),
                                 test=True)
        

    # INTERACTIVE LOOP
    interactive_loop(cmd_number, session_vars)

    # --- Optional: check session directory size ---
    stdin, stdout, stderr = ssh.exec_command(f"du -sh {session_dir}")
    size_info = stdout.read().decode().strip()
    print(f"\nTotal session directory size: {size_info}")

    # --- Close SSH ---
    sftp.close()
    channel.close()
    ssh.close()

if __name__ == "__main__": main()
