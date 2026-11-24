# Operations scripts

Scripts for running and watching the workshop workspace and printing the login cards. They work on the instances the
playbook created (`/opt/choras/users/<login>`, one Docker Compose project
`choras_<login>` each) and run on the workspace itself:

```bash
git clone https://github.com/choras-org/2026_community_day.git ~/choras-playbook   # once
git -C ~/choras-playbook pull                                                       # later
```

`sudo -i` doesn't work on SRC workspaces, so prefix single commands with `sudo`
(it asks for the SRAM TOTP code). Run long jobs in `tmux`, so a dropped SSH
connection doesn't stop them.

## load_test.py

Simulates the workshop's simulation load. Talks to the backends directly on
`127.0.0.1:81NN` (no Nginx, no login), so it doesn't need `sudo`. Python
standard library only.

- **Default (session mode):** each group starts at a random moment within
  `--stagger` (60 s), runs its first simulation, waits until it's done, "thinks"
  for a random `--think` time (30–180 s) and repeats for `--duration` (10 min).
- **`--burst`:** one simulation per instance at the same moment (worst case).
- **`--set-method misuka`:** first switches each instance's first simulation to
  that method with its default settings, like the frontend's method dropdown.
  DE runs on one core; misuka, sparrowpy and MoD-ART use the full CPU limit, so
  use one of those to test the CPU load.
- **`--set-method random`** (or a list such as `misuka,sparrowpy`): every run of
  a group first switches to a random method from the list, like groups trying
  different methods; `random` means DE, misuka, MoD-ART, sparrowpy and
  pyroomacoustics. The summary then also lists runs and durations per method.
- **`--setting MoDART:ppsm=5`** (repeatable): uses that value instead of the
  method's default whenever the test switches to that method with `--set-method`.
  The value is read as JSON where possible (`5`, `0.5`, `true`), otherwise as text.
  An unknown setting name stops that group with a list of the method's settings.
- **`--material wood`:** assigns the first material whose name contains "wood" to all surfaces of the simulation. The example uses "Acoustic plaster 68 mm thick", which absorbs a lot; less absorption means longer decays and longer runs.
- **`--obj room.obj --source x,y,z --receiver x,y,z`:** uploads that model into
  every instance first, the same way the frontend does (project "Load test",
  upload, geometry check, model, waiting for the geometry pipeline, mesh), and
  creates a DE simulation on it with one source, one receiver and "Acoustic
  plaster" on all surfaces. `--set-method` and `--material` then apply to it. The
  setup runs for all instances in parallel; `--setup-timeout` (900 s) limits the
  wait for the geometry pipeline. The model and project stay in the instances.
  `models/Atlas8340_empty.obj` is the empty Atlas 8.340 lecture hall (TU/e).
  CHORAS turns the OBJ's Y-up into Z-up (CHORAS X = OBJ x, Y = −OBJ z, Z = OBJ y),
  so in CHORAS coordinates the hall is 8.94 × 17.24 m with the floor at Z = 0 and
  6.2 m walls; source 4.5,3,1.5 and receiver 4.5,10,1.2 are inside it.
  `models/Atlas8340_empty_nocolumns.obj` is the same hall without the columns
  (34 instead of 84 faces, same dimensions, same positions work).
- **`--users`:** the logins to use (default `user01`–`user15`).

```bash
python3 ~/choras-playbook/scripts/load_test.py --dry-run                  # what would start
python3 ~/choras-playbook/scripts/load_test.py                            # 15 groups, 10 min
python3 ~/choras-playbook/scripts/load_test.py --set-method misuka --burst
python3 ~/choras-playbook/scripts/load_test.py --set-method random --material wood
python3 ~/choras-playbook/scripts/load_test.py --set-method random --setting MoDART:ppsm=5 --setting MoDART:rays=200
python3 ~/choras-playbook/scripts/load_test.py --users user16,user17,user18,user19,user20
python3 ~/choras-playbook/scripts/load_test.py --obj ~/choras-playbook/scripts/models/Atlas8340_empty.obj \
    --source 4.5,3,1.5 --receiver 4.5,10,1.2 --set-method misuka --material wood
python3 ~/choras-playbook/scripts/load_test.py --selftest                 # check against stub backends
```

