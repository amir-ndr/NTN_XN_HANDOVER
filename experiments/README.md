# Xn handover experiments (NTN)

Measurement harness for the Xn handover testbed: injects NTN propagation delay
derived from real Starlink ephemeris, runs handovers, and produces the figures.

This is the detailed reference for each script. Start with the repository's
[main README](../README.md): it covers installing Open5GS and the modified
UERANSIM ([UERANSIM_XN](https://github.com/amir-ndr/UERANSIM_XN)), bringing the
testbed up, and reproducing the paper's results.

---

## Layout

```
experiments/
├── lib/
│   ├── ntn_geometry.py      elevation -> slant range -> propagation delay (skyfield + TLE)
│   ├── ntn_constellation.py visibility schedule: all satellites over a window
│   ├── ho_policy.py         selection policies + offline decision simulation
│   ├── netem.sh             delay injection per 5G interface
│   └── testbed.py           handover control, interruption measurement
├── run/
│   ├── e1_baseline.py       E1: baseline, no injected delay
│   ├── e2_elevation_sweep.py E2: latency/interruption vs elevation angle
│   ├── e3_policy_compare.py E3: satellite-selection policy comparison (offline)
│   └── e4_orchestrator.py   E4: live replay of real pass sequences on the testbed
├── plots/
│   ├── plot_style.py        shared styling
│   ├── plot_geometry.py     the orbital geometry behind the delays
│   ├── plot_baseline.py     E1 figures
│   ├── plot_elevation.py    E2 figures
│   ├── plot_policy.py       E3 figures
│   ├── plot_orchestrator.py E4 figures
│   └── paper_tables.py      prints the paper's Tables III and IV and the E1 baseline
├── manuscript/              LaTeX source of the paper
├── results/
│   ├── geometry/            pass profile, sweep points, orbit references
│   ├── raw/                 one CSV per experiment run
│   │   ├── repeatability/   E4 window 0 run a second time
│   │   └── realtime_check/  E4 at real-time speed (--speed 1), kept out of the averages
│   └── summary/             aggregated tables
└── figures/                 PNGs for the manuscript
```

---

## Prerequisites

The testbed must already be running, with the UE in its namespace. From the
UERANSIM_XN folder:

```bash
sudo ./config/xn3/setup-netns.sh              # once per boot
sudo ./build/nr-gnb -c config/xn3/gnb1-netns.yaml
sudo ip netns exec ue1 ./build/nr-ue -c config/xn3/ue1-netns.yaml
sudo ./build/nr-gnb -c config/xn3/gnb2-netns.yaml
```

Confirm the data plane is real before measuring anything:

```bash
sudo ip netns exec ue1 ping -I uesimtun0 -c 3 10.45.0.1     # expect 1-6 ms
```

Under 0.1 ms means traffic is short-circuiting inside the kernel instead of
traversing GTP, and every user-plane number would be meaningless.

All `run/` scripts must be run as **root** (they use `ip netns exec` and `tc`).

---

## Step 1 — Geometry

```bash
python3 lib/ntn_geometry.py
python3 plots/plot_geometry.py
```

Selects a real Starlink pass over Montreal (45.5017, -73.5673) and converts
elevation angle to propagation delay. Writes `results/geometry/`.

Two decisions worth knowing when citing these numbers:

- **Passes are computed at the TLE epoch**, not at wall-clock "now". A TLE is
  only accurate for a few days around its epoch; `starlink.tle` is from late
  April 2026, so propagating it to today would give fictional geometry.
- **Only satellites in the 500-600 km shell are considered.** The TLE set also
  contains lowered and deorbiting satellites (some under 400 km); one of those
  would understate a representative LEO link.

Current selection: STARLINK-3075, culmination 87 deg, slant range
544-1805 km, one-way delay **1.82-6.02 ms** (RTT 3.63-12.04 ms).

---

## Step 2 — E1, baseline

```bash
sudo -E python3 run/e1_baseline.py --runs 50
python3 plots/plot_baseline.py
```

Handovers back and forth between gnb1 and gnb2 with no injected delay, with a
100 pps ping running across each switch. This is the reference every delayed
configuration is compared against.

Figures: `fig_e1_cdf.png`, `fig_e1_breakdown.png`, `fig_e1_interruption.png`.

---

## Step 3 — E2, elevation sweep

```bash
sudo -E python3 run/e2_elevation_sweep.py --scenario transparent  --runs 20
sudo -E python3 run/e2_elevation_sweep.py --scenario regenerative --runs 20
sudo -E python3 run/e2_elevation_sweep.py --scenario isl          --runs 20
python3 plots/plot_elevation.py
```

At each elevation point the corresponding one-way delay is injected on the link
that the chosen NTN architecture makes long:

| Scenario | Where the gNB is | Long link | Injected on | Inflates |
|---|---|---|---|---|
| `transparent` | ground (satellite is a bent-pipe relay) | service + feeder, as the radio hop | `radio` | `ue_arrival_ms` |
| `regenerative` | on the satellite | feeder link to the core | `n2` + `n3` | `psw_ms` |
| `isl` | on two different satellites | inter-satellite Xn | `xn` | `ue_arrival_ms` (prep) |

The same delay costs a different amount depending on where it sits: Xn delay is
paid once, before the UE moves, whereas feeder delay is paid during the path
switch — *after* the UE has already left the source, which is exactly when the
user plane is down. Quantifying that asymmetry is the point of E2.

Figures: `fig_e2_<scenario>_latency.png`, `fig_e2_<scenario>_interruption.png`,
plus `fig_e2_comparison.png` and `fig_e2_sensitivity.png` once more than one
scenario has data. The sensitivity figure gives the slope in ms of handover
latency per ms of link RTT — the coefficient an optimization model needs.

---

## Step 4 — E3, satellite-selection policy comparison (offline)

```bash
python3 lib/ntn_constellation.py          # visibility schedule over a window
python3 run/e3_policy_compare.py --window 900
python3 plots/plot_policy.py
```

No testbed involved: this replays the real constellation geometry and works out
what each policy would decide. Its output is also the handover plan the live
orchestrator will later execute.

| Policy | Behaviour | Literature expectation |
|---|---|---|
| `max_elevation` | always serve on the highest-elevation satellite | best link, frequent handovers |
| `elevation_threshold` | hold until elevation drops below a threshold | fewer handovers, can switch late |
| `max_service_time` | hold until the serving satellite is about to set | fewest handovers |

Measured over a 15-minute Starlink sequence over Montreal, reproducing exactly
that trade-off:

```
max_elevation         8 HO (0.53/min)  late 0   mean elev 52.7 deg   mean delay 2.58 ms
elevation_threshold   2 HO (0.13/min)  late 1   mean elev 25.9 deg   mean delay 3.90 ms
max_service_time      2 HO (0.13/min)  late 0   mean elev 24.0 deg   mean delay 3.99 ms
```

`max_elevation` buys 2x the link quality for 4x the handovers.
`elevation_threshold` produced a **late handover** -- the serving satellite was
lost before the switch fired -- which is the known weakness of a fixed elevation
threshold: two passes can share an elevation while having very different time
left before outage. `max_service_time` avoids it at the same handover count.

### Trigger semantics -- how to describe this in writing

3GPP defines **no elevation-angle event**. The NTN-specific Rel-17 conditional
handover triggers are **CondEvent D1** (distance to the serving reference
location above one threshold AND distance to the candidate below another) and
**CondEvent T1** (time based). Since elevation and slant range are monotonically
related, an elevation threshold is equivalent to a D1-style distance condition,
and `max_service_time` is a T1-style time condition.

Note also that this is **elevation-triggered handover, not CHO**: the network
decides and sends a command. In true 3GPP CHO the *UE* holds candidate
configurations and evaluates the conditions itself. Calling this CHO would be
inaccurate.

### Stability controls

All policies apply, and the flags expose, the controls that make a trigger
usable in practice:

- `--hysteresis` a candidate must beat the serving cell by a margin, not merely
  beat it; without this, near-equal elevations cause ping-pong handovers
- `--ttt` the condition must hold continuously before firing
- `--min-dwell` floor on time between consecutive handovers
- `--lead-time` fire this long before the serving satellite becomes unusable.
  **Set this from the handover latency measured in E2**, plus margin -- it is the
  direct link between the cost measurements and the policy.

## Step 5 — E4, live replay of real pass sequences

```bash
for p in max_elevation elevation_threshold max_service_time; do
  for off in 0 900 1800 2700; do
    sudo -E python3 run/e4_orchestrator.py --policy $p --window 900 --offset $off --tag w$off
  done
done
python3 plots/plot_orchestrator.py
python3 plots/paper_tables.py
```

Each run replays 15 minutes of the real constellation schedule (from Step 4)
against the live testbed: each gNB's service-link delay follows the satellite it
currently represents, and a real Xn handover fires when the policy says so. Only
gnb1 and gnb2 are used; after a handover the freed gNB takes the role of the next
satellite. Geometry runs on an accelerated clock (`--speed 10` by default), the
handovers themselves in real time. `--offset` picks independent 15-minute
stretches, so the four windows per policy are separate samples. `--dry-run`
prints the handover plan without touching the testbed.

`plot_orchestrator.py` reads every `results/raw/e4_<policy>*.csv`, so keep extra
runs (a repeat, a different speed) in a subfolder. That is why
`realtime_check/` and `repeatability/` are separate.

Figures: `fig_e4_timeline.png`, `fig_e4_cost_vs_elev.png`, `fig_e4_frontier.png`,
`fig_e4_summary.png`.

---

## Delay injection

```bash
sudo ./lib/netem.sh init
sudo ./lib/netem.sh set radio 5.985      # one-way ms
sudo ./lib/netem.sh set feeder 3.275     # n2 + n3 together
sudo ./lib/netem.sh show
sudo ./lib/netem.sh verify               # measure the resulting RTTs
sudo ./lib/netem.sh clear
```

Links: `radio` (veth into the namespace), `xn` (TCP 38422), `n2` (SCTP 38412),
`n3` (UDP 2152), `feeder` (= n2 + n3), `all`.

netem shapes egress only, so each link is delayed at **both ends** with the
one-way value; the round trip comes out at 2x naturally. `jitter` is
deliberately not used — it reorders packets, which propagation delay does not
do, and would corrupt the interruption measurement.

The sweep scripts clear the shaping on exit, including on Ctrl-C.

---

## Measured quantities

Each row in `results/raw/*.csv` combines the gNB's own timing with a ping
measurement taken across the same handover:

| Column | Source | Meaning |
|---|---|---|
| `ue_arrival_ms` | target gNB | Xn request received -> UE completed its move |
| `psw_ms` | target gNB | Path Switch Request -> Acknowledge (AMF/SMF/UPF) |
| `total_ms` | target gNB | whole procedure at the target |
| `gap_packets` / `gap_ms` | ping | longest unbroken run of lost packets = the outage |
| `ping_lost` | ping | total loss over the capture window |

`gap_ms` rather than `ping_lost` is the interruption figure: a handover shows up
as one continuous run of missing sequence numbers, whereas total loss could also
include unrelated scattered drops.

Resolution is set by the ping interval (10 ms by default), so an outage smaller
than one interval reads as 0-1 packets. Lower `--ping-interval` if the
interruption gets small enough to need finer resolution.

---

## Caveats

- **Slice SST is currently inert.** It is carried through the Xn message and
  written to the CSV, but nothing branches on it, so sweeping it would produce
  identical results. It becomes a real parameter only if slice-differentiated
  handling is added.
- **Handovers are CLI-triggered.** All cells report `dbm = -1`, so there is no
  path-loss model and therefore no measurement-event-driven mobility.
- **One elevation angle per run.** The service link (UE->satellite) and the
  feeder link (satellite->gateway) physically have their own geometries; treating
  them as equal is the standard first-order simplification. Run the sweep per
  link where they need to differ.
- **Loopback baseline.** With no injection, Xn and N2/N3 delays are ~0-1 ms
  because everything shares a host. `psw_ms` is genuine core processing and is
  the most directly reusable value.
