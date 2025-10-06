#!/usr/bin/env python3
import sys
import time
import os
import getpass
import select
import paramiko

#host = 'login-ci.rc.colorado.edu'
host = 'riviera.colostate.edu'
#username = 'dcking@colostate.edu'
username = 'dking'
#password = getpass.getpass("Enter password with duo prompt (password, push) or (password, hardwareKey): ")
# Path to your private key (default: ~/.ssh/id_rsa)
private_key_path = os.path.expanduser("~/.ssh/id_rsa")

transport = None

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
    transport = paramiko.Transport((host, 22))
    
        # --- Try key-based authentication first ---
    try:
        pkey = paramiko.RSAKey.from_private_key_file(private_key_path)
        transport.connect(username=username, pkey=pkey)
        print(f"Authenticated with private key {private_key_path}", file=sys.stderr)
    except Exception as key_error:
        print(f"Key authentication failed: {key_error}", file=sys.stderr)

        # --- Fall back to password authentication ---
        password = getpass.getpass("Enter password with duo prompt (password, push) or (password, hardwareKey): ")
        transport.connect(username=username, password=password)

    

    channel = None
    try:
        # Open a channel for executing commands
        channel = transport.open_session()
        print("get_pty()", end="", file=sys.stderr)
        channel.get_pty()
        print(file=sys.stderr)
        print("invoke_shell()", end="", file=sys.stderr)
        channel.invoke_shell()
        print(file=sys.stderr)
        

        # (Optional) read initial prompt or MOTD
        time.sleep(0.5)
        if channel.recv_ready():
            banner = channel.recv(4096).decode('utf-8')
            print(banner)
        interactive_shell(channel)
        
        """ 
        # Now send multiple commands
        output1 = send_command('whoami', channel)
        print("Output 1:", output1, file=sys.stderr)

        output2 = send_command('pwd', channel)
        print("Output 2:", output2, file=sys.stderr)

        output3 = send_command('ls -l', channel)
        print("Output 3:", output3, file=sys.stderr) """

 
        
            
        
    except paramiko.SSHException as e:
        print(f"SSH error: {e}")
        raise
    finally:
        if channel is not None:
            channel.close()

except paramiko.AuthenticationException as e:
    print(f"Authentication Failed: {e}", file=sys.stderr)
    #sys.exit(1)

except Exception as e:
    print(f"Unexpected error: {e}")

finally:
    if transport is not None: transport.close()
    if channel is not None: channel.close()