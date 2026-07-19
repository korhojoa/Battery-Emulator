# Upstreaming notes

Tracking list for work on this fork that is intended to go upstream to
dalathegreat/Battery-Emulator. Nothing below has been filed or opened yet.
Findings reference sections in
[improvement-survey-2026-07.md](improvement-survey-2026-07.md).

## Branches ready to become PRs

All fix branches below are based on upstream main `d4569a85` and pass the
native test suite (67 tests). Created July 2026, not yet filed:

- [ ] **`fix/geely-sea-cell-index-bounds`** — bounds-check 0x142 cell index
  (survey 1.1)
- [ ] **`fix/solax-cell-rescale-div0`** — guard design-span division + clamp
  rescale (survey 1.2, inverter side)
- [ ] **`fix/custom-bms-user-limit-guards`** — 7 drivers apply user limits
  only when nonzero (survey 1.2, battery side)
- [ ] **`fix/kangoo-soc-estimate-clamp`** — clamp SOC estimate to 0–100 %
  (survey 1.4)
- [ ] **`fix/pylon-ferroamp-status-byte`** — fix inverted 0x425X status byte
  in PYLON-CAN + FERROAMP-CAN, replace stale PYLON-BATTERY comment
  (survey 1.3)
- [ ] **`fix/logging-loop-hardening`** — WDT registration + unconditional
  yield (survey 1.5, downgraded item)

- [ ] **`test/canlog-replay-fixtures`** — CAN log converter
  (`test/can_log_based/convert_can_log.py`), nine real-log base fixtures
  (Dacia Spring, Jaguar I-PACE, Kia eNiro, Nissan LEAF 62 kWh, Renault
  Zoe1/Zoe2, Tesla Model 3, Volvo SPA, Ford Mach-E; replay coverage
  5 → 13 battery types), and the timed-replay harness change (virtual
  `millis()` from log timestamps + driver transmit-scheduler ticks).
  All 76 native tests pass. Self-contained; no production-code changes.
- [ ] **`fix/foxess-can-still-alive`** — one-line FoxESS fix: renew
  `CAN_battery_still_alive` on 0x1873 BMS_PackData. Without it any FoxESS
  install raises `EVENT_CAN_BATTERY_MISSING` ~60 s after startup.
- [ ] **`docs/improvement-survey`** (this branch) — survey document + ADR
  scaffolding. Optional to upstream; the ADR proposal (`docs/adr/`) could
  go as its own small PR to start the conversation.

## Bugs to file as issues (or fix-PRs directly)

- [ ] Geely SEA out-of-bounds cell-voltage write + unclamped
  `number_of_cells` (`GEELY-SEA-BATTERY.cpp`, frame 0x142; survey 1.1).
  **Re-verified July 2026 against d4569a8:** unchanged, no structural gate;
  real Zeekr traffic sends indices 1–110 only, so latent (malformed-input)
  severity — file as robustness fix. Prefer fixing via a shared
  bounds-checked `Battery::set_cell_voltage()` helper.
- [ ] Solax cell rescale divide-by-zero / negative-wrap
  (`SOLAX-CAN.cpp:69-77`; survey 1.2; introduced in upstream PR #2151).
  **Re-verified July 2026, severity UPGRADED:** divisor is the *design*
  cell-limit span; 7 of 9 custom-BMS drivers copy web-UI cell limits into
  it unguarded and the NVS default is 0 → those batteries + Solax with
  unset/equal limits = div-by-zero panic **reboot loop**. Two-part fix:
  guard the division, and make unguarded drivers apply user limits only
  when nonzero (Pylon/Relion already do) or add web-UI validation.
- [x] ~~Pylon current-sign comment/code mismatch~~ (`PYLON-BATTERY.cpp:18`;
  survey 1.3). **Verified July 2026 against real Dyness Stack 100 logs from
  upstream issue #2082: the code is correct** (wire raw > 30000 = charging,
  matching the datalayer convention unnegated); the "invert the sign"
  comment is stale copy-paste. Downgraded to a comment cleanup — fold into
  any Pylon-touching PR rather than filing an issue.
- [ ] **NEW: Pylon inverter-side 0x425X status byte inverted**
  (`PYLON-CAN.cpp:138-142`): maps negative `reported_current_dA` to
  "Charge" and positive to "Discharge" — backwards vs. both the datalayer
  convention (`types.cpp:24`) and the wire convention verified above. The
  current field itself is transmitted correctly, so only the reported
  charge/discharge *state* is wrong. `FERROAMP-CAN.cpp:58-66` has the
  identical inverted block (copy-paste propagation) — file as one issue
  covering both.
- [ ] Renault Kangoo SOC estimate underflow/overflow
  (`RENAULT-KANGOO-BATTERY.cpp`; survey 1.4; introduced in #2104).
  **Re-verified July 2026 with corrections:** input is pack dV (not cell
  mV) and the path is gated behind the opt-in
  `user_selected_use_estimated_SOC`; within that gate it's real — 355 % SOC
  at startup while `voltage_dV` is 0, wrap below 300 V pack. Fix: clamp to
  0–10000 and skip while voltage unpopulated.
- [ ] `logging_loop` hardening (`Software.cpp:139-147`; survey 1.5 —
  **corrected July 2026**: the busy-wait is unreachable in current code
  since the task only exists when an SD flag is true and the write paths
  block in a 10 ms ring-buffer wait). Downgraded to cheap insurance:
  `delay(1)` at loop end + WDT registration, guarding against a future
  runtime toggle making the spin real (which would starve `mqtt_loop`,
  priority 2 on the same core). File as a minor hardening PR, not a bug.
- [ ] FoxESS `update_values()` clobbers `max_design_voltage_dV` from a
  per-pack-count preset table, overriding BMS-reported limits (0x1872);
  the #1664 log (1 pack, 395 V) spuriously flags overvoltage. Found while
  building fixtures.

## Design discussions to raise (issue or ADR-style proposal)

- [ ] Event severity vs. contactor policy: over/under-voltage, cell
  events, welded contactor, isolation fault are WARNING-only and never
  open contactors (survey §2). Supporting evidence upstream: #581, #1534.
- [ ] Staleness policy: stale battery values forwarded to inverter for up
  to ~70 s after CAN loss; no per-field invalidation (survey §2; upstream
  #630 was closed without a generic implementation).
- [ ] Cross-core `datalayer` synchronization (survey §3).
- [ ] Fault-flap accumulator reset (`timeSpentInFaultedMode`, survey §2).

## Test-harness follow-ups (not yet started)

- [ ] Per-fixture pack-limit flags so custom-BMS bench logs (Thunderstruck
  #2144, RJXZS #1764) can pass `base` despite not matching the assumed
  90s-NMC limits in `canlog_safety_tests.cpp` SetUp.
- [ ] Fixtures for Geely SEA / VW MEB / BMW i3 need captures taken with a
  tester or emulator attached (temps/cells are UDS-poll-only); the
  vehicle-only captures in EV-CANlogs cannot satisfy `base`.
- [ ] Remaining direct-use logs not yet cut into fixtures: BYD Dolphin
  (#1043, 10 MB), Thunderstruck (#2144), BMW i3 (#1783/#2313, blocked on
  poll data), FoxESS (#1664, blocked on max_design bug above).
