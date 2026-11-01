import subprocess
print(subprocess.run("echo RUNNING=$(pgrep -fc '[b]ench_vm.py'); cat /content/bench/run.log; tail -c 300 /content/bench/run.err | grep -iE 'error|traceback' ", shell=True, capture_output=True, text=True).stdout)
