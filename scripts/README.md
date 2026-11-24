# Operations scripts

Scripts for running the workshop workspace. They work on the instances the
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
- **`--users`:** the logins to use (default `user01`–`user15`).

```bash
python3 ~/choras-playbook/scripts/load_test.py --dry-run                  # what would start
python3 ~/choras-playbook/scripts/load_test.py                            # 15 groups, 10 min
python3 ~/choras-playbook/scripts/load_test.py --set-method misuka --burst
python3 ~/choras-playbook/scripts/load_test.py --users user16,user17,user18,user19,user20
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
