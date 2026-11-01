# Launch bench_vm.py as a detached process on the VM (exec clients drop after ~60 s of silence).
import os, subprocess
# `colab exec --env` values persist in the kernel, so drop stale BENCH_* unless set in this file
env = {k: v for k, v in os.environ.items() if not k.startswith("BENCH_")}
env.update({k: v for k, v in (l.split("=", 1) for l in open("/content/bench/env").read().split()) } if os.path.exists("/content/bench/env") else {})
p = subprocess.Popen(["python", "/content/bench_vm.py"], stdout=open("/content/bench/run.log", "a"),
                     stderr=open("/content/bench/run.err", "a"), env=env, start_new_session=True)
print("pid", p.pid)
