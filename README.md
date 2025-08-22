# RASH - a GUI/Command line hybrid terminal emulator

This project is one that emulates an SSH terminal by passing commands through a python layer and interpreting the results to save state and add GUI 
features.
Since an interactive shell is running in the python layer, the user is actually "logged on" and passing string commands to their server.  
That session is active with its own environmental variables, functions, and history.

## Details

### Command execution strategy

Using the python `paramiko` library, there are several types of ssh connections and functions available.  However, in order to both save state in a 
persistent session *AND* isolate stdout, stderr, and the exit status, I used the following approach:

Each user command is saved in a history folder in the users server home directory and produces the following files per command:
1. command_file (labeled as history-#)
2. stdout_file
3. stderr_file
4. exit_status_file

The actual command sent to the server is wrapped in 

`source command_file > stdout_file 2> stderr_file && echo $? > exit_status_file`

and the three generated files are read by a faster sftp session. The information is stored and displayed for the user.

