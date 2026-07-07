# VoltCraft: Tier-0 Autonomous Mechatronics Engineering Workstation

VoltCraft is a local-first, dual-agent-driven CAD and circuit simulation platform running bare-metal on Arch Linux. It integrates continuous-time analog simulation, discrete-event digital schedules, bidirectional Draw.io vector sheet serialization, and automated datasheet ingestion in a single, lightweight, zero-framework runtime.

---

## 1. Core Mathematical Foundations & Solvers

The continuous-time mechatronic simulator is implemented entirely from first principles inside `engine/solver.py`, utilizing NumPy *only* for low-level matrix inversion/factorization.

### Modified Nodal Analysis (MNA)
For an electrical network with $N$ nodes (excluding ground $n0$) and $M$ independent voltage-defining branches, VoltCraft compiles the comprehensive system:

$$\mathbf{A} \mathbf{x} = \mathbf{z}$$

Where the structural parameters are partitioned as:

$$\begin{bmatrix} \mathbf{G} & \mathbf{B} \\ \mathbf{B}^T & \mathbf{D} \end{bmatrix} \begin{bmatrix} \mathbf{v} \\ \mathbf{i}_v \end{bmatrix} = \begin{bmatrix} \mathbf{j} \\ \mathbf{e} \end{bmatrix}$$

*   $\mathbf{G} \in \mathbb{R}^{(N-1) \times (N-1)}$: Conductance matrix mapping resistors and companion conductances.
*   $\mathbf{B} \in \mathbb{R}^{(N-1) \times M}$: Voltage branch connections.
*   $\mathbf{i}_v \in \mathbb{R}^M$: Unknown currents flowing through independent voltage sources and operational amplifiers.
*   $\mathbf{j} \in \mathbb{R}^{(N-1)}$: Net independent current source injections.
*   $\mathbf{e} \in \mathbb{R}^M$: Independent voltage values.

### Non-Linear Newton-Raphson & Homotopy
Diodes are modeled using the non-linear Shockley diode relation:

$$i_D = I_S \left( e^{\frac{v_D}{N V_T}} - 1 \right)$$

At each Newton-Raphson step, the diode is linearized to an equivalent conductance $g_d$ and parallel current source $i_{eq}$:

$$g_d^{(k)} = \frac{I_S}{N V_T} e^{\frac{v_D^{(k)}}{N V_T}},\quad i_{eq}^{(k)} = g_d^{(k)} v_D^{(k)} - I_S \left( e^{\frac{v_D^{(k)}}{N V_T}} - 1 \right)$$

#### SPICE PN-Junction Limiting
To prevent floating-point exponent overflow during steep iterations, VoltCraft implements PN junction limiting:

$$v_{crit} = N V_T \ln\left( \frac{N V_T}{\sqrt{2} I_S} \right)$$

If $v_D^{(k+1)} > v_{crit}$ and the voltage step exceeds $2 N V_T$, successive iterations are damped to restrict step variations. If standard iterations fail, the solver falls back to a logarithmic $G_{min}$-stepping and source-stepping homotopy.

### Transient companion Models
VoltCraft supports energy-storing transient integration utilizing two companion stamp configurations:
1.  **Backward Euler (1st order, L-stable):**
    *   Capacitor ($C$): $g_{eq} = \frac{C}{dt}$, $i_{eq} = g_{eq} v(t-dt)$ (Parallel current source).
    *   Inductor ($L$): $g_{eq} = \frac{dt}{L}$, $i_{eq} = i(t-dt)$ (Parallel current source).
2.  **Trapezoidal (2nd order, energy-conserving):**
    *   Capacitor ($C$): $g_{eq} = \frac{2C}{dt}$, $i_{eq} = g_{eq} v(t-dt) + i(t-dt)$.
    *   Inductor ($L$): $g_{eq} = \frac{dt}{2L}$, $i_{eq} = g_{eq} v(t-dt) + i(t-dt)$.

### Transistor Device Models
*   **MOSFET (types `nmos` / `pmos`):** level-1 square-law model with cutoff, triode, and saturation regions, channel-length modulation ($\lambda$), symmetric drain-source reversal, and PMOS polarity mirroring. Params: `K` [A/V²], `Vth` [V], `lambda` [1/V].
    $$I_D^{sat} = \frac{K}{2}\left(V_{GS} - V_{th}\right)^2 (1 + \lambda V_{DS})$$
*   **BJT (types `bjt_npn` / `bjt_pnp`):** full Ebers-Moll two-junction model valid in cutoff, active, and saturation. Params: `Is` [A], `beta_f`, `beta_r`, `Vt` [V]. Active-region current gain $I_C/I_B = \beta_F$ falls out of the model rather than being imposed.

Both devices are linearized per Newton-Raphson iteration into $g_m$/$g_{ds}$ companion stamps shared by the DC, transient, and AC analyses, and both report bias quantities ($V_{GS}$, $I_D$, $V_{BE}$, $I_C$, ...) in the DC operating-point output.

