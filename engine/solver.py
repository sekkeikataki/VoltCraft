import numpy as np
import heapq
import math
from typing import Dict, Any, List, Optional, Tuple


def _positive_param(params: Dict[str, Any], key: str, default: float, component_id: str) -> float:
    """Reads a component parameter that must be strictly positive."""
    value = float(params.get(key, default))
    if value <= 0.0:
        raise ValueError(f"Component '{component_id}' requires a positive {key}, got {value}")
    return value


def _stamp_conductance(A: np.ndarray, ia: Optional[int], ib: Optional[int], g: float) -> None:
    """Stamps a conductance g between two node indices (None = ground)."""
    if ia is not None:
        A[ia, ia] += g
    if ib is not None:
        A[ib, ib] += g
    if ia is not None and ib is not None:
        A[ia, ib] -= g
        A[ib, ia] -= g


def _stamp_current_injection(z: np.ndarray, ia: Optional[int], ib: Optional[int], i: float) -> None:
    """Stamps a current i injected into node a and drawn out of node b."""
    if ia is not None:
        z[ia] += i
    if ib is not None:
        z[ib] -= i


def _stamp_voltage_branch(A: np.ndarray, ia: Optional[int], ib: Optional[int], br_idx: int) -> None:
    """Stamps the +/- incidence entries linking a voltage branch to its nodes."""
    if ia is not None:
        A[ia, br_idx] += 1.0
        A[br_idx, ia] += 1.0
    if ib is not None:
        A[ib, br_idx] -= 1.0
        A[br_idx, ib] -= 1.0


def _require_wired_branch(n_id: str, n_type: str, ia: Optional[int], ib: Optional[int]) -> None:
    """
    A voltage-defining branch with every terminal on ground produces an
    all-zero matrix row (an unsolvable short); report it by name instead of
    letting numpy fail with a bare 'Singular matrix'.
    """
    if ia is None and ib is None:
        raise ValueError(
            f"'{n_id}' ({n_type}) has every terminal on ground net n0 - wire its pins before simulating"
        )


def source_voltage_at(params: Dict[str, Any], t: float) -> float:
    """
    Evaluates a voltage source's value at time t.

    Waveform parameters:
        V:      amplitude (peak for periodic waves, level for DC), default 5.0
        freq:   frequency in Hz; a positive freq makes the source periodic
        wave:   "sine" (default), "square", "triangle", or "sawtooth"
        phase:  phase offset in radians (all waveforms)
        offset: DC offset added to periodic waveforms, default 0.0
        duty:   square-wave duty cycle in (0, 1), default 0.5

    Periodic waves swing +/-V around the offset, matching the sine
    convention. A source without a positive freq is a DC level of V.
    """
    v_amp = float(params.get("V", 5.0))
    freq = float(params.get("freq", 0.0))
    if freq <= 0.0:
        return v_amp

    wave = params.get("wave", "sine")
    phase = float(params.get("phase", 0.0))
    offset = float(params.get("offset", 0.0))

    if wave == "sine":
        return offset + v_amp * math.sin(2.0 * math.pi * freq * t + phase)

    # Normalized position in the cycle [0, 1), with phase in radians
    cycle = (t * freq + phase / (2.0 * math.pi)) % 1.0

    if wave == "square":
        duty = float(params.get("duty", 0.5))
        return offset + (v_amp if cycle < duty else -v_amp)
    elif wave == "triangle":
        # -V at cycle 0, +V at cycle 0.5, back to -V at cycle 1
        if cycle < 0.5:
            return offset + v_amp * (4.0 * cycle - 1.0)
        return offset + v_amp * (3.0 - 4.0 * cycle)
    elif wave == "sawtooth":
        return offset + v_amp * (2.0 * cycle - 1.0)

    raise ValueError(f"Unknown source waveform: {wave!r}")


