import numpy as np
import heapq
import math
from typing import Dict, Any, List, Tuple

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
        
    def _get_nets_and_mappings(self) -> Tuple[List[str], Dict[str, int], List[Dict[str, Any]]]:
        """
        Gathers all unique nets in the circuit and assigns matrix indices.
        Excludes 'n0' (ground) from the KCL index mapping.
        """
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
            if node["type"] in ("voltage_source", "opamp"):
                voltage_components.append(node)
                
        return nets_list, net_map, voltage_components

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
            
        # Process each component
        for node in self.nodes:
            n_type = node["type"]
            n_id = node["id"]
            pins = node.get("pins", {})
            params = node.get("params", {})
            
            if n_type == "resistor":
                r_val = float(params.get("R", 1000.0))
                g_val = 1.0 / r_val
                na = pins.get("a", "n0")
                nb = pins.get("b", "n0")
                
                ia = net_map.get(na)
                ib = net_map.get(nb)
                
                if ia is not None:
                    A[ia, ia] += g_val
                if ib is not None:
                    A[ib, ib] += g_val
                if ia is not None and ib is not None:
                    A[ia, ib] -= g_val
                    A[ib, ia] -= g_val
                    
            elif n_type == "current_source":
                i_val = float(params.get("I", 0.0))
                na = pins.get("a", "n0")
                nb = pins.get("b", "n0")
                
                ia = net_map.get(na)
                ib = net_map.get(nb)
                
                # I source flows out of a, into b
                if ia is not None:
                    z[ia] -= i_val
                if ib is not None:
                    z[ib] += i_val
                    
            elif n_type == "voltage_source":
                v_val = float(params.get("V", 5.0))
                # Sinusoidal or transient function support
                if "freq" in params:
                    freq = float(params["freq"])
                    phase = float(params.get("phase", 0.0))
                    v_val = v_val * math.sin(2.0 * math.pi * freq * t + phase)
                    
                na = pins.get("a", "n0")  # Positive
                nb = pins.get("b", "n0")  # Negative
                
                ia = net_map.get(na)
                ib = net_map.get(nb)
                br_idx = volt_map[n_id]
                
                if ia is not None:
                    A[ia, br_idx] += 1.0
                    A[br_idx, ia] += 1.0
                if ib is not None:
                    A[ib, br_idx] -= 1.0
                    A[br_idx, ib] -= 1.0
                z[br_idx] = v_val
                
            elif n_type == "diode":
                na = pins.get("anode", "n0")
                nc = pins.get("cathode", "n0")
                
                ia = net_map.get(na)
                ic = net_map.get(nc)
                
                Is = float(params.get("Is", 1e-14))
                N = float(params.get("N", 1.0))
                Vt = float(params.get("Vt", 0.02585))
                
                # Extract previous diode voltage for NR linearization
                v_d = 0.0
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
                i_eq = g_d * v_d - i_d
                
                if ia is not None:
                    A[ia, ia] += g_d
                if ic is not None:
                    A[ic, ic] += g_d
                if ia is not None and ic is not None:
                    A[ia, ic] -= g_d
                    A[ic, ia] -= g_d
                    
                if ia is not None:
                    z[ia] -= i_eq
                if ic is not None:
                    z[ic] += i_eq
                    
            elif n_type == "capacitor":
                c_val = float(params.get("C", 1e-6))
                na = pins.get("a", "n0")
                nb = pins.get("b", "n0")
                
                ia = net_map.get(na)
                ib = net_map.get(nb)
                
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
                    
                # Resistor + Current source parallel stamp
                if ia is not None:
                    A[ia, ia] += g_eq
                if ib is not None:
                    A[ib, ib] += g_eq
                if ia is not None and ib is not None:
                    A[ia, ib] -= g_eq
                    A[ib, ia] -= g_eq
                    
                if ia is not None:
                    z[ia] += i_eq
                if ib is not None:
                    z[ib] -= i_eq
                    
            elif n_type == "inductor":
                l_val = float(params.get("L", 1e-3))
                na = pins.get("a", "n0")
                nb = pins.get("b", "n0")
                
                ia = net_map.get(na)
                ib = net_map.get(nb)
                
                prev_v = float(history.get(f"{n_id}_v", 0.0))
                prev_i = float(history.get(f"{n_id}_i", 0.0))
                
                method = history.get("integration_method", "backward_euler")
                if method == "trapezoidal":
                    g_eq = dt / (2.0 * l_val)
                    i_eq = g_eq * prev_v + prev_i
                else:
                    g_eq = dt / l_val
                    i_eq = prev_i
                    
                if ia is not None:
                    A[ia, ia] += g_eq
                if ib is not None:
                    A[ib, ib] += g_eq
                if ia is not None and ib is not None:
                    A[ia, ib] -= g_eq
                    A[ib, ia] -= g_eq
                    
                if ia is not None:
                    z[ia] -= i_eq
                if ib is not None:
                    z[ib] += i_eq
                    
            elif n_type == "opamp":
                non_inv = pins.get("non_inverting", "n0")
                inv = pins.get("inverting", "n0")
                out = pins.get("out", "n0")
                
                ini = net_map.get(non_inv)
                iinv = net_map.get(inv)
                iout = net_map.get(out)
                
                gain = float(params.get("gain", 1e5))
                Rin = float(params.get("Rin", 1e6))
                Rout = float(params.get("Rout", 50.0))
                
                # Stamp Rin between terminals
                g_in = 1.0 / Rin
                if ini is not None:
                    A[ini, ini] += g_in
                if iinv is not None:
                    A[iinv, iinv] += g_in
                if ini is not None and iinv is not None:
                    A[ini, iinv] -= g_in
                    A[iinv, ini] -= g_in
                    
                # Output branch: v_out - gain*(v_pos - v_neg) - Rout*i_out = 0
                br_idx = volt_map[n_id]
                
                if iout is not None:
                    A[iout, br_idx] += 1.0
                    A[br_idx, iout] += 1.0
                if ini is not None:
                    A[br_idx, ini] -= gain
                if iinv is not None:
                    A[br_idx, iinv] += gain
                    
                A[br_idx, br_idx] -= Rout
                z[br_idx] = 0.0

        # Construct comprehensive index mapping log
        complete_map = {}
        for k, v in net_map.items():
            complete_map[k] = v
        for k, v in volt_map.items():
            complete_map[f"branch_{k}"] = v
            
        return A, z, complete_map

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
        
        for step in range(1, steps + 1):
            scale = step / float(steps)
            
            # Setup scaled nodes
            scaled_nodes = []
            for node in self.nodes:
                node_copy = node.copy()
                if node["type"] == "voltage_source" and "V" in node["params"]:
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
                A, z, cmap = solver_step.assemble_mna(0.0, 1.0, history, prev_x=x)
                
                # Check condition number to diagnose stability
                cond = np.linalg.cond(A)
                if cond > 1e15:
                    # Inject Gmin boost dynamically to restore condition
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
                if diff < tol:
                    break
            else:
                # If convergence fails, return last best guess
                pass
                
        _, cmap, _ = self._get_nets_and_mappings()
        return x, cmap

    def solve_transient(self, t_start: float, t_stop: float, dt: float, method: str = "backward_euler", uic: bool = False) -> Tuple[np.ndarray, List[float], Dict[str, int]]:
        """
        Computes the continuous transient simulation waveform matrix over time.
        """
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
        
        # Initialize component state histories
        for node in self.nodes:
            n_type = node["type"]
            n_id = node["id"]
            pins = node.get("pins", {})
            na = pins.get("a", "n0")
            nb = pins.get("b", "n0")
            ia = net_map.get(na)
            ib = net_map.get(nb)
            
            if n_type == "capacitor":
                v_cap = (x[ia] if ia is not None else 0.0) - (x[ib] if ib is not None else 0.0)
                history[f"{n_id}_v"] = v_cap
                history[f"{n_id}_i"] = 0.0  # Initial current is 0
            elif n_type == "inductor":
                history[f"{n_id}_v"] = 0.0
                history[f"{n_id}_i"] = 0.0
                
        # Main transient integration loop
        t = t_start
        for step in range(1, steps + 1):
            t += dt
            time_points.append(t)
            
            # Newton-Raphson loop for each transient step
            x_prev = results[:, step - 1]
            x_curr = x_prev.copy()
            
            for nr_iter in range(80):
                A, z, _ = self.assemble_mna(t, dt, history, prev_x=x_curr)
                x_next = np.linalg.solve(A, z)
                
                # Check convergence
                diff = np.linalg.norm(x_next - x_curr)
                x_curr = x_next
                if diff < 1e-6:
                    break
            
            # Save results
            results[:, step] = x_curr
            
            # Update history states
            for node in self.nodes:
                n_type = node["type"]
                n_id = node["id"]
                pins = node.get("pins", {})
                params = node.get("params", {})
                na = pins.get("a", "n0")
                nb = pins.get("b", "n0")
                ia = net_map.get(na)
                ib = net_map.get(nb)
                
                if n_type == "capacitor":
                    c_val = float(params.get("C", 1e-6))
                    v_new = (x_curr[ia] if ia is not None else 0.0) - (x_curr[ib] if ib is not None else 0.0)
                    v_old = history[f"{n_id}_v"]
                    
                    # Compute current flowing through capacitor
                    if method == "trapezoidal":
                        g_eq = 2.0 * c_val / dt
                        i_cap = g_eq * (v_new - v_old) - history[f"{n_id}_i"]
                    else:
                        g_eq = c_val / dt
                        i_cap = g_eq * (v_new - v_old)
                        
                    history[f"{n_id}_v"] = v_new
                    history[f"{n_id}_i"] = i_cap
                    
                elif n_type == "inductor":
                    l_val = float(params.get("L", 1e-3))
                    v_new = (x_curr[ia] if ia is not None else 0.0) - (x_curr[ib] if ib is not None else 0.0)
                    v_old = history[f"{n_id}_v"]
                    i_old = history[f"{n_id}_i"]
                    
                    if method == "trapezoidal":
                        g_eq = dt / (2.0 * l_val)
                        i_ind = g_eq * (v_new + v_old) + i_old
                    else:
                        g_eq = dt / l_val
                        i_ind = g_eq * v_new + i_old
                        
                    history[f"{n_id}_v"] = v_new
                    history[f"{n_id}_i"] = i_ind
                    
        return results, time_points, cmap

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

        # Run DFS to find Spanning Forest
        visited_nets = set()
        tree_edges = set()
        parent_map = {}  # net -> (parent_net, branch_id)
        
        def dfs(u):
            visited_nets.add(u)
            for v, br_id in adj[u]:
                if v not in visited_nets:
                    tree_edges.add(br_id)
                    parent_map[v] = (u, br_id)
                    dfs(v)
                    
        for net in nets_list:
            if net not in visited_nets:
                dfs(net)
                
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
        # Map net links for quick traversal
        gate_inputs: Dict[str, Dict[str, str]] = {}  # node_id -> {pin_name: net_name}
        gate_outputs: Dict[str, Dict[str, str]] = {}  # node_id -> {pin_name: net_name}
        gate_by_id: Dict[str, Dict[str, Any]] = {}
        
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

        # Main timeline scheduler loop
        while self.queue and self.queue[0][0] <= t_limit:
            t, _, target_net, new_val = heapq.heappop(self.queue)
            
            # Skip redundant states
            if self.states.get(target_net) == new_val:
                continue
                
            self.states[target_net] = new_val
            if target_net not in self.output_logs:
                self.output_logs[target_net] = []
            self.output_logs[target_net].append((t, new_val))
            
            # Find downstream gates that use this mutated net as an input
            for g_id, inputs_map in gate_inputs.items():
                if target_net in inputs_map.values():
                    # Formulate input state dict
                    curr_input_states = {}
                    for pin, net in inputs_map.items():
                        curr_input_states[pin] = self.states.get(net, "0")
                        
                    gate = gate_by_id[g_id]
                    t_d = float(gate.get("params", {}).get("delay", 1e-9))
                    
                    # Evaluate outputs
                    out_val = self.evaluate_gate(gate["type"], curr_input_states, gate.get("params", {}))
                    
                    # Schedule future output updates
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
        nets_list, net_map, volt_comps = self.analog._get_nets_and_mappings()
        size = len(net_map) + len(volt_comps)
        
        t = t_start
        analog_results = []
        analog_times = []
        
        # Initial analog state
        x, cmap = self.analog.solve_dc()
        analog_results.append(x.copy())
        analog_times.append(t)
        
        history = {"integration_method": "backward_euler"}
        
        # Initialize capacitor/inductor companion histories
        for node in self.analog.nodes:
            n_type = node["type"]
            n_id = node["id"]
            pins = node.get("pins", {})
            ia = net_map.get(pins.get("a", "n0"))
            ib = net_map.get(pins.get("b", "n0"))
            
            if n_type == "capacitor":
                v_cap = (x[ia] if ia is not None else 0.0) - (x[ib] if ib is not None else 0.0)
                history[f"{n_id}_v"] = v_cap
                history[f"{n_id}_i"] = 0.0
            elif n_type == "inductor":
                history[f"{n_id}_v"] = 0.0
                history[f"{n_id}_i"] = 0.0

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
            x_curr = x.copy()
            for nr_iter in range(50):
                A, z, _ = self.analog.assemble_mna(t_next, dt, history, prev_x=x_curr)
                x_next = np.linalg.solve(A, z)
                diff = np.linalg.norm(x_next - x_curr)
                x_curr = x_next
                if diff < 1e-6:
                    break
                    
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
            
            # Update capacitor/inductor transient histories
            for node in self.analog.nodes:
                n_type = node["type"]
                n_id = node["id"]
                pins = node.get("pins", {})
                ia = net_map.get(pins.get("a", "n0"))
                ib = net_map.get(pins.get("b", "n0"))
                
                if n_type == "capacitor":
                    c_val = float(node["params"].get("C", 1e-6))
                    v_new = (x[ia] if ia is not None else 0.0) - (x[ib] if ib is not None else 0.0)
                    v_old = history[f"{n_id}_v"]
                    i_cap = (c_val / dt) * (v_new - v_old)
                    history[f"{n_id}_v"] = v_new
                    history[f"{n_id}_i"] = i_cap
                elif n_type == "inductor":
                    l_val = float(node["params"].get("L", 1e-3))
                    v_new = (x[ia] if ia is not None else 0.0) - (x[ib] if ib is not None else 0.0)
                    v_old = history[f"{n_id}_v"]
                    i_old = history[f"{n_id}_i"]
                    i_ind = (dt / l_val) * v_new + i_old
                    history[f"{n_id}_v"] = v_new
                    history[f"{n_id}_i"] = i_ind

        # Final digital processing
        self.digital.run_until(t_stop, self.analog.nodes)

        return {
            "analog_waveforms": np.array(analog_results).T,
            "analog_times": analog_times,
            "analog_map": cmap,
            "digital_waveforms": self.digital.output_logs
        }