### Dependent Sources & Device Capacitances
All four SPICE controlled sources are supported with the standard MNA stamps and work in every analysis: **VCVS** (`vcvs`, E: $v = k\,v_c$), **VCCS** (`vccs`, G: $i = g_m v_c$), **CCCS** (`cccs`, F: $i = k\,i_{ctrl}$), and **CCVS** (`ccvs`, H: $v = r\,i_{ctrl}$). Current-controlled elements name their controlling branch via `params.control` (any voltage-defining component), using the SPICE $I(V)$ branch-current convention.

Devices also carry first-order parasitic capacitances — diode junction `Cj0` and MOSFET `Cgs`/`Cgd` — stamped through the same companion machinery as explicit capacitors in transient runs and as $j\omega C$ admittances in AC, producing the physical frequency poles (e.g. the $1/2\pi R_G C_{GS}$ input pole of a common-source stage) and gate-charging delays.

### AC Small-Signal Frequency Analysis (Bode)
VoltCraft linearizes the circuit at its DC operating point (diodes reduce to their small-signal conductance $g_d$), then solves the complex-valued MNA system per frequency over a logarithmic sweep:

$$\mathbf{A}(j\omega)\, \mathbf{x} = \mathbf{z}, \quad Y_C = j\omega C, \quad Y_L = \frac{1}{j\omega L}$$

Sources carrying an `ac_mag` parameter (with optional `ac_phase` in degrees) drive the sweep; if none is annotated, the first voltage source is driven at $1\text{V}$ so node results read directly as the transfer function. Results are returned as magnitude (dB) and phase (degrees) per net and rendered as a two-pane Bode plot.

### Stimulus Waveforms
Voltage sources accept a `wave` parameter — `sine` (default), `square`, `triangle`, or `sawtooth` — plus `freq`, `phase` (radians), `offset`, and `duty` (square only). Periodic waves swing $\pm V$ around the offset; a source without a positive `freq` is a DC level.

### Adaptive Timestep Control (LTE)
Transient runs accept `adaptive: true` (or the **LTE** toggle in the UI): each candidate step is solved once at $h$ and again as two $h/2$ substeps, and the difference estimates the local truncation error scaled by $2^p - 1$ for an order-$p$ integrator. Steps exceeding `lte_tol` are rejected and halved; comfortably accurate steps double (bounded by `dt_min`/`dt_max`). On an RC charging benchmark the controller matches a fixed grid's worst-case accuracy with **44 steps instead of 40,000** (226x fewer Newton solves), and it automatically refines around square-wave edges.

### Hierarchical Subcircuits
A subcircuit definition is an ordinary VCG graph plus a top-level `"ports"` list naming the nets it exposes. Instances are nodes of type `subcircuit` (ID prefix `X`) whose `params.ref` names the definition file and whose pins map ports onto parent nets. Before solving, instances are flattened inline: internal components and nets are namespaced `X1.R1` / `X1.mid` (addressable in result maps and the operating-point report), ports splice onto the parent nets, and `n0` remains global ground. Definitions nest arbitrarily; cycles are rejected with a depth guard, and definition files are validated and confined to the workspace tree.

### DC Parameter Sweeps
Any component parameter can be swept linearly (`mode="dc_sweep"`, or the *DC Param Sweep* mode in the UI): the solver computes the full DC operating point per value and returns the transfer curves — e.g. voltage-divider ramps or MOSFET $I_D$–$V_{GS}$ characteristics. The swept parameter is restored after the run.

### Solver Telemetry & Operating-Point Report
Every DC, transient, and AC run returns a `stats` block (matrix size, Newton-Raphson iteration counts, convergence flag, worst residual, condition estimate) that drives the live diagnostics panel. DC runs additionally return an `operating_point` report with per-component voltage, current, and power (branch currents follow the MNA convention: a delivering source reports negative current). Voltage-defining components left with every terminal on ground are rejected by name instead of surfacing a bare singular-matrix error.

### Cycle-Basis Mesh Analysis Loop-Finder
For loop-current cycle equations, VoltCraft finds fundamental loop bases dynamically:
1.  Executes a Depth-First Search (DFS) spanning tree over two-terminal nodes.
2.  Locates cotree links (branches not in the spanning tree) forming $L = B - N + 1$ fundamental cycles.
3.  Compiles the Cycle-Branch incidence matrix $\mathbf{C} \in \mathbb{R}^{L \times B}$ by tracking tree paths.
4.  Solves the loop currents:
    $$\mathbf{Z}_m \mathbf{i}_m = \mathbf{e}_m \implies \left( \mathbf{C} \mathbf{Z}_e \mathbf{C}^T \right) \mathbf{i}_m = \mathbf{C} \mathbf{v}_s$$

---

## 2. Discrete-Event & Mixed-Signal Synchronization

