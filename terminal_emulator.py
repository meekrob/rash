#!/usr/bin/env python3
"""
terminal_emulator - light weight ssh interface using paramiko
"""
import sys
import time
import os
import logging
import getpass
import select
import paramiko

#HOST = 'login-ci.rc.colorado.edu'
HOST = 'riviera.colostate.edu'
#USERNAME = 'dcking@colostate.edu'
USERNAME = 'dking'
GETPASS_PROMPT = "Enter password with duo prompt (password, push) or (password, hardwareKey): "
#PASSWORD = getpass.getpass(GETPASS_PROMPT)
# Path to your private key (default: ~/.ssh/id_rsa)
private_key_path = os.path.expanduser("~/.ssh/id_rsa")

TRANSPORT = None

def interactive_shell(channel: paramiko.Channel):
    """Simple interactive loop to send user input and print server output."""
    print("Entering interactive shell (type 'exit' or Ctrl-D to quit)")
    try:
        while True:
            # Check if there is data from the server
            if channel.recv_ready():
                data = channel.recv(4096).decode("utf-8")
                if data:
                    print(data, end="", flush=True)

            # Check if there is input from the user
            if sys.stdin in select.select([sys.stdin], [], [], 0)[0]:
                user_input = sys.stdin.readline()
                if not user_input:  # EOF (Ctrl-D)
                    break
                if user_input.strip().lower() in ["exit", "quit"]:
                    break
                channel.send(user_input.encode("utf-8"))

            # If the channel is closed, exit
            if channel.closed or channel.exit_status_ready():
                break
    except KeyboardInterrupt:
        print("\nExiting interactive shell.")

def send_command(cmd: str, channel: paramiko.Channel, delay: float = 0.5) -> str:
    """
    send_command - send string command to provided channel, wait, and return output
    """
    if not channel.send_ready():
        raise RuntimeError("Channel not ready for sending commands.")
    cmd += '\n'
    channel.send(cmd.encode())
    time.sleep(delay)  # Give it a moment to respond
    output = ''
    while channel.recv_ready():
        output += channel.recv(1024).decode('utf-8')
    return output



try:
    # Setup transport (shared connection)
    transport = paramiko.Transport((HOST, 22))

    # --- Try key-based authentication first ---
    AUTHENTICATED = False
    try:
        pkey = paramiko.RSAKey.from_private_key_file(private_key_path)
        transport.connect(username=USERNAME, pkey=pkey)
        print(f"Authenticated with private key {private_key_path}", file=sys.stderr)
        AUTHENTICATED = True
    except IOError as ioerror:
        print(f"could not read key file: {private_key_path}", file=sys.stderr)
        AUTHENTICATED = False
    except paramiko.PasswordRequiredException as e:
        print(f"Passphrase required for key file: {e}", file=sys.stderr)
        AUTHENTICATED = False
    except paramiko.SSHException as e:
        print(f"Authenticated FAILED with private key {private_key_path}", file=sys.stderr)
        AUTHENTICATED = False
    finally:
        # --- Fall back to password authentication ---
        if not AUTHENTICATED:
            password = getpass.getpass(GETPASS_PROMPT)
            transport.connect(username=USERNAME, password=password)

    CHANNEL = None
    try:
        # Open a channel for executing commands
        CHANNEL = transport.open_session()
        print("get_pty()", end="", file=sys.stderr)
        CHANNEL.get_pty()
        print(file=sys.stderr)
        print("invoke_shell()", end="", file=sys.stderr)
        CHANNEL.invoke_shell()
        print(file=sys.stderr)


        # (Optional) read initial prompt or MOTD
        time.sleep(0.5)
        if CHANNEL.recv_ready():
            banner = CHANNEL.recv(4096).decode('utf-8')
            print(banner)
        interactive_shell(CHANNEL)

        RUN_TEST_COMMANDS = False
        if RUN_TEST_COMMANDS:
            # Now send multiple commands
            output1 = send_command('whoami', CHANNEL)
            print("Output 1:", output1, file=sys.stderr)

            output2 = send_command('pwd', CHANNEL)
            print("Output 2:", output2, file=sys.stderr)

            output3 = send_command('ls -l', CHANNEL)
            print("Output 3:", output3, file=sys.stderr)

    except paramiko.SSHException as e:
        print(f"SSH error: {e}")
        raise
    finally:
        if CHANNEL is not None:
            CHANNEL.close()

except paramiko.AuthenticationException as e:
    print(f"Authentication Failed: {e}", file=sys.stderr)
    #sys.exit(1)

except Exception as e:
    logging.exception(e)
    raise

finally:
    if transport is not None:
        transport.close()
    if CHANNEL is not None:
        CHANNEL.close()