At the end it prints, per group, the completed runs and the median duration,
and overall the minimum, median and maximum duration and the peak number of
simulations running at once. Watch `sudo docker stats` alongside.

The test changes the instances: every run appears in the group's run history,
and `--set-method` leaves the simulation on the new method. Test on the spare
logins, or reset the instances afterwards (`reset_user.sh`).

## export_results.sh

Bundles each instance's results into `/opt/choras/exports/<login>-<date>.tar.gz`:

- `uploads/`: models, results JSON, `.wav`, `.xlsx` (file names contain internal IDs)
- `database.sql`: a `pg_dump` of the instance's database (projects, simulations),
  so the projects can be restored into another CHORAS instance

```bash
sudo ~/choras-playbook/scripts/export_results.sh                # all instances
sudo ~/choras-playbook/scripts/export_results.sh user01 user02  # only these
scp '<sram-user>@<workspace>:/opt/choras/exports/*' .           # from your own machine
```

The instance's database must be running. A failed instance is reported and the
others are still exported; the script then exits with 1. The archives belong to
the user who ran `sudo`, so `scp` can read them. Set `CHORAS_EXPORT_DIR` for
another target directory.

## reset_user.sh

Wipes instances back to a fresh start: stops their running simulations,
deletes their database and `uploads/`, and starts the stack again. On startup
the backend recreates the database and the example project. The login, the
password and the instance's secrets stay the same, so a group's login card
keeps working.

```bash
sudo ~/choras-playbook/scripts/reset_user.sh user16 user17    # asks for confirmation
sudo ~/choras-playbook/scripts/reset_user.sh --yes user16     # no question
```

Everything of that instance is gone afterwards; run `export_results.sh` first if
the data matters. Other instances aren't touched (simulation containers are
matched by `choras-<login>-`, so resetting `user16` leaves `user160` alone).

## credentials_pdf.py

Makes an A4 PDF of login cards for printing: 10 cards per page (2 columns × 5
rows) with the workspace URL, login and password (monospaced) and the note that
only one person per group logs in. Dashed cut lines run from edge to edge, so
the cards can be cut apart with scissors in straight lines. Python standard
library only.

The credentials file belongs to root. Either generate the PDF on the workspace
and copy it to your own machine:

```bash
# on the workspace
sudo cat /root/choras_credentials.txt | python3 ~/choras-playbook/scripts/credentials_pdf.py - ~/logins.pdf
# on your own machine
scp '<sram-user>@<workspace>:logins.pdf' ~/Desktop/
```

or copy the credentials and generate the PDF locally, then delete the
plaintext copies:

```bash
# on the workspace
sudo cat /root/choras_credentials.txt > ~/credentials.txt && chmod 600 ~/credentials.txt
# on your own machine, in this repo
scp '<sram-user>@<workspace>:credentials.txt' /tmp/credentials.txt
python3 scripts/credentials_pdf.py /tmp/credentials.txt ~/Desktop/logins.pdf
rm /tmp/credentials.txt
ssh '<sram-user>@<workspace>' 'rm ~/credentials.txt'
```

`--users user01,user02` prints only some logins (e.g. the groups without the
spares); `--title`, `--url` and `--note` change the texts. `python3
credentials_pdf.py --selftest` checks the script. The PDF contains the
passwords, so treat it like the printed cards.

## status.sh

One snapshot of the server: load average against the number of cores (with a
warning above it), memory, disk use of `/`, instances whose backend or database
isn't running, and the simulations running right now with their CPU and memory.

```bash
sudo ~/choras-playbook/scripts/status.sh
watch -n 10 sudo ~/choras-playbook/scripts/status.sh    # refresh every 10 s, Ctrl-C to stop
```

A simulation can use up to `choras_sim_cpus` × 100 % CPU (400 % with 4 cores).