### Digital Logic Engine
Digital components (AND, OR, NOT, NAND, NOR, XOR, comparators) are simulated using an event-driven queue:
*   Timeline events are tracked inside a `heapq` priority queue: `(time, scheduling_id, net_id, state)`.
*   Zero-delay logic oscillations are resolved via delta-timestep queue segments.

### Timestep Co-Simulation Synchronization
Mixed-signal coordination coordinates continuous-discrete border nets:
*   **Timestep Arbitration:** The analog integrator truncates its integration step $\Delta t_{ana}$ dynamically to align with the next scheduled digital queue event $t_{dig}$.
*   **Boundary Interfaces:** Digital outputs inject low-resistance voltage sources ($0\text{V}$ or $5\text{V}$) into MNA equations. Analog voltages drive digital triggers via threshold detectors ($V_{IL} = 1.5\text{V}$ and $V_{IH} = 3.5\text{V}$) using linear crossing interpolation.

---

## 3. Bidirectional Draw.io Codec XML

VoltCraft incorporates a bidirectional translator (`engine/parser_drawio.py`) supporting uncompressed Draw.io `<mxGraphModel>` files:
*   **XML to VCG JSON:** Extracted vertices (shapes) are mapped to mechatronic nodes, and connection coordinates are grouped into electrical netlists via a Disjoint Set Union (DSU) algorithm.
*   **VCG JSON to XML:** Reconstructs standard mxCell hierarchies with grid offsets, intermediate array routes, and custom component parameters embedded inside style attributes.

---

## 4. Web-Scraper & Caching Subsystem

 Datasheet parameter ingestion is implemented inside `engine/scraper.py`:
*   Asynchronously fetches component parameters (saturation currents, input/output impedances) utilizing `httpx` and `BeautifulSoup4`.
*   Strictly respects `robots.txt` and rate-limits to $1$ second minimum request intervals.
*   Enforces on-disk, SHA-256 addressed JSON caching under `storage/cache/` to ensure zero redundant external requests.

---

## 5. REST & WebSocket API Surface

Serves concurrent agent Designer and Verifier loops utilizing FastAPI:
*   **REST Actions (`/api/agent/action`, POST):** Integrates schematic loading/saving, component placements, netlist wiring, and transient runs.
*   **WebSockets Channel (`/ws/agent`):** Handles optimistic concurrent merges (last-writer-wins per node coordinate) and broadcasts updates to clients.
*   **State Journaling:** Every single graph mutation appends a line to the append-only JSON journal under `storage/journal/mutations.ndjson` recording timestamps, agent IDs, actions, and current SHA-256.

---

## 6. Premium Single-Page Workbench Interface

Vanilla HTML5, precompiled Tailwind CSS (`tailwind.min.css`), and pure ES2022 asynchronous JavaScript:
*   **SVG Schematic CAD Canvas (`designer.js`):** Interactive placement, snap-to-grid grids, double-click rotations, and 3-segment orthogonal wire layouts. Clicking a component selects it and opens the sidebar **component inspector**, where every model parameter can be edited live (`update_params` action) with the change journaled and broadcast to all clients.
*   **Diagnostic Probes:** Any net voltage — and any branch current carried in the MNA solution (`I(V1)`, `I(U1)`, ...) — can be toggled as a probe and plotted in the transient and AC views.
*   **Waveform Signal Plotter (`simulator_view.js`):** Renders analog curves, stacked digital logic transitions, and AC Bode plots (log-frequency magnitude + phase panes) on SVG panels. Handles zoom/pan, cursor coordinates, and diagnostic probes.
*   **Agent telemetry Panel (`agent_bridge.js`):** Displays flashing cyan overlays on canvas viewports during ongoing autonomous agent socket edits.

---

## 7. Setup & Execution Guide

### Arch Linux Dependencies
Ensure your package manager contains Python and the authorized dependencies:
```bash
sudo pacman -S --needed python python-fastapi uvicorn python-numpy python-httpx python-beautifulsoup4 python-pytest
```

On any other platform, install from the pinned requirements instead:
```bash
python -m venv .venv && . .venv/bin/activate
pip install -r voltcraft/requirements.txt
```

### Verification & Compilation Run
1.  Compile check:
    ```bash
    /usr/bin/python -m py_compile $(find voltcraft -name '*.py')
    ```
2.  Execute the full pytest regression suite (solver, parsers, API, and bug-regression tests):
    ```bash
    /usr/bin/python -m pytest voltcraft/tests -v
    ```
3.  VCG circuit schematic JSON validation:
    ```bash
    /usr/bin/python -c "import json, pathlib; [json.loads(p.read_text()) for p in pathlib.Path('voltcraft/storage/schematics').glob('*.vcg.json')]"
    ```

### Launching VoltCraft
Start the ASGI server locally:
```bash
/usr/bin/python -m uvicorn voltcraft.app:app --host 127.0.0.1 --port 8000
```
Open a browser and navigate to `http://127.0.0.1:8000/` to access the premium workstation.
