#!/usr/bin/env python3
"""Simulate the workshop's simulation load on the CHORAS instances.

Default: each group starts at a random moment within --stagger seconds, runs its
simulation, waits until it's done, "thinks" for a random --think time (changing
settings), and repeats until --duration minutes are over. --burst instead starts
one simulation per instance at the same moment (worst case).

Runs on the workspace and talks to the backends directly on 127.0.0.1 (no Nginx,
no login), so it loads the backends and the simulation containers, not Nginx.
Polls each run's status once per second, like the frontend does. Prints each
run's start and end and, every --report seconds, the runs still going; Ctrl-C
stops early with a summary so far. Standard library only.

    python3 load_test.py                      # user01-user15, 10 min, think 30-180 s
    python3 load_test.py --duration 45 --think 60,300
    python3 load_test.py --burst              # all at once, one run each
    python3 load_test.py --users user01,user02 --method sparrowpy
    python3 load_test.py --set-method misuka  # switch each first simulation to misuka first
    python3 load_test.py --dry-run            # only show what would start
    python3 load_test.py --selftest           # check the script against a stub backend

--method only picks simulations that already use that method. --set-method
switches each picked simulation to a method with that method's default settings,
as the frontend does on a method change (the example project in every instance
has a DE simulation, which runs on one core; misuka, sparrowpy and MoD-ART use
the full CPU limit).
Run `sudo docker stats` alongside to watch CPU and memory.
"""
import argparse
import json
import random
import re
import statistics
import threading
import time
import urllib.request

USERS_MAP = "/etc/nginx/conf.d/choras_users.conf"
DONE = {"Completed", "Error", "Cancelled"}


def backend_ports(users, map_file, base_port):
    """Port per user from the playbook's Nginx map, else base_port + NN of userNN."""
    try:
        text = open(map_file).read()
        ports = {u: int(p) for u, p in re.findall(r"^\s*(\S+)\s+127\.0\.0\.1:(\d+);", text, re.M)}
    except OSError:
        ports = {}
    return {u: ports.get(u, base_port + int(re.sub(r"\D", "", u) or 0)) for u in users}


def call(port, path, body=None, method=None):
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=None if body is None else json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method=method,
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def pick_simulation(port, method):
    """First simulation of the instance, optionally with the given method."""
    for model in call(port, "/projects/simulations"):
        for sim in model.get("simulations", []):
            if method is None or (sim.get("simulationMethod") or "").lower() == method.lower():
                return sim
    return None


def set_method(port, sim, name):
    """Switch a simulation to a method with its default settings, like the frontend's method dropdown."""
    types = [m["simulationType"] for m in call(port, "/simulation_settings")]
    match = [t for t in types if t.lower() == name.lower()]
    if not match:
        raise ValueError(f"unknown method {name}, available: {', '.join(types)}")
    options = call(port, f"/simulation_settings/{match[0]}")["options"]
    body = {
        "modelId": sim["modelId"], "name": sim["name"], "status": sim["status"],
        "hasBeenEdited": sim.get("hasBeenEdited") or False, "simulationMethod": match[0],
        "solverSettings": {"simulationSettings": {o["id"]: o["default"] for o in options}},
    }
    if sim.get("resourceType"):
        body["resourceType"] = sim["resourceType"]
    return call(port, f"/simulations/{sim['id']}", body, method="PUT")


def log(msg):
    print(f"{time.strftime('%H:%M:%S')}  {msg}", flush=True)


class Running:
    """Runs going right now (user -> [run id, status, percentage, start]) and the peak count."""

    def __init__(self):
        self.lock, self.active, self.peak = threading.Lock(), {}, 0

    def begin(self, user):
        with self.lock:
            self.active[user] = [None, "Starting", 0, time.monotonic()]
            self.peak = max(self.peak, len(self.active))

    def update(self, user, run):
        with self.lock:
            self.active[user][:3] = [run.get("id"), run.get("status"), run.get("percentage") or 0]

    def end(self, user):
        with self.lock:
            self.active.pop(user, None)

    def report(self):
        with self.lock:
            now = time.monotonic()
            items = [f"{u} {st} {pct}% {now - t0:.0f}s" for u, (_, st, pct, t0) in sorted(self.active.items())]
        log(f"{len(items)} running: " + ", ".join(items) if items else "0 running")


