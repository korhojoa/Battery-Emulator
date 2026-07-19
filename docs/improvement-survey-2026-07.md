# Battery-Emulator — Improvement Survey (July 2026)

This document is the result of a structured survey of the codebase (~96k lines of
C++/headers; 42 battery drivers, 25 inverter drivers) across four dimensions:
core architecture, driver code, safety-critical logic, and testing/CI.
Findings are ordered by how much they matter, not by area. File/line references
are against `main` at the time of writing (cfef104b).

Related upstream issues and PRs are cross-referenced per finding where they
exist — see also the [Upstream cross-reference](#upstream-cross-reference)
section at the end.

## 1. Concrete bugs worth fixing now

### 1.1 Out-of-bounds write from CAN data (Geely SEA)

`Software/src/battery/GEELY-SEA-BATTERY.cpp:128` — cell voltages are written to
`cell_voltages_mV[rx_frame.data.u8[2] - 1]` with no bounds check. The array
holds `MAX_AMOUNT_CELLS` (192) entries but the index byte can be anything
0–255: values above 192 write past the array, and a value of 0 produces
index −1. `number_of_cells` is also set from the same byte unclamped, so
downstream loops iterate out of bounds too.

Most other drivers (SIMPBMS, MG-HS-PHEV, Tesla, CMP) bound this correctly — a
shared bounds-checked `Battery::set_cell_voltage(idx, mV)` helper on the base
class would fix this and prevent the whole bug class recurring.

### 1.2 Divide-by-zero and sign wrap (Solax inverter)

`Software/src/inverter/SOLAX-CAN.cpp:69-77` — the cell-voltage rescale divides
by `(max_cell_voltage_mV − min_cell_voltage_mV)`; if both are 0 or equal,
that is a divide-by-zero. The signed intermediate can also go negative and
wraps when stored into a `uint16_t`.

### 1.3 Comment/code mismatch on current sign (Pylon battery) — RESOLVED: code correct, comment stale

`Software/src/battery/PYLON-BATTERY.cpp:18` — the comment says "invert the
sign" but the code doesn't. **Verified against real logs (July 2026): the
code is correct and the comment is stale copy-paste** (the identical comment
appears in the Kia/Ioniq drivers, which do negate).

Evidence: the `_dyness.zip` logs on upstream issue
[#2082](https://github.com/dalathegreat/Battery-Emulator/issues/2082) — ~17k
RX 0x4210 frames from a real Dyness Stack 100 speaking Pylon HV protocol,
with charge/discharge phases labeled in the filenames (laden/entladen).
Decoding bytes 2-3 (little-endian, −30000): SOC rises 49→100% while current
is +11 A and falls 100→50% at −22.3 A; pack voltage rises under positive
current and sags under negative. So on the wire, raw > 30000 = charging,
which matches the datalayer convention (positive `current_dA` = charging,
`types.cpp:24`) with no negation needed. Corroborated by maintainer
statements in issue
[#2019](https://github.com/dalathegreat/Battery-Emulator/issues/2019)
("incoming 30300 → charging 30 A").

**New bug found during verification:** the Pylon *inverter-side* status byte
is inverted — `Software/src/inverter/PYLON-CAN.cpp:138-142` maps
`reported_current_dA < 0` to "Charge" (0x01) and `> 0` to "Discharge"
(0x02), backwards relative to both the datalayer convention and the verified
wire convention. The same file transmits the current field itself correctly
(unnegated), so only the 0x425X battery-status byte misreports
charge/discharge state to the inverter.

### 1.4 SOC estimate underflow (Renault Kangoo)

`Software/src/battery/RENAULT-KANGOO-BATTERY.cpp:21-27` —
`(voltage − 3000) * 10` on a `uint16_t` with no clamp: below 3000 mV it wraps
to a huge SOC; above 4000 mV it exceeds 100%.

### 1.5 Busy-wait pinning the WiFi core

`Software/Software.cpp:139-147` — `logging_loop` spins in
`while (sd_initialized)` with no delay when neither SD-logging flag is active,
running the WiFi core at 100% and potentially starving the connectivity task.
It is also not registered with the task watchdog.

## 2. Safety design gaps (highest long-term value)

This firmware closes contactors on high-voltage packs; several places where
failure handling is weaker than the design suggests:

- **Hazardous conditions don't open contactors.** Only `EVENT_LEVEL_ERROR`
  events set `system_status = FAULT`, and only FAULT opens GPIO contactors.
  Battery over/under-voltage, cell over/under-voltage, cell deviation, welded
  contactor, and isolation fault are all only WARNING
  (`Software/src/devboard/utils/events.cpp:96-97,125-127`) — they zero the
  power limits but leave contactors closed, relying entirely on the inverter
  honoring 0 W. Escalating these (or adding a "force open" trigger independent
  of event severity) is probably the single highest-value safety change.
- **Stale data keeps flowing to the inverter.** When battery CAN dies, it
  takes ~60 s to raise the missing-battery ERROR
  (`Software/src/devboard/safety/safety.cpp:296`, `CAN_STILL_ALIVE = 60`)
  plus another ~10 s FAULT dwell before contactors open — and during and
  after that, last-known voltage/SOC/current values are still transmitted to
  the inverter. There is no generic per-field staleness layer; only the BMW
  iX/PHEV drivers implement local staleness detection. A datalayer-level
  invalidation on liveness expiry would cover all 42 drivers at once.
- **A flapping fault never opens contactors.** `timeSpentInFaultedMode`
  hard-resets to 0 whenever status momentarily leaves FAULT
  (`Software/src/communication/contactorcontrol/comm_contactorcontrol.cpp:177`),
  so an intermittent fault can persist indefinitely without ever accumulating
  the 10 s needed for shutdown. A leaky/decaying counter fixes this.
- **`battery_allows_contactor_closing` is ignored by the GPIO state machine.**
  Drivers set it, but `handle_contactors()` never reads it — a battery
  requesting open only gets it if it also raises an ERROR. Meanwhile
  `inverter_allows_contactor_closing` defaults to `true`
  (`Software/src/datalayer/datalayer.h:392`) and is only refreshed for
  inverters that control contactors, so for many installs closing is gated by
  little more than e-stop and a 10 s startup delay.
- **Brownout resets are only INFO-level** (`events.cpp:153`), yet a recurring
  brownout from a marginal 12 V supply is a real contactor-drop risk that
  currently raises no visible warning.

## 3. Cross-core data races

The `datalayer` singleton is written by the core loop on one CPU core and
concurrently read/written by the webserver (162 accesses, including writes to
user charge/SOC/voltage limits), MQTT, and connectivity tasks on the other
core — with **zero locking**; the only mutex in application code is for
syslog. Many fields are 32-bit or wider, so torn reads/writes are possible,
e.g. `filter_inverter_limits()` (`Software/Software.cpp:249-303`) does
read-modify-write on `max_charge_power_W` while the webserver can rewrite user
caps mid-computation. `volatile` is used in a few places as a substitute for
synchronization, which it isn't.

Pragmatic fix: `std::atomic` for the scalar limit fields, or a short
critical-section snapshot of the settings struct at the top of each 1 s cycle.

## 4. Testing — big gaps, cheap wins

The native GoogleTest setup with the Arduino/FreeRTOS emulation layer
(`test/emul/`) is genuinely good infrastructure, but:

- **The contactor/precharge state machine
  (`comm_contactorcontrol.cpp:150`) has zero tests**, despite being the most
  safety-critical code and already being compiled into the test binary. The
  emulator can advance `millis()`, so testing the full precharge sequence,
  timing, and inverted-logic (NC contactor) branches is straightforward.
- **CAN-log replay tests cover only ~5 of 62 battery types.** The framework is
  fully data-driven — dropping a log file into `test/can_log_based/can_logs/`
  adds coverage with zero code. And the logs largely already exist upstream:
  - [dalathegreat/EV-CANlogs](https://github.com/dalathegreat/EV-CANlogs)
    holds vehicle captures covering ~15 battery types (Tesla Model 3, Nissan
    LEAF, Kia EV6/eNiro/Niro PHEV, Geely SEA/Zeekr/EX30, Jaguar iPace, BMW i3,
    Think City, Dacia Spring, Ford Mach-E, and more) in a mix of formats
    (candump, SavvyCAN CSV, Vector .asc, PCAN .trc — a small conversion
    script covers most). Related: `hyundai-santa-fe-phev-battery`,
    `Ioniq28Investigations`.
  - Web-UI logs attached to issues/PRs (`canlog_HH-MM-SS.txt`) are **already
    in the exact fixture format**. Directly usable examples: Volvo SPA
    ([#2403](https://github.com/dalathegreat/Battery-Emulator/issues/2403)),
    BMW i3 ([#2313](https://github.com/dalathegreat/Battery-Emulator/issues/2313),
    [#1783](https://github.com/dalathegreat/Battery-Emulator/issues/1783)),
    Renault Zoe2 ([#1692](https://github.com/dalathegreat/Battery-Emulator/issues/1692)
    — real replacement for the currently-synthetic Zoe2 fixture),
    Zoe1 ([#1274](https://github.com/dalathegreat/Battery-Emulator/issues/1274)),
    BYD Atto3/Dolphin ([#1022](https://github.com/dalathegreat/Battery-Emulator/issues/1022),
    [#1043](https://github.com/dalathegreat/Battery-Emulator/issues/1043)),
    RJXZS BMS ([#1764](https://github.com/dalathegreat/Battery-Emulator/issues/1764)),
    FoxESS ([#1664](https://github.com/dalathegreat/Battery-Emulator/issues/1664)),
    Thunderstruck ([#2144](https://github.com/dalathegreat/Battery-Emulator/pull/2144)),
    Ford Mach-E ([#2418](https://github.com/dalathegreat/Battery-Emulator/pull/2418)).
  - Formats needing conversion: Kia E-GMP
    ([#387](https://github.com/dalathegreat/Battery-Emulator/issues/387),
    SavvyCAN/Vector), VW MEB
    ([#524](https://github.com/dalathegreat/Battery-Emulator/issues/524),
    candump), Polestar 2
    ([#442](https://github.com/dalathegreat/Battery-Emulator/issues/442),
    custom).

  **Update:** branch `test/canlog-replay-fixtures` acts on the above — it adds
  `test/can_log_based/convert_can_log.py` (converts emulator/candump/
  SavvyCAN/CANHacker/Vector-.asc captures to the fixture format) and nine
  real-log `base` fixtures: Dacia Spring, Jaguar I-PACE, Kia eNiro, Nissan
  LEAF 62 kWh, Renault Zoe1, Zoe2, Tesla Model 3, Volvo SPA, and Ford
  Mach-E — taking replay coverage from 5 to 13 battery types. The replay
  harness now advances virtual time from log timestamps and ticks the
  driver's transmit scheduler, so poll-based drivers behave as on real
  hardware. Attempting this surfaced further driver findings:
  - FoxESS never renewed `CAN_battery_still_alive` (fixed on branch
    `fix/foxess-can-still-alive`),
    and its `update_values()` recomputes `max_design_voltage_dV` from a
    per-pack-count preset table, clobbering the limits the BMS reports in
    frame 0x1872 — the FoxESS log from
    [#1664](https://github.com/dalathegreat/Battery-Emulator/issues/1664)
    reports 1 pack at 395 V, so the preset (584 dV) spuriously flags
    overvoltage.
  - Geely SEA, VW MEB, and BMW i3 report temperatures and/or cell voltages
    only via UDS poll responses, so vehicle-only captures can never satisfy
    the `base` fixture checks for them; fixtures for these need captures
    taken with an emulator or tester attached.
- `test/CMakeLists.txt` hardcodes ~90 source files; new drivers silently drop
  out of native compilation unless manually added. Auto-globbing or a CI
  assertion would close that hole.
- The host-based test job could trivially add ASan/UBSan
  (`-fsanitize=address,undefined`) and coverage reporting — the sanitizers
  would have caught the Geely out-of-bounds write on any replayed log.
- No static analysis anywhere (clang-tidy/cppcheck), and `-Werror` applies
  only to the dummy `compiler_warning_check` env, not real board builds.

## 5. Maintainability / refactoring

- **Adding a battery driver requires touching ~6 hand-synced sites** (enum,
  include, two switches in `BATTERIES.cpp`, plus double/triple-battery
  switches where forgetting one fails silently at runtime). A self-registering
  driver table would collapse this to one declaration.
- **Massive unshared boilerplate across drivers:** thousands of hand-written
  `(u8[1]<<8)|u8[0]` byte assemblies (each an endianness bug opportunity),
  `CAN_battery_still_alive = CAN_STILL_ALIVE` repeated in nearly every switch
  case instead of once in the base receive path, and copy-pasted SOC→Wh,
  temperature-averaging, and setup boilerplate. Drivers are also inconsistent
  about clamping (SOH clamped in some, not others) and about whether bad data
  raises events or is silently ignored.
- **Core loop cleanup:** `update_calculated_values()` is ~215 lines with
  near-identical copy-paste blocks for battery2/battery3
  (`Software/Software.cpp:411-520`); the voltage-fallback/cap-conversion
  logic is repeated in three places; the `performance_measurement_active`
  branches duplicate their bodies.
- **Heap fragmentation risks:** the webserver log-import accumulates the whole
  uploaded file into a growing `String`
  (`Software/src/devboard/webserver/webserver.cpp:74`), and MQTT autodiscovery
  builds topics via per-cell `String` concatenation
  (`Software/src/devboard/mqtt/mqtt.cpp:201-207`).
- `platformio.ini` copy-pastes a ~20-line component-removal list into all six
  board envs; a shared `[env]` base would halve the file.

## Suggested priority order

1. Fix the concrete bugs in section 1 (small, isolated, one PR each — the
   Geely fix ideally via the shared bounds-checked helper).
2. Contactor state-machine tests + ASan/UBSan in CI — makes everything after
   safer to change.
3. The safety design decisions in section 2 — these deserve discussion with
   the maintainer community (severity escalation and staleness handling change
   behavior for every install).
4. Datalayer synchronization for the cross-core limit/settings fields.
5. Refactoring (driver registration, shared helpers) — best done incrementally
   once tests exist.

## Recording decisions: ADRs

Several of the findings above are not bugs but *policies* — deliberate
choices whose rationale is currently only in code comments or PR threads
(event severity vs. contactor behavior, the 60 s liveness timeout, the
concurrency model for `datalayer`, manual driver registration). This repo
would benefit from [Architecture Decision
Records](https://github.com/architecture-decision-record/architecture-decision-record)
so those choices are stored, searchable, and revisitable.

This branch seeds a `docs/adr/` directory with the ADR convention and a
template. Candidate first ADRs, each mapping to a section above:

- Event severity policy: which conditions must open contactors vs. only zero
  power (section 2).
- Data staleness policy: what the inverter is told when battery data stops
  arriving (section 2).
- Concurrency model for `datalayer` (section 3).
- Driver registration mechanism (section 5).

## Upstream cross-reference

Existing dalathegreat/Battery-Emulator issues and PRs related to these
findings (searched July 2026, including closed items):

**Findings that appear unreported upstream** — candidates for new issues/PRs:

- Geely SEA cell-voltage array bounds / unclamped `number_of_cells` (1.1).
  The driver came in via [#1889](https://github.com/dalathegreat/Battery-Emulator/pull/1889);
  later Geely fixes ([#1218](https://github.com/dalathegreat/Battery-Emulator/pull/1218),
  [#1472](https://github.com/dalathegreat/Battery-Emulator/pull/1472),
  [#2159](https://github.com/dalathegreat/Battery-Emulator/pull/2159)) don't
  touch this.
- Pylon current-sign comment/code mismatch (1.3). Sign bugs were fixed in
  several *other* drivers
  ([#217](https://github.com/dalathegreat/Battery-Emulator/pull/217),
  [#1901](https://github.com/dalathegreat/Battery-Emulator/pull/1901),
  [#1853](https://github.com/dalathegreat/Battery-Emulator/pull/1853),
  [#2214](https://github.com/dalathegreat/Battery-Emulator/pull/2214)), which
  shows the failure mode is real and recurring, but Pylon specifically is
  unreported.
- Cross-core `datalayer` races (3).
  [#697](https://github.com/dalathegreat/Battery-Emulator/pull/697) added a
  mutex for the async web library only;
  [#254](https://github.com/dalathegreat/Battery-Emulator/pull/254)
  introduced the datalayer with no locking discussion.
- Contactor/precharge state-machine tests (4) — test infrastructure exists
  ([#1358](https://github.com/dalathegreat/Battery-Emulator/pull/1358),
  [#170](https://github.com/dalathegreat/Battery-Emulator/pull/170),
  [#1510](https://github.com/dalathegreat/Battery-Emulator/pull/1510)) but no
  contactor coverage.
- Self-registering driver table (5) — no prior refactoring effort found.
- ADRs (above) — no prior design-documentation effort found.

**Findings with relevant upstream history:**

- *Solax rescale (1.2):*
  [#2151](https://github.com/dalathegreat/Battery-Emulator/pull/2151)
  (merged) introduced the min/max cell-voltage rescale for the IE102 error —
  the divide-by-zero/sign-wrap cases are in that code and remain in current
  `main`.
- *Kangoo SOC estimation (1.4):* added in
  [#2104](https://github.com/dalathegreat/Battery-Emulator/pull/2104); the
  unclamped arithmetic dates from there. Related field report:
  [#1113](https://github.com/dalathegreat/Battery-Emulator/issues/1113).
- *Logging task behavior (1.5):*
  [#2624](https://github.com/dalathegreat/Battery-Emulator/pull/2624) and
  [#2640](https://github.com/dalathegreat/Battery-Emulator/pull/2640) made
  CAN/USB logging non-blocking on the core task, but the `logging_loop`
  busy-wait itself is unreported.
- *Contactor fault behavior (2):*
  [#581](https://github.com/dalathegreat/Battery-Emulator/issues/581)
  (closed) reported contactors failing to open correctly on fault;
  [#1534](https://github.com/dalathegreat/Battery-Emulator/issues/1534)
  (open) reports an inverter continuing to run in faulted state — supporting
  evidence that relying on the inverter to honor 0 W is fragile.
  [#547](https://github.com/dalathegreat/Battery-Emulator/pull/547) is
  precedent for deliberate severity-level tuning, and
  [#903](https://github.com/dalathegreat/Battery-Emulator/issues/903) for
  safety-layer override bugs.
- *Stale data (2):*
  [#630](https://github.com/dalathegreat/Battery-Emulator/issues/630)
  (closed) requested exactly this — "add stale data check for critical
  measurements" — but no generic per-field staleness layer exists in current
  `main`, so it seems to have been closed without a full implementation.
  CAN-timeout tuning has an active history
  ([#2466](https://github.com/dalathegreat/Battery-Emulator/pull/2466),
  [#2275](https://github.com/dalathegreat/Battery-Emulator/pull/2275),
  [#2345](https://github.com/dalathegreat/Battery-Emulator/pull/2345),
  [#1502](https://github.com/dalathegreat/Battery-Emulator/pull/1502)),
  showing timeout behavior is contentious across inverters — a good argument
  for recording the policy in an ADR.
