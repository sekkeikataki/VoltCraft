import pytest
import numpy as np
from voltcraft.engine.solver import ContinuousSolver

def test_voltage_divider_dc():
    # 10V voltage source in series with two 1k resistors
    # R1: node n1 to n2, R2: node n2 to n0 (GND)
    nodes = [
        {
            "id": "V1",
            "type": "voltage_source",
            "params": {"V": 10.0},
            "pins": {"a": "n1", "b": "n0"}
        },
        {
            "id": "R1",
            "type": "resistor",
            "params": {"R": 1000.0},
            "pins": {"a": "n1", "b": "n2"}
        },
        {
            "id": "R2",
            "type": "resistor",
            "params": {"R": 1000.0},
            "pins": {"a": "n2", "b": "n0"}
        }
    ]
    edges = []
    
    solver = ContinuousSolver(nodes, edges)
    x, cmap = solver.solve_dc()
    
    idx_n1 = cmap["n1"]
    idx_n2 = cmap["n2"]
    
    assert np.isclose(x[idx_n1], 10.0, atol=1e-4)
    assert np.isclose(x[idx_n2], 5.0, atol=1e-4)

def test_diode_voltage_drop_dc():
    # 5V voltage source, 1k resistor in series with a diode to GND
    # Anode = n2, Cathode = n0 (GND)
    nodes = [
        {
            "id": "V1",
            "type": "voltage_source",
            "params": {"V": 5.0},
            "pins": {"a": "n1", "b": "n0"}
        },
        {
            "id": "R1",
            "type": "resistor",
            "params": {"R": 1000.0},
            "pins": {"a": "n1", "b": "n2"}
        },
        {
            "id": "D1",
            "type": "diode",
            "params": {"Is": 1e-14, "N": 1.0, "Vt": 0.02585},
            "pins": {"anode": "n2", "cathode": "n0"}
        }
    ]
    edges = []
    
    solver = ContinuousSolver(nodes, edges)
    x, cmap = solver.solve_dc()
    
    idx_n2 = cmap["n2"]
    v_diode = x[idx_n2]
    
    # Standard silicon diode forward drop is ~0.6V to 0.75V
    assert 0.6 < v_diode < 0.75

def test_inverting_opamp_gain_dc():
    # Inverting amplifier. Vin = 1V. Rin = 1k, Rf = 2k. Expected Vout = -2V.
    # Non-inverting input to GND (n0)
    # Rin: n1 (Vin) to n2 (virtual ground)
    # Rf: n2 to n3 (Vout)
    nodes = [
        {
            "id": "V1",
            "type": "voltage_source",
            "params": {"V": 1.0},
            "pins": {"a": "n1", "b": "n0"}
        },
        {
            "id": "Rin",
            "type": "resistor",
            "params": {"R": 1000.0},
            "pins": {"a": "n1", "b": "n2"}
        },
        {
            "id": "Rf",
            "type": "resistor",
            "params": {"R": 2000.0},
            "pins": {"a": "n2", "b": "n3"}
        },
        {
            "id": "U1",
            "type": "opamp",
            "params": {"gain": 1e5, "Rin": 1e6, "Rout": 50.0},
            "pins": {"non_inverting": "n0", "inverting": "n2", "out": "n3"}
        }
    ]
    edges = []
    
    solver = ContinuousSolver(nodes, edges)
    x, cmap = solver.solve_dc()
    
    idx_n3 = cmap["n3"]
    v_out = x[idx_n3]
    
    # Output should be close to -2V
    assert np.isclose(v_out, -2.0, atol=1e-3)

def test_mesh_analysis_equations():
    # 5V voltage source, 1k resistor in series with a 2k resistor
    # Total loop impedance = 3000 ohms. Expected loop current = 1.667 mA.
    nodes = [
        {
            "id": "V1",
            "type": "voltage_source",
            "params": {"V": 5.0},
            "pins": {"a": "n1", "b": "n0"}
        },
        {
            "id": "R1",
            "type": "resistor",
            "params": {"R": 1000.0},
            "pins": {"a": "n1", "b": "n2"}
        },
        {
            "id": "R2",
            "type": "resistor",
            "params": {"R": 2000.0},
            "pins": {"a": "n2", "b": "n0"}
        }
    ]
    edges = []
    
    solver = ContinuousSolver(nodes, edges)
    Zm, em, loop_paths = solver.assemble_mesh()
    
    # Solve loop currents
    i_loop = np.linalg.solve(Zm, em)
    
    assert len(loop_paths) == 1
    # Loop impedance is 3000.000001 (due to V1 internal tiny shunt)
    assert np.isclose(Zm[0, 0], 3000.0, atol=1e-2)
    assert np.isclose(abs(em[0]), 5.0, atol=1e-3)
    assert np.isclose(abs(i_loop[0]), 5.0 / 3000.0, atol=1e-4)