def run_one(user, port, sim, timeout, poll, running):
    t0 = time.monotonic()
    running.begin(user)
    try:
        run = call(port, "/simulations/run", {"simulationId": sim["id"]})
        running.update(user, run)
        status = run.get("status")
        while status not in DONE and time.monotonic() - t0 < timeout:
            time.sleep(poll)
            state = call(port, f"/simulations/run/{run['id']}/status")
            running.update(user, state)
            status = state.get("status")
        result = (status if status in DONE else "Timeout", time.monotonic() - t0)
    except Exception as exc:  # one failing instance must not stop the others
        result = (f"Failed: {exc}", time.monotonic() - t0)
    finally:
        running.end(user)
    log(f"{user} {result[0]} after {result[1]:.0f} s")
    return result


def run_user(user, port, sim, start, args, running, result):
    """--burst: one run at the start signal. Otherwise: stagger, then run/think until the end."""
    start.wait()
    runs = result[user] = []
    if args.burst:
        runs.append(run_one(user, port, sim, args.timeout, args.poll, running))
        return
    end = time.monotonic() + args.duration * 60
    time.sleep(random.uniform(0, args.stagger))
    while time.monotonic() < end:
        runs.append(run_one(user, port, sim, args.timeout, args.poll, running))
        time.sleep(random.uniform(*args.think))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--users", default=",".join(f"user{i:02d}" for i in range(1, 16)))
    ap.add_argument("--method", help="only simulations with this method, e.g. DE, sparrowpy")
    ap.add_argument("--set-method", help="switch each picked simulation to this method first, e.g. misuka")
    ap.add_argument("--map-file", default=USERS_MAP)
    ap.add_argument("--base-port", type=int, default=8100)
    ap.add_argument("--timeout", type=float, default=900, help="seconds per run before it counts as Timeout")
    ap.add_argument("--report", type=float, default=60, help="seconds between progress reports")
    ap.add_argument("--poll", type=float, default=1.0, help="seconds between status requests")
    ap.add_argument("--burst", action="store_true", help="all at once, one run each")
    ap.add_argument("--duration", type=float, default=10, help="minutes (not with --burst)")
    ap.add_argument("--think", default="30,180", help="min,max seconds between runs of a group")
    ap.add_argument("--stagger", type=float, default=60, help="seconds over which groups start")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()
    args.think = tuple(float(x) for x in args.think.split(","))

    users = [u.strip() for u in args.users.split(",") if u.strip()]
    ports = backend_ports(users, args.map_file, args.base_port)
    plan = {}
    for user in users:
        try:
            sim = pick_simulation(ports[user], args.method)
        except Exception as exc:
            print(f"{user} :{ports[user]}  backend not reachable: {exc}")
            continue
        if sim is None:
            print(f"{user} :{ports[user]}  no simulation" + (f" with method {args.method}" if args.method else ""))
            continue
        if args.set_method and not args.dry_run:
            try:
                sim = set_method(ports[user], sim, args.set_method)
            except Exception as exc:
                print(f"{user} :{ports[user]}  could not set method: {exc}")
                continue
        plan[user] = sim
        print(f"{user} :{ports[user]}  simulation {sim['id']} ({sim.get('simulationMethod')}) {sim.get('name', '')}")
    if args.dry_run or not plan:
        return 0 if plan else 1

    start, result, running = threading.Event(), {}, Running()
    threads = [threading.Thread(target=run_user, args=(u, ports[u], s, start, args, running, result), daemon=True)
               for u, s in plan.items()]
    for t in threads:
        t.start()
    mode = "burst" if args.burst else (f"{args.duration:g} min, think {args.think[0]:g}-{args.think[1]:g} s, "
                                       f"stagger {args.stagger:g} s")
    print(f"\nStarting {len(threads)} groups at {time.strftime('%H:%M:%S')} ({mode}) ...")
    start.set()
    next_report = time.monotonic() + args.report
    try:
        while any(t.is_alive() for t in threads):
            time.sleep(0.2)
            if time.monotonic() >= next_report:
                running.report()
                next_report += args.report
    except KeyboardInterrupt:
        print("\nStopped; runs still going in the backends continue there. Summary so far:")
        running.report()

    print()
    for user in plan:
        runs = list(result.get(user, []))
        done = [secs for status, secs in runs if status == "Completed"]
        other = sorted({status for status, _ in runs if status != "Completed"})
        median = f"median {statistics.median(done):5.0f} s" if done else ""
        print(f"{user}  {plan[user].get('simulationMethod'):<16} {len(done)}/{len(runs)} completed  {median}  {' '.join(other)}")
    runs = [r for rs in list(result.values()) for r in rs]
    ok = [secs for status, secs in runs if status == "Completed"]
    print(f"\nCompleted {len(ok)}/{len(runs)} runs, at most {running.peak} running at once", end="")
    if ok:
        print(f"; min {min(ok):.0f} s, median {statistics.median(ok):.0f} s, max {max(ok):.0f} s", end="")
    print()
    return 0 if runs and len(ok) == len(runs) else 1


