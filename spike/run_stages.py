# Run bench_vm.py once per stage, each in a fresh process (NeMo's import leaves torch.load and
# other globals patched, which breaks later stages in the same process). Detached; logs to run.log.
import os, subprocess, sys
stages = os.environ.get("STAGES", "align,parakeet,sortformer")
env = {k: v for k, v in os.environ.items() if not k.startswith("BENCH_")}
script = " ; ".join(f"BENCH_STAGES={s} python /content/bench_vm.py" for s in stages.split(","))
p = subprocess.Popen(["bash", "-c", script], stdout=open("/content/bench/run.log", "a"),
                     stderr=open("/content/bench/run.err", "a"), env=env, start_new_session=True)
print("pid", p.pid, script)