class ContinuousSolver:
    """
    Continuous-time analog circuit solver.
    Assembles Modified Nodal Analysis (MNA) and Mesh matrices.
    Solves DC and Transient circuits.
    """
    def __init__(self, nodes: List[Dict[str, Any]], edges: List[Dict[str, Any]]):
        self.nodes = nodes
        self.edges = edges
        self.g_min = 1e-12  # Minimum shunt conductance to prevent singular matrices
        self._mappings_cache = None
        # Populated by solve_dc/solve_transient/solve_ac with convergence
        # diagnostics for the most recent analysis (see each method)
        self.last_solve_stats: Optional[Dict[str, Any]] = None

    def _get_nets_and_mappings(self) -> Tuple[List[str], Dict[str, int], List[Dict[str, Any]]]:
        """
        Gathers all unique nets in the circuit and assigns matrix indices.
        Excludes 'n0' (ground) from the KCL index mapping.
        The result is cached: the circuit topology (pins/nets) must not change
        during the lifetime of a solver instance (parameter values may).
        """
        if self._mappings_cache is not None:
            return self._mappings_cache

        nets_set = set()
        for node in self.nodes:
            for pin, net in node.get("pins", {}).items():
                nets_set.add(net)
        for edge in self.edges:
            nets_set.add(edge.get("net"))
            
        nets_list = sorted(list(nets_set))
        if "n0" not in nets_list:
            nets_list.insert(0, "n0")
            
        # Map non-ground nets to index (0 to K-1)
        net_map = {}
        idx = 0
        for net in nets_list:
            if net == "n0":
                continue
            net_map[net] = idx
            idx += 1
            
        # Find voltage-defining components
        voltage_components = []
        for node in self.nodes:
            if node["type"] in ("voltage_source", "opamp", "digital_interface_out"):
                voltage_components.append(node)

        self._mappings_cache = (nets_list, net_map, voltage_components)
        return self._mappings_cache

    def limit_diode_voltage(self, v_old: float, v_new: float, vt: float, n: float, Is: float) -> float:
        """
        Restricts successive diode voltage steps to prevent exponential overflow in Newton-Raphson.
        Uses SPICE-equivalent PN junction limiting.
        """
        nvt = n * vt
        v_crit = nvt * math.log(nvt / (math.sqrt(2.0) * Is))
        if v_new > v_crit and (v_new - v_old) > 2.0 * nvt:
            return v_old + 2.0 * nvt
        elif v_new < 0.0 and (v_old - v_new) > 2.0 * nvt:
            return v_old - 2.0 * nvt
        return v_new

    def _mos_small_signal(self, node: Dict[str, Any], x: Optional[np.ndarray]) -> Dict[str, Any]:
        """
        Level-1 (square-law) MOSFET linearization at the voltages in x.
        Handles cutoff/triode/saturation, drain-source reversal by symmetry,
        and PMOS polarity. Returns physical-frame quantities: i_d is the
        current entering the drain terminal, gm/gds the small-signal
        (trans)conductances, and eff_d/eff_s the (possibly swapped) node
        indices the stamps apply to.
        """
        _, net_map, _ = self._get_nets_and_mappings()
        pins = node.get("pins", {})
        params = node.get("params", {})
        n_id = node["id"]

        p = -1.0 if node["type"] == "pmos" else 1.0
        ig = net_map.get(pins.get("gate", "n0"))
        idr = net_map.get(pins.get("drain", "n0"))
        isr = net_map.get(pins.get("source", "n0"))

        def v_at(idx: Optional[int]) -> float:
            return float(x[idx]) if (x is not None and idx is not None) else 0.0

        K = _positive_param(params, "K", 1e-3, n_id)       # A/V^2 (already includes W/L)
        vth = float(params.get("Vth", 1.0))
        lam = float(params.get("lambda", 0.0))

        vgs = p * (v_at(ig) - v_at(isr))
        vds = p * (v_at(idr) - v_at(isr))

        # The device is symmetric: for negative vds operate with the
        # terminals swapped so the model always sees vds >= 0
        eff_d, eff_s = idr, isr
        if vds < 0.0:
            vgs -= vds
            vds = -vds
            eff_d, eff_s = isr, idr

        vov = vgs - vth
        if vov <= 0.0:
            i_f, gm, gds = 0.0, 0.0, 1e-12
        elif vds >= vov:
            # Saturation
            chan = 1.0 + lam * vds
            i_f = 0.5 * K * vov * vov * chan
            gm = K * vov * chan
            gds = 0.5 * K * vov * vov * lam + 1e-12
        else:
            # Triode
            chan = 1.0 + lam * vds
            i_f = K * (vov - 0.5 * vds) * vds * chan
            gm = K * vds * chan
            gds = K * (vov - vds) * chan + K * (vov - 0.5 * vds) * vds * lam + 1e-12

        return {
            "i_d": p * i_f,     # current entering the physical drain pin
            "gm": gm,
            "gds": gds,
            "eff_d": eff_d,
            "eff_s": eff_s,
            "gate": ig,
            "vgs": vgs if p > 0 else -vgs,
            "vds": p * (v_at(idr) - v_at(isr))
        }

    def _stamp_mos(self, A: np.ndarray, z: Optional[np.ndarray], node: Dict[str, Any], x: Optional[np.ndarray]) -> None:
        """
        Stamps the MOSFET companion model: gds across drain-source, a gm
        transconductance from the gate, and (when z is given) the Newton
        equivalent current source. With z=None only the small-signal
        conductances are stamped (AC analysis).
        """
        ss = self._mos_small_signal(node, x)
        ed, es, ig = ss["eff_d"], ss["eff_s"], ss["gate"]
        gm, gds = ss["gm"], ss["gds"]

        _stamp_conductance(A, ed, es, gds)
        # Transconductance rows: current gm*(vg - v_eff_s) enters eff_d
        if ed is not None:
            if ig is not None:
                A[ed, ig] += gm
            if es is not None:
                A[ed, es] -= gm
        if es is not None:
            if ig is not None:
                A[es, ig] -= gm
            if es is not None:
                A[es, es] += gm

        if z is not None:
            def v_at(idx):
                return float(x[idx]) if (x is not None and idx is not None) else 0.0
            # Constant term of the linearized drain current (same companion
            # pattern as the diode stamp)
            i_const = ss["i_d"] - gds * (v_at(ed) - v_at(es)) - gm * (v_at(ig) - v_at(es))
            _stamp_current_injection(z, ed, es, -i_const)

    def _bjt_small_signal(self, node: Dict[str, Any], x: Optional[np.ndarray]) -> Dict[str, Any]:
        """
        Ebers-Moll BJT linearization at the voltages in x. Returns the 3x3
        physical-frame conductance matrix over (collector, base, emitter),
        the terminal currents entering each pin, and the node indices.
        """
        _, net_map, _ = self._get_nets_and_mappings()
        pins = node.get("pins", {})
        params = node.get("params", {})

        p = -1.0 if node["type"] == "bjt_pnp" else 1.0
        ic_ = net_map.get(pins.get("collector", "n0"))
        ib_ = net_map.get(pins.get("base", "n0"))
        ie_ = net_map.get(pins.get("emitter", "n0"))

        def v_at(idx: Optional[int]) -> float:
            return float(x[idx]) if (x is not None and idx is not None) else 0.0

        Is = float(params.get("Is", 1e-15))
        beta_f = float(params.get("beta_f", 100.0))
        beta_r = float(params.get("beta_r", 1.0))
        Vt = float(params.get("Vt", 0.02585))

        a_f = beta_f / (beta_f + 1.0)
        a_r = beta_r / (beta_r + 1.0)
        i_es = Is / a_f
        i_cs = Is / a_r

        # Junction voltages in the NPN frame, clamped like the diode model
        vbe = max(-10.0, min(p * (v_at(ib_) - v_at(ie_)), 2.0))
        vbc = max(-10.0, min(p * (v_at(ib_) - v_at(ic_)), 2.0))

        exp_be = math.exp(vbe / Vt)
        exp_bc = math.exp(vbc / Vt)
        i_f = i_es * (exp_be - 1.0)
        i_r = i_cs * (exp_bc - 1.0)
        g_f = (i_es / Vt) * exp_be
        g_r = (i_cs / Vt) * exp_bc

        # Terminal currents entering each pin (NPN frame)
        ic_f = a_f * i_f - i_r
        ie_f = -i_f + a_r * i_r
        ib_f = -(ic_f + ie_f)

        # Physical-frame conductance matrix over (c, b, e); polarity cancels
        # in the quadratic form, only the current constants carry p
        G = (
            (g_r, a_f * g_f - g_r, -a_f * g_f),
            (-(1.0 - a_r) * g_r, (1.0 - a_f) * g_f + (1.0 - a_r) * g_r, -(1.0 - a_f) * g_f),
            (-a_r * g_r, a_r * g_r - g_f, g_f)
        )

        return {
            "indices": (ic_, ib_, ie_),
            "G": G,
            "i_terms": (p * ic_f, p * ib_f, p * ie_f),
            "vbe": p * vbe,
            "vce": v_at(ic_) - v_at(ie_)
        }

    def _stamp_bjt(self, A: np.ndarray, z: Optional[np.ndarray], node: Dict[str, Any], x: Optional[np.ndarray]) -> None:
        """
        Stamps the BJT companion model over its three terminals. With z=None
        only the small-signal conductance matrix is stamped (AC analysis).
        """
        ss = self._bjt_small_signal(node, x)
        idxs = ss["indices"]
        G = ss["G"]

        for row in range(3):
            if idxs[row] is None:
                continue
            for col in range(3):
                if idxs[col] is not None:
                    A[idxs[row], idxs[col]] += G[row][col]

        if z is not None:
            def v_at(idx):
                return float(x[idx]) if (x is not None and idx is not None) else 0.0
            volts = [v_at(idx) for idx in idxs]
            for row in range(3):
                if idxs[row] is None:
                    continue
                i_const = ss["i_terms"][row] - sum(G[row][col] * volts[col] for col in range(3))
                z[idxs[row]] -= i_const

    def assemble_mna(self, t: float, dt: float, history: Dict[str, Any], prev_x: np.ndarray = None) -> Tuple[np.ndarray, np.ndarray, Dict[str, int]]:
        """
        Assembles the MNA system A * x = z at time t with timestep dt.
        history: dictionary containing previous voltages, currents, and companion source states.
        prev_x: previous Newton-Raphson iteration vector for nonlinear elements.
        """
        nets_list, net_map, volt_comps = self._get_nets_and_mappings()
        
        num_nets = len(net_map)
        num_volts = len(volt_comps)
        size = num_nets + num_volts
        
        A = np.zeros((size, size))
        z = np.zeros(size)
        
        # Build index mapping for voltage branches
        volt_map = {}
        for i, comp in enumerate(volt_comps):
            volt_map[comp["id"]] = num_nets + i
            
        # Add minimal Gmin conductances to prevent floating nodes
        for idx in range(num_nets):
            A[idx, idx] += self.g_min
            
        def pin_index(pins: Dict[str, str], pin_name: str) -> Optional[int]:
            return net_map.get(pins.get(pin_name, "n0"))

        # Process each component
        for node in self.nodes:
            n_type = node["type"]
            n_id = node["id"]
            pins = node.get("pins", {})
            params = node.get("params", {})

            if n_type == "resistor":
                r_val = _positive_param(params, "R", 1000.0, n_id)
                _stamp_conductance(A, pin_index(pins, "a"), pin_index(pins, "b"), 1.0 / r_val)

            elif n_type == "current_source":
                i_val = float(params.get("I", 0.0))
                # I source flows out of a, into b
                _stamp_current_injection(z, pin_index(pins, "a"), pin_index(pins, "b"), -i_val)

            elif n_type == "voltage_source":
                br_idx = volt_map[n_id]
                # Pin a is positive, pin b negative
                ia = pin_index(pins, "a")
                ib = pin_index(pins, "b")
                _require_wired_branch(n_id, n_type, ia, ib)
                _stamp_voltage_branch(A, ia, ib, br_idx)
                z[br_idx] = source_voltage_at(params, t)

            elif n_type == "diode":
                ia = pin_index(pins, "anode")
                ic = pin_index(pins, "cathode")

                Is = float(params.get("Is", 1e-14))
                N = float(params.get("N", 1.0))
                Vt = float(params.get("Vt", 0.02585))

                # Extract previous diode voltage for NR linearization
                if prev_x is not None:
                    v_a = prev_x[ia] if ia is not None else 0.0
                    v_c = prev_x[ic] if ic is not None else 0.0
                    v_d = v_a - v_c
                else:
                    # DC initial guess
                    v_d = 0.6

                # Compute linearized diode companion parameters
                v_d = max(-10.0, min(v_d, 2.0))  # Sanity clamp
                exp_term = math.exp(v_d / (N * Vt))
                i_d = Is * (exp_term - 1.0)
                g_d = (Is / (N * Vt)) * exp_term
                # Companion source I_eq = i_d - g_d*v_d flows anode->cathode;
                # as an injection that is +(g_d*v_d - i_d) into the anode
                i_eq = g_d * v_d - i_d

                _stamp_conductance(A, ia, ic, g_d)
                _stamp_current_injection(z, ia, ic, i_eq)

            elif n_type == "capacitor":
                c_val = _positive_param(params, "C", 1e-6, n_id)
                ia = pin_index(pins, "a")
                ib = pin_index(pins, "b")

                # Retrieve history values
                prev_v = float(history.get(f"{n_id}_v", 0.0))
                prev_i = float(history.get(f"{n_id}_i", 0.0))

                method = history.get("integration_method", "backward_euler")
                if method == "trapezoidal":
                    g_eq = 2.0 * c_val / dt
                    i_eq = g_eq * prev_v + prev_i
                else:
                    # Backward Euler
                    g_eq = c_val / dt
                    i_eq = g_eq * prev_v

                # Resistor + Current source parallel companion stamp
                _stamp_conductance(A, ia, ib, g_eq)
                _stamp_current_injection(z, ia, ib, i_eq)

            elif n_type == "inductor":
                l_val = _positive_param(params, "L", 1e-3, n_id)
                ia = pin_index(pins, "a")
                ib = pin_index(pins, "b")

                prev_v = float(history.get(f"{n_id}_v", 0.0))
                prev_i = float(history.get(f"{n_id}_i", 0.0))

                method = history.get("integration_method", "backward_euler")
                if method == "trapezoidal":
                    g_eq = dt / (2.0 * l_val)
                    i_eq = g_eq * prev_v + prev_i
                else:
                    g_eq = dt / l_val
                    i_eq = prev_i

                _stamp_conductance(A, ia, ib, g_eq)
                _stamp_current_injection(z, ia, ib, -i_eq)

            elif n_type == "opamp":
                ini = pin_index(pins, "non_inverting")
                iinv = pin_index(pins, "inverting")
                iout = pin_index(pins, "out")

                gain = float(params.get("gain", 1e5))
                Rin = _positive_param(params, "Rin", 1e6, n_id)
                Rout = float(params.get("Rout", 50.0))

                # Stamp Rin between terminals
                _stamp_conductance(A, ini, iinv, 1.0 / Rin)

                # Output branch: v_out - gain*(v_pos - v_neg) - Rout*i_out = 0
                br_idx = volt_map[n_id]
                _stamp_voltage_branch(A, iout, None, br_idx)
                if ini is not None:
                    A[br_idx, ini] -= gain
                if iinv is not None:
                    A[br_idx, iinv] += gain

                A[br_idx, br_idx] -= Rout
                z[br_idx] = 0.0

            elif n_type in ("nmos", "pmos"):
                self._stamp_mos(A, z, node, prev_x)

            elif n_type in ("bjt_npn", "bjt_pnp"):
                self._stamp_bjt(A, z, node, prev_x)

            elif n_type == "digital_interface_out":
                v_val = float(params.get("V", 0.0))
                br_idx = volt_map[n_id]
                # Output pin referenced to ground
                ia = pin_index(pins, "analog_out")
                _require_wired_branch(n_id, n_type, ia, None)
                _stamp_voltage_branch(A, ia, None, br_idx)
                z[br_idx] = v_val

        # Construct comprehensive index mapping log
        complete_map = {}
        for k, v in net_map.items():
            complete_map[k] = v
        for k, v in volt_map.items():
            complete_map[f"branch_{k}"] = v
            
        return A, z, complete_map

    def init_energy_storage_history(self, x: np.ndarray, history: Dict[str, Any]) -> None:
        """Seeds capacitor/inductor companion state from an operating point x."""
        _, net_map, _ = self._get_nets_and_mappings()
        for node in self.nodes:
            n_type = node["type"]
            if n_type not in ("capacitor", "inductor"):
                continue
            n_id = node["id"]
            pins = node.get("pins", {})
            ia = net_map.get(pins.get("a", "n0"))
            ib = net_map.get(pins.get("b", "n0"))

            if n_type == "capacitor":
                v_cap = (x[ia] if ia is not None else 0.0) - (x[ib] if ib is not None else 0.0)
                history[f"{n_id}_v"] = v_cap
                history[f"{n_id}_i"] = 0.0  # Initial current is 0
            else:
                history[f"{n_id}_v"] = 0.0
                history[f"{n_id}_i"] = 0.0

    def update_energy_storage_history(self, x: np.ndarray, dt: float, history: Dict[str, Any],
                                      method: str = "backward_euler") -> None:
        """Advances capacitor/inductor companion state after a solved timestep."""
        _, net_map, _ = self._get_nets_and_mappings()
        for node in self.nodes:
            n_type = node["type"]
            if n_type not in ("capacitor", "inductor"):
                continue
            n_id = node["id"]
            pins = node.get("pins", {})
            params = node.get("params", {})
            ia = net_map.get(pins.get("a", "n0"))
            ib = net_map.get(pins.get("b", "n0"))

            v_new = (x[ia] if ia is not None else 0.0) - (x[ib] if ib is not None else 0.0)
            v_old = history[f"{n_id}_v"]

            if n_type == "capacitor":
                c_val = float(params.get("C", 1e-6))
                if method == "trapezoidal":
                    i_new = (2.0 * c_val / dt) * (v_new - v_old) - history[f"{n_id}_i"]
                else:
                    i_new = (c_val / dt) * (v_new - v_old)
            else:
                l_val = float(params.get("L", 1e-3))
                if method == "trapezoidal":
                    i_new = (dt / (2.0 * l_val)) * (v_new + v_old) + history[f"{n_id}_i"]
                else:
                    i_new = (dt / l_val) * v_new + history[f"{n_id}_i"]

            history[f"{n_id}_v"] = v_new
            history[f"{n_id}_i"] = i_new

    def newton_transient_step(self, t: float, dt: float, history: Dict[str, Any], x0: np.ndarray,
                              max_iter: int = 80, tol: float = 1e-6) -> Tuple[np.ndarray, int, float]:
        """
        Solves one timestep's MNA system by Newton-Raphson iteration from x0.
        Returns (solution, iterations_used, final_step_norm).
        """
        x_curr = x0.copy()
        diff = 0.0
        iterations = 0
        for iterations in range(1, max_iter + 1):
            A, z, _ = self.assemble_mna(t, dt, history, prev_x=x_curr)
            x_next = np.linalg.solve(A, z)
            diff = np.linalg.norm(x_next - x_curr)
            x_curr = x_next
            if diff < tol:
                break
        return x_curr, iterations, float(diff)

    def solve_dc(self, max_iter: int = 150, tol: float = 1e-6) -> Tuple[np.ndarray, Dict[str, int]]:
        """
        Computes the DC operating point using Newton-Raphson iteration.
        Employs source-stepping homotopy to force convergence for nonlinear diodes.
        """
        nets_list, net_map, volt_comps = self._get_nets_and_mappings()
        size = len(net_map) + len(volt_comps)
        
        # Newton-Raphson Loop with Source Stepping
        # Start at scale 0.0 (fully off) to 1.0 (fully on)
        steps = 10
        x = np.zeros(size)
        history = {"integration_method": "backward_euler"}
        total_iterations = 0
        converged = True
        final_diff = 0.0
        A = np.zeros((0, 0))

        for step in range(1, steps + 1):
            scale = step / float(steps)
            
            # Setup scaled nodes
            scaled_nodes = []
            for node in self.nodes:
                node_copy = node.copy()
                if node["type"] in ("voltage_source", "digital_interface_out") and "V" in node["params"]:
                    params_copy = node["params"].copy()
                    params_copy["V"] = float(node["params"]["V"]) * scale
                    node_copy["params"] = params_copy
                elif node["type"] == "current_source" and "I" in node["params"]:
                    params_copy = node["params"].copy()
                    params_copy["I"] = float(node["params"]["I"]) * scale
                    node_copy["params"] = params_copy
                scaled_nodes.append(node_copy)
                
            solver_step = ContinuousSolver(scaled_nodes, self.edges)
            
            # Newton Raphson Iterations
            for nr_iter in range(max_iter):
                A, z, _ = solver_step.assemble_mna(0.0, 1.0, history, prev_x=x)

                try:
                    next_x = np.linalg.solve(A, z)
                    if not np.all(np.isfinite(next_x)):
                        raise np.linalg.LinAlgError("non-finite MNA solution")
                except np.linalg.LinAlgError:
                    # Inject Gmin boost dynamically to restore conditioning
                    for idx in range(len(net_map)):
                        A[idx, idx] += 1e-9
                    next_x = np.linalg.solve(A, z)
                
                # Apply diode damping to prevent explosive exponential overflow
                for node in self.nodes:
                    if node["type"] == "diode":
                        na = node["pins"].get("anode", "n0")
                        nc = node["pins"].get("cathode", "n0")
                        ia = net_map.get(na)
                        ic = net_map.get(nc)
                        
                        v_old = (x[ia] if ia is not None else 0.0) - (x[ic] if ic is not None else 0.0)
                        v_new = (next_x[ia] if ia is not None else 0.0) - (next_x[ic] if ic is not None else 0.0)
                        
                        Is = float(node["params"].get("Is", 1e-14))
                        N = float(node["params"].get("N", 1.0))
                        Vt = float(node["params"].get("Vt", 0.02585))
                        
                        v_lim = self.limit_diode_voltage(v_old, v_new, Vt, N, Is)
                        
                        # Compensate nodes
                        if ia is not None and ic is not None:
                            next_x[ia] = next_x[ic] + v_lim
                        elif ia is not None:
                            next_x[ia] = v_lim
                        elif ic is not None:
                            next_x[ic] = -v_lim
                            
                # Check residual tolerance
                diff = np.linalg.norm(next_x - x)
                x = next_x
                total_iterations += 1
                final_diff = float(diff)
                if diff < tol:
                    converged = True
                    break
            else:
                # If convergence fails, carry on with the last best guess
                converged = False

        self.last_solve_stats = {
            "analysis": "dc",
            "matrix_size": size,
            "source_steps": steps,
            "newton_iterations": total_iterations,
            "converged": bool(converged),
            "residual": final_diff,
            "condition_estimate": float(np.linalg.cond(A)) if A.size else 1.0
        }

        # Return the complete index map (nets plus voltage branch currents)
        # so every entry of x is addressable, matching assemble_mna's map
        cmap = dict(net_map)
        for i, comp in enumerate(volt_comps):
            cmap[f"branch_{comp['id']}"] = len(net_map) + i
        return x, cmap

    def solve_transient(self, t_start: float, t_stop: float, dt: float, method: str = "backward_euler", uic: bool = False) -> Tuple[np.ndarray, List[float], Dict[str, int]]:
        """
        Computes the continuous transient simulation waveform matrix over time.
        """
        if dt <= 0.0:
            raise ValueError(f"Transient timestep dt must be positive, got {dt}")
        if t_stop <= t_start:
            raise ValueError(f"t_stop ({t_stop}) must be greater than t_start ({t_start})")

        nets_list, net_map, volt_comps = self._get_nets_and_mappings()
        size = len(net_map) + len(volt_comps)

        # Initial condition from DC analysis
        x, cmap = self.solve_dc()
        if uic:
            x = np.zeros(size)

        steps = int(math.ceil((t_stop - t_start) / dt))
        results = np.zeros((size, steps + 1))
        results[:, 0] = x

        time_points = [t_start]
        history = {"integration_method": method}
        self.init_energy_storage_history(x, history)

        # Main transient integration loop
        total_iterations = 0
        max_step_iterations = 0
        worst_residual = 0.0
        for step in range(1, steps + 1):
            t = t_start + step * dt
            time_points.append(t)

            x_curr, iters, residual = self.newton_transient_step(t, dt, history, results[:, step - 1])
            total_iterations += iters
            max_step_iterations = max(max_step_iterations, iters)
            worst_residual = max(worst_residual, residual)
            results[:, step] = x_curr
            self.update_energy_storage_history(x_curr, dt, history, method=method)

        self.last_solve_stats = {
            "analysis": "transient",
            "matrix_size": size,
            "method": method,
            "timesteps": steps,
            "newton_iterations": total_iterations,
            "max_step_iterations": max_step_iterations,
            "residual": worst_residual
        }

        return results, time_points, cmap

    def solve_transient_adaptive(self, t_start: float, t_stop: float, dt_init: float = None,
                                 dt_min: float = None, dt_max: float = None, lte_tol: float = 1e-4,
                                 method: str = "trapezoidal", uic: bool = False,
                                 max_steps: int = 200000) -> Tuple[np.ndarray, List[float], Dict[str, int]]:
        """
        Transient simulation with adaptive timestep control.

        The local truncation error of each candidate step is estimated by
        step-doubling: the step is solved once at h and again as two h/2
        substeps; for an integrator of order p the difference scales the
        true error by (2^p - 1). Steps exceeding lte_tol are rejected and
        halved (down to dt_min); comfortably accurate steps grow the next
        step (up to dt_max). The two-substep solution, which is the more
        accurate one, is the accepted result.

        Returns (results, time_points, cmap) like solve_transient; the time
        grid is non-uniform.
        """
        if t_stop <= t_start:
            raise ValueError(f"t_stop ({t_stop}) must be greater than t_start ({t_start})")
        if lte_tol <= 0.0:
            raise ValueError(f"lte_tol must be positive, got {lte_tol}")

        span = t_stop - t_start
        if dt_init is None:
            dt_init = span / 100.0
        if dt_max is None:
            dt_max = span / 10.0
        if dt_min is None:
            dt_min = span * 1e-9
        if dt_init <= 0.0 or dt_min <= 0.0 or dt_max < dt_min:
            raise ValueError("Adaptive timestep bounds must satisfy 0 < dt_min <= dt_max and dt_init > 0")

        order = 2.0 if method == "trapezoidal" else 1.0
        err_scale = (2.0 ** order) - 1.0

        nets_list, net_map, volt_comps = self._get_nets_and_mappings()
        size = len(net_map) + len(volt_comps)

        x, cmap = self.solve_dc()
        if uic:
            x = np.zeros(size)

        history = {"integration_method": method}
        self.init_energy_storage_history(x, history)

        xs = [x.copy()]
        ts = [t_start]
        t = t_start
        h = min(dt_init, dt_max)

        accepted = 0
        rejected = 0
        total_nr = 0
        h_min_used = float("inf")
        h_max_used = 0.0

        while t < t_stop - 1e-15:
            if accepted + rejected >= max_steps:
                raise ValueError(f"Adaptive transient exceeded {max_steps} steps; loosen lte_tol or raise dt_min")

            h = min(h, t_stop - t)

            # Candidate 1: single full step from the current state
            x_full, it_full, _ = self.newton_transient_step(t + h, h, history, x)

            # Candidate 2: two half steps on a checkpointed history copy
            hist_half = dict(history)
            x_h1, it_h1, _ = self.newton_transient_step(t + h / 2.0, h / 2.0, hist_half, x)
            self.update_energy_storage_history(x_h1, h / 2.0, hist_half, method=method)
            x_h2, it_h2, _ = self.newton_transient_step(t + h, h / 2.0, hist_half, x_h1)
            total_nr += it_full + it_h1 + it_h2

            err = float(np.linalg.norm(x_h2 - x_full)) / err_scale

            if err <= lte_tol or h <= dt_min * (1.0 + 1e-9):
                # Accept the two-substep result and its companion history
                self.update_energy_storage_history(x_h2, h / 2.0, hist_half, method=method)
                history = hist_half
                t += h
                x = x_h2
                xs.append(x.copy())
                ts.append(t)
                accepted += 1
                h_min_used = min(h_min_used, h)
                h_max_used = max(h_max_used, h)
                if err < 0.25 * lte_tol:
                    h = min(h * 2.0, dt_max)
            else:
                rejected += 1
                h = max(h / 2.0, dt_min)

        results = np.column_stack(xs)

        self.last_solve_stats = {
            "analysis": "transient_adaptive",
            "matrix_size": size,
            "method": method,
            "timesteps": accepted,
            "rejected_steps": rejected,
            "newton_iterations": total_nr,
            "lte_tol": lte_tol,
            "dt_min_used": h_min_used,
            "dt_max_used": h_max_used
        }

        return results, ts, cmap

    def solve_dc_sweep(self, component_id: str, param_name: str, start: float, stop: float,
                       points: int = 25) -> Tuple[List[float], np.ndarray, Dict[str, int]]:
        """
        Sweeps one component parameter across a linear range and computes the
        DC operating point at each value (SPICE '.dc' analysis). The swept
        parameter is restored afterwards. Returns (values, results, cmap)
        where results is a (size x points) matrix of operating points.
        """
        points = int(points)
        if points < 2:
            raise ValueError(f"DC sweep needs at least 2 points, got {points}")

        node = next((n for n in self.nodes if n["id"] == component_id), None)
        if node is None:
            raise ValueError(f"Component '{component_id}' not found for DC sweep")

        params = node.setdefault("params", {})
        had_param = param_name in params
        original = params.get(param_name)

        values = [float(v) for v in np.linspace(start, stop, points)]
        _, net_map, volt_comps = self._get_nets_and_mappings()
        size = len(net_map) + len(volt_comps)
        results = np.zeros((size, points))

        cmap: Dict[str, int] = {}
        total_iterations = 0
        all_converged = True
        try:
            for k, value in enumerate(values):
                params[param_name] = value
                x, cmap = self.solve_dc()
                results[:, k] = x
                total_iterations += self.last_solve_stats["newton_iterations"]
                all_converged = all_converged and self.last_solve_stats["converged"]
        finally:
            if had_param:
                params[param_name] = original
            else:
                params.pop(param_name, None)

        self.last_solve_stats = {
            "analysis": "dc_sweep",
            "matrix_size": size,
            "points": points,
            "component": component_id,
            "param": param_name,
            "newton_iterations": total_iterations,
            "converged": all_converged
        }

        return values, results, cmap

    def _assemble_ac(self, omega: float, x_op: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        """
        Assembles the complex-valued small-signal MNA system at angular
        frequency omega, with nonlinear elements linearized around the DC
        operating point x_op.
        """
        nets_list, net_map, volt_comps = self._get_nets_and_mappings()
        num_nets = len(net_map)
        size = num_nets + len(volt_comps)

        A = np.zeros((size, size), dtype=complex)
        z = np.zeros(size, dtype=complex)

        volt_map = {comp["id"]: num_nets + i for i, comp in enumerate(volt_comps)}

        for idx in range(num_nets):
            A[idx, idx] += self.g_min

        def pin_index(pins: Dict[str, str], pin_name: str) -> Optional[int]:
            return net_map.get(pins.get(pin_name, "n0"))

        # AC drive selection: sources carrying an explicit ac_mag are driven;
        # if none declares one, the first voltage source is driven at 1V
        # so an unannotated circuit still produces a transfer function.
        has_explicit_drive = any(
            "ac_mag" in node.get("params", {})
            for node in self.nodes
            if node["type"] in ("voltage_source", "current_source")
        )
        implicit_drive_id = None
        if not has_explicit_drive:
            for node in self.nodes:
                if node["type"] == "voltage_source":
                    implicit_drive_id = node["id"]
                    break

        def ac_drive(node: Dict[str, Any]) -> complex:
            params = node.get("params", {})
            if "ac_mag" in params:
                mag = float(params["ac_mag"])
                phase_deg = float(params.get("ac_phase", 0.0))
                return mag * complex(math.cos(math.radians(phase_deg)),
                                     math.sin(math.radians(phase_deg)))
            if node["id"] == implicit_drive_id:
                return complex(1.0, 0.0)
            return complex(0.0, 0.0)

        for node in self.nodes:
            n_type = node["type"]
            n_id = node["id"]
            pins = node.get("pins", {})
            params = node.get("params", {})

            if n_type == "resistor":
                r_val = _positive_param(params, "R", 1000.0, n_id)
                _stamp_conductance(A, pin_index(pins, "a"), pin_index(pins, "b"), 1.0 / r_val)

            elif n_type == "capacitor":
                c_val = _positive_param(params, "C", 1e-6, n_id)
                _stamp_conductance(A, pin_index(pins, "a"), pin_index(pins, "b"), 1j * omega * c_val)

            elif n_type == "inductor":
                l_val = _positive_param(params, "L", 1e-3, n_id)
                _stamp_conductance(A, pin_index(pins, "a"), pin_index(pins, "b"), 1.0 / (1j * omega * l_val))

            elif n_type == "diode":
                # Small-signal conductance at the DC operating point
                ia = pin_index(pins, "anode")
                ic = pin_index(pins, "cathode")
                Is = float(params.get("Is", 1e-14))
                N = float(params.get("N", 1.0))
                Vt = float(params.get("Vt", 0.02585))

                v_a = x_op[ia] if ia is not None else 0.0
                v_c = x_op[ic] if ic is not None else 0.0
                v_d = max(-10.0, min(v_a - v_c, 2.0))
                g_d = (Is / (N * Vt)) * math.exp(v_d / (N * Vt))
                _stamp_conductance(A, ia, ic, g_d)

            elif n_type == "current_source":
                # Injects only its declared AC magnitude (DC value is bias)
                _stamp_current_injection(z, pin_index(pins, "a"), pin_index(pins, "b"), -ac_drive(node))

            elif n_type == "voltage_source":
                br_idx = volt_map[n_id]
                ia = pin_index(pins, "a")
                ib = pin_index(pins, "b")
                _require_wired_branch(n_id, n_type, ia, ib)
                _stamp_voltage_branch(A, ia, ib, br_idx)
                z[br_idx] = ac_drive(node)

            elif n_type == "opamp":
                ini = pin_index(pins, "non_inverting")
                iinv = pin_index(pins, "inverting")
                iout = pin_index(pins, "out")

                gain = float(params.get("gain", 1e5))
                Rin = _positive_param(params, "Rin", 1e6, n_id)
                Rout = float(params.get("Rout", 50.0))

                _stamp_conductance(A, ini, iinv, 1.0 / Rin)

                br_idx = volt_map[n_id]
                _stamp_voltage_branch(A, iout, None, br_idx)
                if ini is not None:
                    A[br_idx, ini] -= gain
                if iinv is not None:
                    A[br_idx, iinv] += gain
                A[br_idx, br_idx] -= Rout
                z[br_idx] = 0.0

            elif n_type in ("nmos", "pmos"):
                # Small-signal gm/gds at the DC operating point
                self._stamp_mos(A, None, node, x_op)

            elif n_type in ("bjt_npn", "bjt_pnp"):
                self._stamp_bjt(A, None, node, x_op)

            elif n_type == "digital_interface_out":
                # A driven rail is an AC ground
                br_idx = volt_map[n_id]
                ia = pin_index(pins, "analog_out")
                _require_wired_branch(n_id, n_type, ia, None)
                _stamp_voltage_branch(A, ia, None, br_idx)
                z[br_idx] = 0.0

        return A, z

    def solve_ac(self, f_start: float, f_stop: float, points_per_decade: int = 20) -> Tuple[List[float], np.ndarray, np.ndarray, Dict[str, int]]:
        """
        Computes the small-signal frequency response over a logarithmic sweep.

        The circuit is linearized at its DC operating point, then a complex
        MNA system is solved per frequency point. Sources with an 'ac_mag'
        parameter drive the sweep (magnitude + 'ac_phase' degrees); without
        any, the first voltage source is driven at 1V so results read
        directly as the transfer function.

        Returns:
            (frequencies, magnitude_db, phase_deg, complete_index_map)
            where magnitude_db and phase_deg are (size x num_points) arrays.
        """
        if f_start <= 0.0:
            raise ValueError(f"AC sweep f_start must be positive, got {f_start}")
        if f_stop <= f_start:
            raise ValueError(f"AC sweep f_stop ({f_stop}) must be greater than f_start ({f_start})")
        if points_per_decade < 1:
            raise ValueError(f"points_per_decade must be at least 1, got {points_per_decade}")

        # DC operating point for linearization of nonlinear devices
        x_op, cmap = self.solve_dc()

        nets_list, net_map, volt_comps = self._get_nets_and_mappings()
        size = len(net_map) + len(volt_comps)

        decades = math.log10(f_stop / f_start)
        num_points = max(2, int(math.ceil(decades * points_per_decade)) + 1)
        freqs = list(np.logspace(math.log10(f_start), math.log10(f_stop), num_points))

        magnitude_db = np.zeros((size, num_points))
        phase_deg = np.zeros((size, num_points))

        for k, freq in enumerate(freqs):
            A, z = self._assemble_ac(2.0 * math.pi * freq, x_op)
            x = np.linalg.solve(A, z)
            magnitude_db[:, k] = 20.0 * np.log10(np.maximum(np.abs(x), 1e-18))
            phase_deg[:, k] = np.degrees(np.angle(x))

        self.last_solve_stats = {
            "analysis": "ac",
            "matrix_size": size,
            "points": num_points,
            "f_start": f_start,
            "f_stop": f_stop
        }

        return freqs, magnitude_db, phase_deg, cmap

    def dc_operating_report(self, x: np.ndarray) -> Dict[str, Dict[str, float]]:
        """
        Derives per-component operating-point quantities (voltage, current,
        power) from a solved DC vector x. Branch currents follow the MNA
        convention: positive current flows into the source's positive pin,
        so a delivering source reports negative current and power.
        """
        nets_list, net_map, volt_comps = self._get_nets_and_mappings()
        volt_map = {comp["id"]: len(net_map) + i for i, comp in enumerate(volt_comps)}

        def net_voltage(pins: Dict[str, str], pin_name: str) -> float:
            idx = net_map.get(pins.get(pin_name, "n0"))
            return float(x[idx]) if idx is not None else 0.0

        report: Dict[str, Dict[str, float]] = {}
        for node in self.nodes:
            n_type = node["type"]
            n_id = node["id"]
            pins = node.get("pins", {})
            params = node.get("params", {})

            if n_type == "resistor":
                v = net_voltage(pins, "a") - net_voltage(pins, "b")
                i = v / _positive_param(params, "R", 1000.0, n_id)
                report[n_id] = {"v": v, "i": i, "p": v * i}
            elif n_type == "diode":
                v_d = net_voltage(pins, "anode") - net_voltage(pins, "cathode")
                Is = float(params.get("Is", 1e-14))
                N = float(params.get("N", 1.0))
                Vt = float(params.get("Vt", 0.02585))
                v_lim = max(-10.0, min(v_d, 2.0))
                i = Is * (math.exp(v_lim / (N * Vt)) - 1.0)
                report[n_id] = {"v": v_d, "i": i, "p": v_d * i}
            elif n_type == "voltage_source":
                v = net_voltage(pins, "a") - net_voltage(pins, "b")
                i = float(x[volt_map[n_id]])
                report[n_id] = {"v": v, "i": i, "p": v * i}
            elif n_type == "current_source":
                v = net_voltage(pins, "a") - net_voltage(pins, "b")
                i = float(params.get("I", 0.0))
                report[n_id] = {"v": v, "i": i, "p": v * i}
            elif n_type == "capacitor":
                v = net_voltage(pins, "a") - net_voltage(pins, "b")
                report[n_id] = {"v": v, "i": 0.0, "p": 0.0}
            elif n_type == "opamp":
                v_out = net_voltage(pins, "out")
                i_out = float(x[volt_map[n_id]])
                report[n_id] = {"v": v_out, "i": i_out, "p": v_out * i_out}
            elif n_type in ("nmos", "pmos"):
                ss = self._mos_small_signal(node, x)
                report[n_id] = {
                    "vgs": ss["vgs"], "vds": ss["vds"], "i": ss["i_d"],
                    "gm": ss["gm"], "p": ss["vds"] * ss["i_d"]
                }
            elif n_type in ("bjt_npn", "bjt_pnp"):
                ss = self._bjt_small_signal(node, x)
                i_c, i_b, _ = ss["i_terms"]
                report[n_id] = {
                    "vbe": ss["vbe"], "vce": ss["vce"],
                    "ic": i_c, "ib": i_b,
                    "p": ss["vce"] * i_c
                }

        return report

    def assemble_mesh(self) -> Tuple[np.ndarray, np.ndarray, List[List[str]]]:
        """
        Builds the fundamental loop cycle-basis Mesh matrix equations:
            Zm * im = em
        Selects a DFS spanning tree to locate planar/non-planar cotree loops.
        """
        # Graph construction
        # Identify two-terminal components
        two_term_nodes = []
        for node in self.nodes:
            if node["type"] in ("resistor", "capacitor", "inductor", "voltage_source", "current_source", "diode"):
                two_term_nodes.append(node)
                
        # Get Nets list
        nets_list, net_map, _ = self._get_nets_and_mappings()
        
        # Build adjacency list representation for DFS spanning tree
        adj: Dict[str, List[Tuple[str, str]]] = {net: [] for net in nets_list}
        branches = []
        
        for node in two_term_nodes:
            pins = node["pins"]
            n_keys = list(pins.keys())
            if len(n_keys) != 2:
                continue
            na = pins[n_keys[0]]
            nb = pins[n_keys[1]]
            
            br_id = node["id"]
            branches.append({
                "id": br_id,
                "u": na,
                "v": nb,
                "node": node
            })
            adj[na].append((nb, br_id))
            adj[nb].append((na, br_id))

        # Run an iterative DFS to find the spanning forest (recursion would
        # overflow the interpreter stack on large net chains)
        visited_nets = set()
        tree_edges = set()
        parent_map = {}  # net -> (parent_net, branch_id)

        for net in nets_list:
            if net in visited_nets:
                continue
            visited_nets.add(net)
            stack = [net]
            while stack:
                u = stack.pop()
                for v, br_id in adj[u]:
                    if v not in visited_nets:
                        visited_nets.add(v)
                        tree_edges.add(br_id)
                        parent_map[v] = (u, br_id)
                        stack.append(v)
                
        # Locate Cotree edges (fundamental loops)
        cotree_branches = []
        for br in branches:
            if br["id"] not in tree_edges:
                cotree_branches.append(br)
                
        num_loops = len(cotree_branches)
        num_branches = len(branches)
        
        # Build Cycle-Branch incidence matrix C (size Loop x Branch)
        C = np.zeros((num_loops, num_branches))
        branch_id_to_idx = {br["id"]: idx for idx, br in enumerate(branches)}
        
        # Helper to find path in spanning tree
        def get_tree_path(start, end) -> List[Tuple[str, str, int]]:
            # Retraces DFS parent map paths to join nodes
            p1 = []
            curr = start
            while curr in parent_map:
                p, b = parent_map[curr]
                p1.append((curr, p, b))
                curr = p
            p2 = []
            curr = end
            while curr in parent_map:
                p, b = parent_map[curr]
                p2.append((curr, p, b))
                curr = p
            # Match paths to find LCA
            path_nets_p2 = [item[0] for item in p2] + [end]
            lca = None
            for item in p1:
                if item[1] in path_nets_p2:
                    lca = item[1]
                    break
            if lca is None:
                lca = "n0"  # Root fallback
                
            final_path = []
            # Traverse start -> LCA
            for item in p1:
                final_path.append((item[0], item[1], item[2], 1.0))
                if item[1] == lca:
                    break
            # Traverse LCA -> end
            temp_path = []
            for item in p2:
                temp_path.append((item[1], item[0], item[2], -1.0))
                if item[0] == lca:
                    break
            final_path.extend(reversed(temp_path))
            return final_path

        loop_paths = []
        for i, cotree_br in enumerate(cotree_branches):
            # Define loop cycle orientation starting along cotree branch
            loop_path = [cotree_br["id"]]
            br_idx = branch_id_to_idx[cotree_br["id"]]
            C[i, br_idx] = 1.0  # reference direction positive
            
            # Find path inside tree to close loop from v back to u
            path_in_tree = get_tree_path(cotree_br["v"], cotree_br["u"])
            for u_t, v_t, b_t, dir_sign in path_in_tree:
                loop_path.append(b_t)
                t_br_idx = branch_id_to_idx[b_t]
                # Compare orientation relative to reference branch direction
                C[i, t_br_idx] = dir_sign
                
            loop_paths.append(loop_path)

        # Assemble Branch Impedance Matrix Ze
        Ze = np.zeros((num_branches, num_branches))
        v_sources = np.zeros(num_branches)
        
        for idx, br in enumerate(branches):
            node = br["node"]
            n_type = node["type"]
            params = node.get("params", {})
            
            if n_type == "resistor":
                Ze[idx, idx] = float(params.get("R", 1000.0))
            elif n_type == "capacitor":
                # Instantaneous impedance equivalent
                c_val = float(params.get("C", 1e-6))
                Ze[idx, idx] = 1e-3 / c_val  # Nominal value
            elif n_type == "inductor":
                l_val = float(params.get("L", 1e-3))
                Ze[idx, idx] = l_val / 1e-3
            elif n_type == "voltage_source":
                v_sources[idx] = float(params.get("V", 5.0))
                Ze[idx, idx] = 1e-6  # Tiny series resistance
            elif n_type == "current_source":
                Ze[idx, idx] = 1e9  # Extremely large resistance
            elif n_type == "diode":
                Ze[idx, idx] = 50.0  # Linear approximation
                
        # Zm = C * Ze * C^T, em = C * v_sources
        Zm = np.matmul(np.matmul(C, Ze), C.T)
        em = np.matmul(C, v_sources)
        
        return Zm, em, loop_paths


class DiscreteEventScheduler:
    """
    Event-driven digital logic simulation scheduler.
    """
    def __init__(self):
        self.queue: List[Tuple[float, int, str, Any]] = []
        self.event_counter = 0
        self.states: Dict[str, Any] = {}
        self.output_logs: Dict[str, List[Tuple[float, Any]]] = {}
        self._topology_source: Any = None
        self._topology: Any = None

    def _build_gate_topology(self, netlist_comps: List[Dict[str, Any]]) -> Tuple[Dict, Dict, Dict, Dict]:
        """
        Builds gate pin/net lookup maps plus a net -> listening-gates index.
        Cached per netlist list object: the co-simulator calls run_until
        repeatedly with the same component list, and the topology (pins/nets)
        must not change between those calls.
        """
        if self._topology_source is netlist_comps:
            return self._topology

        gate_inputs: Dict[str, Dict[str, str]] = {}   # node_id -> {pin_name: net_name}
        gate_outputs: Dict[str, Dict[str, str]] = {}  # node_id -> {pin_name: net_name}
        gate_by_id: Dict[str, Dict[str, Any]] = {}
        net_listeners: Dict[str, List[str]] = {}      # net_name -> [gate ids reading it]

        for node in netlist_comps:
            n_id = node["id"]
            n_type = node["type"]
            if not n_type.startswith("digital_"):
                continue
            gate_by_id[n_id] = node
            gate_inputs[n_id] = {}
            gate_outputs[n_id] = {}

            for pin, net in node.get("pins", {}).items():
                if pin in ("out", "q", "q_bar"):
                    gate_outputs[n_id][pin] = net
                else:
                    gate_inputs[n_id][pin] = net
                    listeners = net_listeners.setdefault(net, [])
                    if n_id not in listeners:
                        listeners.append(n_id)

        self._topology_source = netlist_comps
        self._topology = (gate_inputs, gate_outputs, gate_by_id, net_listeners)
        return self._topology

    def schedule_event(self, t: float, node_id: str, val: Any) -> None:
        """
        Pushes a future digital transition into the priority queue.
        """
        self.event_counter += 1
        heapq.heappush(self.queue, (t, self.event_counter, node_id, val))

    def evaluate_gate(self, g_type: str, inputs: Dict[str, Any], params: Dict[str, Any]) -> Any:
        """
        Executes strict truth evaluation for standard digital gates.
        Inputs contains pin name -> state mapping. State can be '0', '1', 'X', 'Z'.
        """
        vals = list(inputs.values())
        if g_type == "digital_not":
            v = vals[0] if vals else "X"
            if v == "0":
                return "1"
            elif v == "1":
                return "0"
            return "X"
            
        elif g_type == "digital_and":
            if "0" in vals:
                return "0"
            if all(v == "1" for v in vals):
                return "1"
            return "X"
            
        elif g_type == "digital_or":
            if "1" in vals:
                return "1"
            if all(v == "0" for v in vals):
                return "0"
            return "X"
            
        elif g_type == "digital_nand":
            if "0" in vals:
                return "1"
            if all(v == "1" for v in vals):
                return "0"
            return "X"
            
        elif g_type == "digital_nor":
            if "1" in vals:
                return "0"
            if all(v == "0" for v in vals):
                return "1"
            return "X"
            
        elif g_type == "digital_xor":
            if "X" in vals or "Z" in vals:
                return "X"
            # Count odd number of '1's
            ones = sum(1 for v in vals if v == "1")
            return "1" if ones % 2 != 0 else "0"
            
        return "X"

    def run_until(self, t_limit: float, netlist_comps: List[Dict[str, Any]]) -> Dict[str, List[Tuple[float, Any]]]:
        """
        Processes scheduled event queue transitions until t_limit.
        Evaluates interconnected gates on output mutations.
        """
        gate_inputs, gate_outputs, gate_by_id, net_listeners = self._build_gate_topology(netlist_comps)

        # Main timeline scheduler loop
        while self.queue and self.queue[0][0] <= t_limit:
            t, _, target_net, new_val = heapq.heappop(self.queue)

            # Skip redundant states
            if self.states.get(target_net) == new_val:
                continue

            self.states[target_net] = new_val
            self.output_logs.setdefault(target_net, []).append((t, new_val))

            # Re-evaluate downstream gates that read this mutated net
            for g_id in net_listeners.get(target_net, ()):
                inputs_map = gate_inputs[g_id]
                curr_input_states = {pin: self.states.get(net, "0") for pin, net in inputs_map.items()}

                gate = gate_by_id[g_id]
                t_d = float(gate.get("params", {}).get("delay", 1e-9))

                # Evaluate outputs and schedule future updates
                out_val = self.evaluate_gate(gate["type"], curr_input_states, gate.get("params", {}))
                for out_pin, out_net in gate_outputs[g_id].items():
                    self.schedule_event(t + t_d, out_net, out_val)

        return self.output_logs


class MixedSignalCoSimulator:
    """
    Orchestrates boundary co-simulation between analog MNA solver
    and digital event schedulers.
    """
    def __init__(self, analog_solver: ContinuousSolver, digital_scheduler: DiscreteEventScheduler):
        self.analog = analog_solver
        self.digital = digital_scheduler
        
    def step_co_simulation(self, t_start: float, t_stop: float, base_dt: float) -> Dict[str, Any]:
        """
        Performs synchronized continuous-discrete progression.
        """
        _, net_map, _ = self.analog._get_nets_and_mappings()

        t = t_start
        analog_results = []
        analog_times = []

        # Initial analog state
        x, cmap = self.analog.solve_dc()
        analog_results.append(x.copy())
        analog_times.append(t)

        history = {"integration_method": "backward_euler"}
        self.analog.init_energy_storage_history(x, history)

        # Boundary Interface Mappings
        # Digital outputs drive analog voltage sources.
        # Analog nodes drive digital events via comparators.
        boundary_digital_outputs = []
        boundary_comparators = []
        for node in self.analog.nodes:
            if node["type"] == "digital_interface_out":
                boundary_digital_outputs.append(node)
            elif node["type"] == "analog_comparator":
                boundary_comparators.append(node)

        # Loop until t_stop is reached
        while t < t_stop:
            # Check next digital event time
            next_dig_t = self.digital.queue[0][0] if self.digital.queue else t_stop
            
            # Determine maximum timestep
            dt = min(base_dt, next_dig_t - t)
            if dt < 1e-13:
                # Digital event lands exactly at t
                self.digital.run_until(t, self.analog.nodes)
                
                # Update boundary voltage outputs in MNA representation
                for node in boundary_digital_outputs:
                    digital_net = node["pins"]["digital_in"]
                    analog_out_net = node["pins"]["analog_out"]
                    d_state = self.digital.states.get(digital_net, "0")
                    # Set corresponding voltage parameter
                    v_target = 5.0 if d_state == "1" else 0.0
                    node["params"]["V"] = v_target
                    
                dt = min(base_dt, (self.digital.queue[0][0] if self.digital.queue else t_stop) - t)
                if dt < 1e-13:
                    dt = base_dt  # safety override to progress clock
                    
            t_next = t + dt

            # Solve analog MNA step
            x_curr, _, _ = self.analog.newton_transient_step(t_next, dt, history, x, max_iter=50)


            # Check comparator crossings
            for comp in boundary_comparators:
                ref_v = float(comp["params"].get("threshold", 2.5))
                target_an = comp["pins"]["analog_in"]
                target_di = comp["pins"]["digital_out"]
                
                ia = net_map.get(target_an)
                v_an = x_curr[ia] if ia is not None else 0.0
                v_an_prev = x[ia] if ia is not None else 0.0
                
                # Check crossing
                if v_an_prev < ref_v <= v_an:
                    # Rising edge crossing: interpolate crossing time
                    ratio = (ref_v - v_an_prev) / max(1e-12, v_an - v_an_prev)
                    t_cross = t + ratio * dt
                    self.digital.schedule_event(t_cross, target_di, "1")
                elif v_an_prev >= ref_v > v_an:
                    # Falling edge
                    ratio = (v_an_prev - ref_v) / max(1e-12, v_an_prev - v_an)
                    t_cross = t + ratio * dt
                    self.digital.schedule_event(t_cross, target_di, "0")

            # Update timeline progress
            x = x_curr
            t = t_next
            analog_results.append(x.copy())
            analog_times.append(t)

            # Update capacitor/inductor transient histories (backward Euler)
            self.analog.update_energy_storage_history(x, dt, history)

        # Final digital processing
        self.digital.run_until(t_stop, self.analog.nodes)

        return {
            "analog_waveforms": np.array(analog_results).T,
            "analog_times": analog_times,
            "analog_map": cmap,
            "digital_waveforms": self.digital.output_logs
        }