def selftest():
    """Run the whole flow against two stub backends on free local ports."""
    import http.server
    import os
    import tempfile

    class Stub(http.server.BaseHTTPRequestHandler):
        polls = {}
        method = "DE"

        def log_message(self, *a):
            pass

        def reply(self, body):
            data = json.dumps(body).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path == "/projects/simulations":
                return self.reply([{"simulations": [{"id": 7, "modelId": 3, "simulationMethod": Stub.method,
                                                     "name": "Example", "status": "Completed"}]}])
            if self.path == "/simulation_settings":
                return self.reply([{"simulationType": "DE"}, {"simulationType": "misuka"}])
            if self.path == "/simulation_settings/misuka":
                return self.reply({"options": [{"id": "rays", "default": 1000}]})
            run_id = int(self.path.split("/")[3])
            Stub.polls[run_id] = Stub.polls.get(run_id, 0) + 1
            self.reply({"id": run_id, "status": "Completed" if Stub.polls[run_id] >= 2 else "InProgress"})

        def do_PUT(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            assert body["simulationMethod"] == "misuka" and body["modelId"] == 3, body
            assert body["solverSettings"] == {"simulationSettings": {"rays": 1000}}, body
            Stub.method = "misuka"
            self.reply({"id": 7, "simulationMethod": "misuka", "name": "Example"})

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            assert body == {"simulationId": 7}, body
            self.reply({"id": self.server.server_port, "status": "Queued"})

    servers = [http.server.ThreadingHTTPServer(("127.0.0.1", 0), Stub) for _ in range(2)]
    for s in servers:
        threading.Thread(target=s.serve_forever, daemon=True).start()
    with tempfile.NamedTemporaryFile("w", suffix=".conf", delete=False) as f:
        f.write("map $remote_user $choras_backend {\n    default \"\";\n")
        for i, s in enumerate(servers, 1):
            f.write(f"    user{i:02d} 127.0.0.1:{s.server_port};\n")
        f.write("}\n")
    try:
        assert backend_ports(["user01"], f.name, 8100) == {"user01": servers[0].server_port}
        assert backend_ports(["user07"], "/nonexistent", 8100) == {"user07": 8107}
        assert pick_simulation(servers[0].server_port, "de")["id"] == 7
        assert pick_simulation(servers[0].server_port, "sparrowpy") is None
        rc = main(["--users", "user01,user02", "--map-file", f.name, "--poll", "0.05", "--burst"])
        assert rc == 0, rc
        assert all(n >= 2 for n in Stub.polls.values()) and len(Stub.polls) == 2, Stub.polls
        # Session mode: 0.01 min with no think time gives each group several runs.
        rc = main(["--users", "user01,user02", "--map-file", f.name, "--poll", "0.05",
                   "--duration", "0.01", "--think", "0,0", "--stagger", "0"])
        assert rc == 0, rc
        rc = main(["--users", "user01", "--map-file", f.name, "--poll", "0.05", "--burst", "--set-method", "MISUKA"])
        assert rc == 0 and Stub.method == "misuka", (rc, Stub.method)
        assert main(["--users", "user01", "--map-file", f.name, "--burst", "--set-method", "nope"]) == 1
    finally:
        os.unlink(f.name)
        for s in servers:
            s.shutdown()
    print("selftest ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
