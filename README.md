# Measuring the Cost of Xn Handover in Non-Terrestrial Networks

An emulation testbed and the experiments behind the paper *"Measuring the Cost of
Xn Handover in Non-Terrestrial Networks: An Emulation Study Driven by Real LEO
Ephemeris"* (Amir Naderi).

Real 3GPP Xn handovers are executed between software gNBs while propagation
delays computed from **real Starlink ephemeris** are injected on individual 5G
interfaces. Each handover is timed by the target gNB, and its user-plane
interruption is measured with a probe through the GTP tunnel.

**What the experiments show**

- **Where the delay sits decides whether it causes downtime.** Delay on the
  inter-satellite Xn link adds handover latency (+0.46 ms per ms of RTT) but
  essentially no interruption (−0.01 ms/ms), because it is paid before the UE
  leaves the source cell. Feeder-link and service-link delay turn almost directly
  into downtime (+0.80 and +1.24 ms/ms).
- **The policy with the fewest handovers picks the most expensive ones.** Over real
  pass sequences, a maximum-elevation policy makes 7× more handovers than a
  maximum-service-time policy, but each costs half as much (9.55 vs 19.47 ms). The
  7× count advantage shrinks to 3.4× in cumulative interruption.

All data from the paper is included in [`experiments/results/`](experiments/results/),
and one command re-prints every number in the paper from it
([section 6](#6-reproduce-the-papers-numbers-from-the-included-data)).

---

## Contents

1. [Repository layout](#1-repository-layout)
2. [Requirements](#2-requirements)
3. [Install: the modified UERANSIM first, then Open5GS](#3-install-the-modified-ueransim-first-then-open5gs)
4. [Bring the testbed up](#4-bring-the-testbed-up)
5. [Run the experiments (E1–E4)](#5-run-the-experiments-e1e4)
6. [Reproduce the paper's numbers from the included data](#6-reproduce-the-papers-numbers-from-the-included-data)
7. [Which file produces which result](#7-which-file-produces-which-result)
8. [Optional: the dispatcher](#8-optional-the-dispatcher)
9. [Limitations](#9-limitations)
10. [Credits](#10-credits)

---

## 1. Repository layout

```
.
├── starlink.tle            Starlink TLE snapshot (late April 2026) used for all geometry
├── requirements.txt        Python packages
├── experiments/
│   ├── lib/                geometry (Skyfield + TLE), policies, delay injection, testbed control
│   ├── run/                E1–E4 experiment runners
│   ├── plots/              figures, and paper_tables.py (prints the paper's numbers)
│   ├── results/
│   │   ├── geometry/       pass profile, elevation sweep points, constellation schedule
│   │   ├── raw/            one CSV per experiment run (the paper's data)
│   │   └── summary/        E3 policy summaries
│   ├── figures/            generated figures
│   ├── manuscript/         LaTeX source of the paper
│   └── README.md           detailed reference for every script and option
└── dispatcher/             optional NF-chain selection service (not used in the paper)
```

The 5G software itself is **not** in this repository: the testbed runs on
[Open5GS](https://open5gs.org/) and on **UERANSIM_XN**, a version of UERANSIM
extended with Xn handover (next section).

---

## 2. Requirements

- **Ubuntu** with **root** access (developed on Ubuntu 24.04, ARM64): the
  experiments create a network namespace and shape traffic with `tc`.
- **UERANSIM_XN** and **Open5GS**, installed as described below.
- **Python 3.8+** with Skyfield, NumPy and Matplotlib:

  ```bash
  pip install -r requirements.txt
  ```

- `iproute2` (for `ip` and `tc`) and the `sch_netem` kernel module, both standard
  on Ubuntu.

---

## 3. Install: the modified UERANSIM first, then Open5GS

### 3.1 Clone and build UERANSIM_XN

Upstream UERANSIM has no handover. This testbed needs
**[UERANSIM_XN](https://github.com/amir-ndr/UERANSIM_XN)**, a modified UERANSIM
that adds a 3GPP-ordered Xn handover (Xn preparation → `RRCReconfiguration` with
`reconfigurationWithSync` → NGAP Path Switch). **Clone it first.** The
experiments expect it at `~/UERANSIM_XN`; set `UERANSIM_DIR` to use another path.

```bash
sudo apt install -y make gcc g++ libsctp-dev lksctp-tools iproute2
sudo snap install cmake --classic

git clone https://github.com/amir-ndr/UERANSIM_XN.git ~/UERANSIM_XN
cd ~/UERANSIM_XN
```

**Set where the target gNB writes its handover timings, then build.** For every
handover, the target gNB appends one row (`sst, ue_arrival_ms, psw_ms, total_ms`)
to a CSV file whose path is fixed in `src/gnb/ngap/pathswitch.cpp`. The
experiments read that file, so point it to `/tmp/xn_ho_latency.csv` (the
experiments' default) before building:

```bash
sed -i 's|/home/amirndr/5g-lab/xn_ho_latency.csv|/tmp/xn_ho_latency.csv|' src/gnb/ngap/pathswitch.cpp
grep -n xn_ho_latency src/gnb/ngap/pathswitch.cpp     # should now show /tmp/xn_ho_latency.csv
make
ls build/                                             # nr-gnb  nr-ue  nr-cli ...
```

If you choose another path, export it as `XN_HO_CSV` before running the
experiments. If the path does not match, every handover fails with a timeout
waiting for its timing row.

The [UERANSIM_XN README](https://github.com/amir-ndr/UERANSIM_XN) explains the
handover and has a two-gNB quick start that is a good first test.

### 3.2 Install Open5GS

Clone and build Open5GS yourself, or install the Ubuntu package. The official
guide has the current steps:
<https://open5gs.org/open5gs/docs/guide/01-quickstart/> (package) and
<https://open5gs.org/open5gs/docs/guide/02-building-open5gs-from-sources/> (source).

The quickest route is the package:

```bash
sudo add-apt-repository ppa:open5gs/latest
sudo apt update
sudo apt install open5gs
```

**AMF configuration.** All gNBs connect to one AMF at `127.0.0.5:38412` with PLMN
999/70, TAC 1 and slice SST 1, which are the Open5GS defaults. A complete working
`amf:` section is in `UERANSIM_XN/config/xn3/open5gs-amf.yaml`; compare it with
`/etc/open5gs/amf.yaml`, then restart the core:

```bash
sudo systemctl restart open5gs-nrfd open5gs-scpd open5gs-amfd open5gs-smfd \
    open5gs-upfd open5gs-ausfd open5gs-udmd open5gs-udrd open5gs-pcfd open5gs-bsfd
```

**Subscriber.** Add the UE used by the testbed (IMSI, key and OPc from
`UERANSIM_XN/config/xn3/ue1-netns.yaml`), either in the Open5GS WebUI or with the
`open5gs-dbctl` script from the Open5GS source tree (`misc/db/open5gs-dbctl`):

```bash
open5gs-dbctl add 999700000000001 \
    465B5CE8B199B49FAA5F0A2EE238A6BC E8ED289DEBA952E4283B54E88E6183CA
```

---

## 4. Bring the testbed up

The experiments use the topology in `UERANSIM_XN/config/xn3/`: one AMF, up to
three gNBs (E1–E4 use gnb1 and gnb2) and **one UE in its own network namespace**.

The namespace is essential. If the UE shares the host's namespace, its IP address
(`10.45.0.x`) is local to the host, so the kernel delivers downlink packets
directly and they never pass through the UPF, the GTP tunnel and the gNB. Every
user-plane measurement would then be meaningless while appearing to work.

Run each command in its own terminal, **in this order**, from `~/UERANSIM_XN`.
Every simulated cell has the same signal strength, so starting gnb2 *after* the
UE has registered on gnb1 makes the starting cell deterministic.

```bash
cd ~/UERANSIM_XN
sudo ./config/xn3/setup-netns.sh                                  # once per boot: namespace "ue1" + veth pair

# Terminal 1 -- gnb1. Wait for "NG Setup procedure is successful".
sudo ./build/nr-gnb -c config/xn3/gnb1-netns.yaml

# Terminal 2 -- the UE, inside the namespace. Wait for the PDU session / uesimtun0.
sudo ip netns exec ue1 ./build/nr-ue -c config/xn3/ue1-netns.yaml

# Terminal 3 -- gnb2.
sudo ./build/nr-gnb -c config/xn3/gnb2-netns.yaml

# Terminal 4 (optional) -- gnb3.
sudo ./build/nr-gnb -c config/xn3/gnb3-netns.yaml
```

**Check the data plane before measuring anything:**

```bash
sudo ip netns exec ue1 ping -I uesimtun0 -c 3 10.45.0.1     # expect 1-6 ms
```

A round-trip time below 0.1 ms means traffic is short-circuiting inside the
kernel instead of going through GTP. Always pass `-I uesimtun0`, and make sure no
`nr-ue` is running outside the namespace.

**Optional quick handover test:**

```bash
./build/nr-cli UERANSIM-gnb-999-70-1 -e ue-list                 # note the ue-id
./build/nr-cli UERANSIM-gnb-999-70-1 -e "xn-handover <ue-id> 127.0.0.2"
tail -1 /tmp/xn_ho_latency.csv                                  # one new timing row
```

---

## 5. Run the experiments (E1–E4)

All commands run from the `experiments/` folder. Runners that touch the testbed
need root; use `sudo -E` so that `UERANSIM_DIR` and `XN_HO_CSV` are passed
through if you set them. Each runner checks the testbed first and writes one CSV
to `results/raw/`. E2 and E4 remove all injected delay when they exit (also on
Ctrl-C); E1 clears any leftover delay before it starts.

```bash
cd experiments
```

### Geometry (no testbed needed)

```bash
python3 lib/ntn_geometry.py          # selects the reference pass, elevation -> delay points
python3 lib/ntn_constellation.py     # visibility schedule of the whole constellation
python3 plots/plot_geometry.py
```

Positions are computed with SGP4 (Skyfield) from `starlink.tle` for a ground
station in Montreal (45.5017° N, 73.5673° W), **at the TLE's epoch**, since a TLE
is only accurate for a few days around it. Only satellites in the 500–600 km
shell are used. Reference pass: STARLINK-3075, 87° culmination, slant range
544–1805 km, one-way delay **1.82–6.02 ms**.

### E1: baseline, no injected delay

```bash
sudo -E python3 run/e1_baseline.py --runs 50                # 50 handovers gnb1 <-> gnb2
# ... run E2 ...
sudo -E python3 run/e1_baseline.py --runs 20 --tag after    # repeat at the end of the session: drift check
python3 plots/plot_baseline.py
```

### E2: elevation sweep for three NTN architectures

```bash
sudo -E python3 run/e2_elevation_sweep.py --scenario transparent  --runs 20
sudo -E python3 run/e2_elevation_sweep.py --scenario regenerative --runs 20
sudo -E python3 run/e2_elevation_sweep.py --scenario isl          --runs 20
python3 plots/plot_elevation.py
```

At each of the 7 elevation points (20 handovers each, 140 per scenario), the
corresponding one-way delay is injected on the link that the architecture makes
long:

| Scenario | Where the gNB is | Delayed link |
|---|---|---|
| `transparent` | on the ground (bent-pipe satellite) | radio link: service + feeder (UE ↔ gNB) |
| `regenerative` | on the satellite | feeder link to the core (N2 + N3) |
| `isl` | on two different satellites | inter-satellite link (Xn) |

Delay is added with `tc netem` at **both ends** of each link, so the round trip
comes out at twice the one-way value. No jitter is used: it reorders packets,
which propagation delay does not, and it would corrupt the interruption
measurement. See [`experiments/README.md`](experiments/README.md#delay-injection)
for the delay tool (`lib/netem.sh`).

### E3: policy comparison, offline (no testbed needed)

```bash
python3 run/e3_policy_compare.py --window 900
python3 plots/plot_policy.py
```

Replays 15 minutes of the real constellation and records what each
satellite-selection policy decides: `max_elevation`, `elevation_threshold` (25°)
and `max_service_time`. All use hysteresis 5°, time-to-trigger 5 s, minimum dwell
20 s and lead time 15 s (`--hysteresis`, `--ttt`, `--min-dwell`, `--lead-time`).

### E4: live replay of real pass sequences

```bash
for p in max_elevation elevation_threshold max_service_time; do
  for off in 0 900 1800 2700; do
    sudo -E python3 run/e4_orchestrator.py --policy $p --window 900 --offset $off --tag w$off
  done
done
python3 plots/plot_orchestrator.py
```

Each run drives the testbed through a 15-minute stretch of real Starlink
geometry: each gNB's service-link delay follows the satellite it currently
represents, and a real Xn handover fires when the policy decides. Only two gNBs
are needed; after each handover the freed gNB becomes the next satellite. The
geometry clock is accelerated 10× (`--speed`); the handovers themselves run in
real time. The four offsets give four independent windows per policy.
`--dry-run` prints the plan without touching the testbed.

`plot_orchestrator.py` reads every `results/raw/e4_<policy>*.csv`. Keep any extra
runs (a repeat, a different `--speed`) in a subfolder, as `results/raw/repeatability/`
and `results/raw/realtime_check/` are, or they will be counted as more windows.

### Experimental controls used in the paper

- **Fixed handover cadence.** E1 and E2 fire one handover every 6 s (`--period`).
  At 2.2 s between handovers the path-switch time nearly doubles, because the
  core does not settle between procedures.
- **Probe rate.** The interruption probe runs every 2 ms in E1/E2 and every 1 ms
  in E4 (`--ping-interval`). Measurement resolution is one probe interval
  (≈1.2 ms undelayed, 5–8 ms with injected delay, as pacing slows down).
- **Drift check.** E1 is repeated at the end of the session (`--tag after`). In
  the paper the median path-switch time moved from 3.897 to 4.087 ms.

---

## 6. Reproduce the paper's numbers from the included data

The CSVs from the paper's runs are included, so you can check every reported
number without running the testbed:

```bash
cd experiments
python3 plots/paper_tables.py
```

It prints, from `results/raw/`:

```
E1 baseline, no injected delay: n = 50 handovers
  UE arrival   median  0.831   mean  0.869 +- 0.234   p95  1.296 ms
  Path switch  median  3.897   mean  4.021 +- 0.564   p95  4.418 ms
  Total        median  4.744   mean  4.891 +- 0.664   p95  5.384 ms
  Interruption median  1.239 ms
  Drift check: path-switch median 3.897 ms before, 4.087 ms after the session (n = 20)

Table III: sensitivity to injected link RTT (ms per ms)
  Scenario            n  UE arrival  Path switch   Total  Interruption
  Regenerative      140       +0.06        +1.36   +1.42         +0.80
  Transparent       140       +1.16        +0.35   +1.50         +1.24
  Inter-satellite   140       +0.48        -0.03   +0.46         -0.01

Table IV: measured policy cost, mean +- SD over the windows
  Policy             windows HO/window  cost per HO (ms)  cum. outage (ms) mean link (ms)
  Max. elevation           4      10.5      9.55 +- 1.82      97.5 +- 16.7           2.15
  Elev. threshold          4       1.5     19.21 +- 1.97      26.4 +- 12.2           5.22
  Max. service time        4       1.5     19.47 +- 1.08      29.1 +- 16.7           5.33
  Repeat of window 0 (cumulative handover cost, ms):
    Max. elevation       78.0 vs   78.5
    Elev. threshold      37.0 vs   36.5
    Max. service time    38.8 vs   48.4
```

(It also prints the E3 policy summary.) The definitions behind each column are at
the top of [`experiments/plots/paper_tables.py`](experiments/plots/paper_tables.py):
interruption is the number of contiguous missing probes times the probe spacing
actually achieved, and Table III's coefficients are least-squares slopes over all
140 handovers of each scenario.

To regenerate the figures from the same data, run the four `plots/plot_*.py`
scripts listed in the next section.

---

## 7. Which file produces which result

| Paper | Produced by | Output |
|---|---|---|
| Fig. 1 (delay vs elevation) | `lib/ntn_geometry.py`, `plots/plot_geometry.py` | `figures/fig_geo_elevation.png` |
| Sec. VI-A (baseline) | `run/e1_baseline.py`, `plots/paper_tables.py` | `results/raw/e1_baseline.csv` |
| Table III, Fig. 2 | `run/e2_elevation_sweep.py`, `plots/plot_elevation.py`, `plots/paper_tables.py` | `results/raw/e2_<scenario>.csv`, `figures/fig_e2_comparison.png` |
| Sec. VI-C (policy behaviour) | `run/e3_policy_compare.py`, `plots/plot_policy.py` | `results/summary/policy_*.csv`, `figures/fig_e3_*.png` |
| Table IV, Figs. 3–5 | `run/e4_orchestrator.py`, `plots/plot_orchestrator.py`, `plots/paper_tables.py` | `results/raw/e4_<policy>_w<offset>.csv`, `figures/fig_e4_frontier.png` (Fig. 3), `fig_e4_timeline.png` (Fig. 4), `fig_e4_cost_vs_elev.png` (Fig. 5) |

All paths are relative to `experiments/`. Every column of every CSV is described
in [`experiments/README.md`](experiments/README.md#measured-quantities).

---

## 8. Optional: the dispatcher

`dispatcher/` is a small add-on that is **not used** in the paper's experiments.
UERANSIM_XN can call an external service while it prepares a handover: if a gNB's
config sets `dispatcherAddress` (and optionally `dispatcherPort`, default 9000),
the target gNB sends it a JSON `PathSwitchRequest` before acknowledging the Xn
request and logs the reply.

- `nf_chain.py` simulates one AMF → SMF → UPF chain as an M/M/1 queue that
  answers probes with its current backlog.
- `dispatcher.py` listens on `127.0.0.1:9000`, probes the chains, picks one at
  random, and returns the choice (`selectedAmf`).
- `start_all.sh` starts three chains (service rates 200, 100 and 50 requests/s,
  on ports 9101–9103) and the dispatcher:

  ```bash
  ./dispatcher/start_all.sh        # start it before the target gNB
  ```

The selection is informational: the handover always uses the AMF the gNB is
already connected to. Because the dispatcher round trip happens inside the
target's handover preparation, leave `dispatcherAddress` unset when measuring
handover latency, as in the paper.

---

## 9. Limitations

- **No physical layer.** No random access, timing advance, Doppler, fading or
  beams. Absolute interruption times are therefore lower bounds: they cover
  signalling and core processing only.
- **Network-triggered handover**, decided from geometry. It is not 3GPP
  conditional handover, where the UE evaluates the conditions itself.
- **Exact geometry knowledge** is assumed; ephemeris staleness and GNSS error are
  not modelled. Delay is constant per link during each handover.
- **No data forwarding** or SN Status Transfer, so the handover is not lossless;
  the short user-plane gap is the quantity being measured.
- **Interruption resolution** is one probe interval (≈1.2 ms undelayed, 5–8 ms
  with injected delay).

See Section VIII of the paper for the full discussion.

---

## 10. Credits

- **Testbed, experiments and paper:** Amir Naderi.
- **[UERANSIM](https://github.com/aligungr/UERANSIM)** by Ali Güngör, extended
  with Xn handover in [UERANSIM_XN](https://github.com/amir-ndr/UERANSIM_XN).
- **[Open5GS](https://open5gs.org/)** for the 5G core.
- **[Skyfield](https://rhodesmill.org/skyfield/)** by Brandon Rhodes for SGP4
  orbit propagation.
- **Starlink orbital elements:** `starlink.tle`, a two-line-element snapshot of
  the Starlink constellation from late April 2026.
