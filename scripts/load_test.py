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
    python3 load_test.py --set-method random  # a random workshop method for every run
    python3 load_test.py --set-method misuka,sparrowpy   # random from this list for every run
    python3 load_test.py --material wood      # all surfaces to the first material matching "wood"
    python3 load_test.py --obj room.obj --source 2,2,1.5 --receiver 4,3,1.5   # upload a model first
    python3 load_test.py --dry-run            # only show what would start
    python3 load_test.py --selftest           # check the script against a stub backend

--method only picks simulations that already use that method. --set-method
switches each picked simulation to a method with that method's default settings,
as the frontend does on a method change (the example project in every instance
has a DE simulation, which runs on one core; misuka, sparrowpy and MoD-ART use
the full CPU limit). With several methods (comma-separated, or "random" for
DE, misuka, MoD-ART, sparrowpy and pyroomacoustics), every run of a group
switches to a random one of them first. --material assigns one material to all surfaces of the
picked simulation (the example uses "Acoustic plaster 68 mm thick", which is
very absorbent; less absorption means longer decays and longer runs).

--obj uploads a model into every instance first, the way the frontend does
(project "Load test", upload, geometry check, model, waiting for the geometry
pipeline, mesh), and creates a DE simulation on it with one source, one receiver
and "Acoustic plaster" on all surfaces; --set-method and --material then apply
to that simulation. The setup runs for all instances in parallel.
Run `sudo docker stats` alongside to watch CPU and memory.
"""
import argparse
import json
import os
import random
import re
import statistics
import threading
import time
import urllib.request
import uuid

USERS_MAP = "/etc/nginx/conf.d/choras_users.conf"
DONE = {"Completed", "Error", "Cancelled"}
WORKSHOP_METHODS = ["DE", "misuka", "MoDART", "sparrowpy", "Pyroomacoustics"]


def backend_ports(users, map_file, base_port):
    """Port per user from the playbook's Nginx map, else base_port + NN of userNN."""
    try:
        text = open(map_file).read()
        ports = {u: int(p) for u, p in re.findall(r"^\s*(\S+)\s+127\.0\.0\.1:(\d+);", text, re.M)}
    except OSError:
        ports = {}
    return {u: ports.get(u, base_port + int(re.sub(r"\D", "", u) or 0)) for u in users}


def get_text(port, path):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=30) as resp:
        return resp.read().decode()


def upload(port, path, file_path):
    """POST a file as multipart/form-data field "file", like the frontend's upload."""
    boundary = uuid.uuid4().hex
    with open(file_path, "rb") as fh:
        content = fh.read()
    body = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
            f'filename="{os.path.basename(file_path)}"\r\nContent-Type: application/octet-stream\r\n\r\n'
            ).encode() + content + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=body, method="POST",
                                 headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.load(resp)


def setup_obj(port, obj_path, source, receiver, timeout):
    """Upload a model like the frontend, wait for the geometry pipeline, mesh it, create a DE simulation."""
    name = os.path.splitext(os.path.basename(obj_path))[0]
    project = call(port, "/projects", {"name": f"Load test {name}", "description": "Created by load_test.py",
                                        "group": "Load test"})
    slot = call(port, "/files")
    uploaded = upload(port, f"/files?slot={slot['id']}", obj_path)
    call(port, f"/files?slot={slot['id']}", method="DELETE")
    geometry = call(port, f"/geometryCheck?fileUploadId={uploaded['id']}", method="POST")
    model = call(port, "/models", {"name": name, "projectId": project["id"], "sourceFileId": geometry["outputModelId"]})
    t0 = time.monotonic()
    while True:  # the inspect/repair pipeline runs in the backend's Celery worker
        model = call(port, f"/models/{model['id']}")
        if model.get("geometryStatus") in (None, "Completed", "Failed"):
            break
        if time.monotonic() - t0 > timeout:
            raise TimeoutError(f"geometry pipeline still {model.get('geometryStatus')} after {timeout:.0f} s")
        time.sleep(2)
    call(port, f"/meshes?modelId={model['id']}", method="PATCH")
    stem = os.path.splitext(call(port, f"/files/{model['outputFileId']}")["fileName"])[0]
    # Surfaces are the .geo's physical surfaces, named by their .3dm object ids (the frontend's layer keys).
    layers = re.findall(r'^Physical Surface\("([^"]+)"\)', get_text(port, f"/uploads/{stem}.geo"), re.M)
    if not layers:
        raise ValueError(f"no physical surfaces in {stem}.geo")
    materials = call(port, "/materials")
    material = next((m for m in materials if "plaster" in m["name"].lower()), materials[0])
    options = call(port, "/simulation_settings/DE")["options"]
    sim = call(port, "/simulations", {
        "modelId": model["id"], "name": "Load test", "description": "Created by load_test.py",
        "simulationMethod": "DE", "layerIdByMaterialId": {layer: material["id"] for layer in layers},
        "solverSettings": {"simulationSettings": {o["id"]: o["default"] for o in options}},
        "sources": [], "receivers": [],
    })
    point = lambda label, xyz: {"id": str(uuid.uuid4()), "label": label, "orderNumber": 1,
                                "x": xyz[0], "y": xyz[1], "z": xyz[2], "isValid": True}
    sim = call(port, f"/simulations/{sim['id']}", {
        "modelId": model["id"], "name": sim["name"], "status": sim["status"], "hasBeenEdited": True,
        "sources": [point("Source 1", source)], "receivers": [point("Receiver 1", receiver)],
    }, method="PUT")
    print(f"  model {model['id']} ({model.get('geometryStatus')}), {len(layers)} surfaces, simulation {sim['id']}")
    return sim


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


def set_material(port, sim, name):
    """Assign the first material whose name contains `name` to every surface of the simulation."""
    materials = call(port, "/materials")
    match = [m for m in materials if name.lower() in m["name"].lower()]
    if not match:
        raise ValueError(f"no material matching {name!r}, available: {'; '.join(m['name'] for m in materials)}")
    layers = sim.get("layerIdByMaterialId") or {}
    if not layers:
        raise ValueError("simulation has no surfaces with materials to change")
    body = {"modelId": sim["modelId"], "name": sim["name"], "status": sim["status"],
            "hasBeenEdited": True, "layerIdByMaterialId": {layer: match[0]["id"] for layer in layers}}
    updated = call(port, f"/simulations/{sim['id']}", body, method="PUT")
    print(f"  {len(layers)} surfaces -> {match[0]['name']}")
    return updated


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
        result = (status if status in DONE else "Timeout", time.monotonic() - t0, sim.get("simulationMethod"))
    except Exception as exc:  # one failing instance must not stop the others
        result = (f"Failed: {exc}", time.monotonic() - t0, sim.get("simulationMethod"))
    finally:
        running.end(user)
    log(f"{user} {result[2]} {result[0]} after {result[1]:.0f} s")
    return result


def run_user(user, port, sim, start, args, running, result):
    """--burst: one run at the start signal. Otherwise: stagger, then run/think until the end."""
    start.wait()
    runs = result[user] = []

    def one():
        nonlocal sim
        if len(args.methods) > 1:  # a group trying another method before this run
            try:
                sim = set_method(port, sim, random.choice(args.methods))
            except Exception as exc:
                return (f"Failed: set method: {exc}", 0.0, None)
        return run_one(user, port, sim, args.timeout, args.poll, running)

    if args.burst:
        runs.append(one())
        return
    end = time.monotonic() + args.duration * 60
    time.sleep(random.uniform(0, args.stagger))
    while time.monotonic() < end:
        runs.append(one())
        time.sleep(random.uniform(*args.think))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--users", default=",".join(f"user{i:02d}" for i in range(1, 16)))
    ap.add_argument("--method", help="only simulations with this method, e.g. DE, sparrowpy")
    ap.add_argument("--set-method", help="method for the picked simulations, e.g. misuka; several "
                                         "comma-separated or 'random' (workshop methods): a random one per run")
    ap.add_argument("--material", help="assign the first material matching this name to all surfaces, e.g. wood")
    ap.add_argument("--obj", help="upload this model into every instance first and simulate it")
    ap.add_argument("--source", help="x,y,z of the source in the --obj model")
    ap.add_argument("--receiver", help="x,y,z of the receiver in the --obj model")
    ap.add_argument("--setup-timeout", type=float, default=900, help="seconds to wait for the geometry pipeline")
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
    if args.set_method == "random":
        args.methods = WORKSHOP_METHODS
    else:
        args.methods = [m.strip() for m in (args.set_method or "").split(",") if m.strip()]
    if args.obj:
        if not (args.source and args.receiver):
            ap.error("--obj needs --source and --receiver")
        if not os.path.isfile(args.obj):
            ap.error(f"no such file: {args.obj}")
        args.source, args.receiver = ([float(v) for v in p.split(",")] for p in (args.source, args.receiver))

    users = [u.strip() for u in args.users.split(",") if u.strip()]
    ports = backend_ports(users, args.map_file, args.base_port)
    created = {}
    if args.obj and not args.dry_run:
        print(f"Uploading {args.obj} into {len(users)} instances ...")

        def make(user):
            try:
                created[user] = setup_obj(ports[user], args.obj, args.source, args.receiver, args.setup_timeout)
            except Exception as exc:
                created[user] = exc
        workers = [threading.Thread(target=make, args=(u,), daemon=True) for u in users]
        for w in workers:
            w.start()
        for w in workers:
            w.join()
    plan = {}
    for user in users:
        try:
            sim = created[user] if args.obj and not args.dry_run else pick_simulation(ports[user], args.method)
            if isinstance(sim, Exception):
                print(f"{user} :{ports[user]}  model setup failed: {sim}")
                continue
        except Exception as exc:
            print(f"{user} :{ports[user]}  backend not reachable: {exc}")
            continue
        if sim is None:
            print(f"{user} :{ports[user]}  no simulation" + (f" with method {args.method}" if args.method else ""))
            continue
        if args.methods and not args.dry_run:
            try:
                sim = set_method(ports[user], sim, random.choice(args.methods))
            except Exception as exc:
                print(f"{user} :{ports[user]}  could not set method: {exc}")
                continue
        if args.material and not args.dry_run:
            try:
                sim = set_material(ports[user], sim, args.material)
            except Exception as exc:
                print(f"{user} :{ports[user]}  could not set material: {exc}")
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
        done = [secs for status, secs, _ in runs if status == "Completed"]
        other = sorted({status for status, _, _ in runs if status != "Completed"})
        median = f"median {statistics.median(done):5.0f} s" if done else ""
        methods = ",".join(sorted({m for _, _, m in runs if m})) or str(plan[user].get("simulationMethod"))
        print(f"{user}  {methods:<16} {len(done)}/{len(runs)} completed  {median}  {' '.join(other)}")
    runs = [r for rs in list(result.values()) for r in rs]
    by_method = {}
    for status, secs, method in runs:
        by_method.setdefault(method, []).append((status, secs))
    if len(by_method) > 1:
        print()
        for method, rs in sorted(by_method.items(), key=lambda kv: str(kv[0])):
            ok_m = [secs for status, secs in rs if status == "Completed"]
            stats = f"median {statistics.median(ok_m):.0f} s, max {max(ok_m):.0f} s" if ok_m else ""
            print(f"  {str(method):<16} {len(ok_m)}/{len(rs)} completed  {stats}")
    ok = [secs for status, secs, _ in runs if status == "Completed"]
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
        calls = []
        switches = []
        method = "DE"
        layers = {"a": 4, "b": 4}

        def log_message(self, *a):
            pass

        def reply(self, body):
            data = json.dumps(body).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            routes = {"/files": {"id": "slot1", "uploadUrl": "x"}, "/models/30": {"id": 30, "geometryStatus": "Completed",
                      "outputFileId": 40}, "/files/40": {"id": 40, "fileName": "Room_ab.3dm"}}
            if self.path in routes:
                return self.reply(routes[self.path])
            if self.path == "/uploads/Room_ab.geo":
                data = b'Physical Surface("s1") = { 1 };\nPhysical Surface("s2") = { 2 };\n'
                self.send_response(200)
                self.end_headers()
                return self.wfile.write(data)
            if self.path == "/projects/simulations":
                return self.reply([{"simulations": [{"id": 7, "modelId": 3, "simulationMethod": Stub.method,
                                                     "name": "Example", "status": "Completed",
                                                     "layerIdByMaterialId": Stub.layers}]}])
            if self.path == "/materials":
                return self.reply([{"id": 4, "name": "Acoustic plaster 68 mm thick"}, {"id": 9, "name": "Wood"}])
            if self.path == "/simulation_settings":
                return self.reply([{"simulationType": "DE"}, {"simulationType": "misuka"}])
            if self.path in ("/simulation_settings/misuka", "/simulation_settings/DE"):
                return self.reply({"options": [{"id": "rays", "default": 1000}]})
            run_id = int(self.path.split("/")[3])
            Stub.polls[run_id] = Stub.polls.get(run_id, 0) + 1
            self.reply({"id": run_id, "status": "Completed" if Stub.polls[run_id] >= 2 else "InProgress"})

        def do_DELETE(self):
            self.reply({})

        def do_PATCH(self):
            assert self.path == "/meshes?modelId=30", self.path
            Stub.calls.append("mesh")
            self.reply({"id": 1})

        def do_PUT(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if "sources" in body:
                assert body["sources"][0]["x"] == 1.0 and body["receivers"][0]["z"] == 6.0, body
                Stub.calls.append("points")
                return self.reply({"id": 50, "modelId": 30, "simulationMethod": "DE", "name": "Load test",
                                   "status": "Created", "layerIdByMaterialId": {"s1": 4, "s2": 4}})
            if "layerIdByMaterialId" in body:
                assert body["layerIdByMaterialId"] == {"a": 9, "b": 9}, body
                Stub.layers = body["layerIdByMaterialId"]
                return self.reply({"id": 7, "simulationMethod": Stub.method, "name": "Example",
                                   "layerIdByMaterialId": Stub.layers})
            assert body["simulationMethod"] in ("misuka", "DE") and body["modelId"] in (3, 30), body
            assert body["solverSettings"] == {"simulationSettings": {"rays": 1000}}, body
            Stub.method = body["simulationMethod"]
            Stub.switches.append(body["simulationMethod"])
            self.reply({"id": 7, "modelId": 3, "simulationMethod": body["simulationMethod"], "name": "Example",
                        "status": "Completed", "layerIdByMaterialId": Stub.layers})

        def do_POST(self):
            raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            if self.path == "/files?slot=slot1":
                assert b'name="file"; filename="room.obj"' in raw and b"v 0 0 0" in raw, raw[:200]
                return self.reply({"id": 11})
            if self.path == "/projects":
                return self.reply({"id": 20})
            if self.path == "/geometryCheck?fileUploadId=11":
                return self.reply({"outputModelId": 21})
            if self.path == "/models":
                assert json.loads(raw)["sourceFileId"] == 21
                return self.reply({"id": 30, "geometryStatus": "Pending"})
            if self.path == "/simulations":
                body = json.loads(raw)
                assert body["layerIdByMaterialId"] == {"s1": 4, "s2": 4}, body
                assert body["solverSettings"] == {"simulationSettings": {"rays": 1000}}, body
                return self.reply({"id": 50, "name": "Load test", "status": "Created"})
            body = json.loads(raw)
            assert body["simulationId"] in (7, 50), body
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
        rc = main(["--users", "user01", "--map-file", f.name, "--poll", "0.05", "--burst", "--material", "WOOD"])
        assert rc == 0 and Stub.layers == {"a": 9, "b": 9}, (rc, Stub.layers)
        assert main(["--users", "user01", "--map-file", f.name, "--burst", "--material", "marble"]) == 1
        obj = os.path.join(os.path.dirname(f.name), "room.obj")
        with open(obj, "w") as fh:
            fh.write("v 0 0 0\n")
        try:
            rc = main(["--users", "user01", "--map-file", f.name, "--poll", "0.05", "--burst",
                       "--obj", obj, "--source", "1,2,3", "--receiver", "4,5,6"])
        finally:
            os.unlink(obj)
        assert rc == 0 and Stub.calls == ["mesh", "points"], (rc, Stub.calls)
        # Several methods: one switch before the start plus one before every run.
        Stub.switches.clear()
        rc = main(["--users", "user01", "--map-file", f.name, "--poll", "0.05", "--set-method", "misuka,DE",
                   "--duration", "0.01", "--think", "0,0", "--stagger", "0"])
        assert rc == 0 and len(Stub.switches) >= 3 and set(Stub.switches) <= {"misuka", "DE"}, (rc, Stub.switches)
    finally:
        os.unlink(f.name)
        for s in servers:
            s.shutdown()
    print("selftest ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
