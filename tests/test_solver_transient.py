import pytest
import numpy as np
import math
from voltcraft.engine.solver import ContinuousSolver

def test_rc_transient_charging():
    # 5V voltage source, R=1k resistor in series with C=10uF capacitor to GND.
    # Time constant tau = R*C = 10 ms.
    # At t = tau = 10ms, Vc should be 5 * (1 - e^-1) = 3.1606V.
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
            "id": "C1",
            "type": "capacitor",
            "params": {"C": 1e-5},
            "pins": {"a": "n2", "b": "n0"}
        }
    ]
    edges = []
    
    solver = ContinuousSolver(nodes, edges)
    
    # 1. Test Backward Euler
    results_be, times_be, cmap_be = solver.solve_transient(0.0, 0.05, 0.001, method="backward_euler", uic=True)
    
    idx_n2 = cmap_be["n2"]
    
    # Let's find index at t = 10ms (index 10)
    idx_10ms = min(range(len(times_be)), key=lambda i: abs(times_be[i] - 0.010))
    v_10ms_be = results_be[idx_n2, idx_10ms]
    
    # Expected Vc(10ms) is 5 * (1 - exp(-1)) = 3.1606 V
    v_expected = 5.0 * (1.0 - math.exp(-1.0))
    
    assert np.isclose(v_10ms_be, v_expected, atol=0.1)  # Backward Euler is 1st order
    
    # 2. Test Trapezoidal (2nd order, should be highly accurate)
    results_tr, times_tr, cmap_tr = solver.solve_transient(0.0, 0.05, 0.001, method="trapezoidal", uic=True)
    idx_10ms_tr = min(range(len(times_tr)), key=lambda i: abs(times_tr[i] - 0.010))
    v_10ms_tr = results_tr[idx_n2, idx_10ms_tr]
    
    assert np.isclose(v_10ms_tr, v_expected, atol=0.1)

def test_rlc_inductor_charging():
    # 5V voltage source, R=100 in series with L=1H to GND
    # Tau = L / R = 10ms.
    # At t = 10ms, the current should reach 50mA * (1 - e^-1) = 31.6mA.
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
            "params": {"R": 100.0},
            "pins": {"a": "n1", "b": "n2"}
        },
        {
            "id": "L1",
            "type": "inductor",
            "params": {"L": 1.0},
            "pins": {"a": "n2", "b": "n0"}
        }
    ]
    edges = []
    
    solver = ContinuousSolver(nodes, edges)
    results, times, cmap = solver.solve_transient(0.0, 0.05, 0.001, method="trapezoidal", uic=True)
    
    # Inductor voltage is V(n2)
    idx_n2 = cmap["n2"]
    idx_10ms = min(range(len(times)), key=lambda i: abs(times[i] - 0.010))
    v_10ms = results[idx_n2, idx_10ms]
    
    # Expected inductor voltage is 5 * exp(-1) = 1.839V
    v_expected = 5.0 * math.exp(-1.0)
    
    assert np.isclose(v_10ms, v_expected, atol=0.1)

def test_digital_interface_out_transient():
    from voltcraft.engine.solver import DiscreteEventScheduler, MixedSignalCoSimulator
    # Set up a circuit with 1 voltage source, 1 analog comparator, 1 digital inverter, and 1 digital_interface_out
    # Analog input is a 5V sinusoidal source at 250Hz. Analog comparator threshold is 2.5V.
    # The comparator output d_comp goes to digital inverter output d_inv.
    # d_inv drives digital_interface_out to v_out.
    # Therefore, when analog source V1 crosses 2.5V rising (at t=0.5ms), d_comp is scheduled to 1,
    # d_inv becomes 0, and v_out goes to 0V.
    
    nodes = [
        {
            "id": "V1",
            "type": "voltage_source",
            "params": {"V": 5.0, "freq": 250.0, "phase": 0.0}, # 250Hz sine wave
            "pins": {"a": "in_an", "b": "n0"}
        },
        {
            "id": "COMP1",
            "type": "analog_comparator",
            "params": {"threshold": 2.5},
            "pins": {"analog_in": "in_an", "digital_out": "d_comp"}
        },
        {
            "id": "INV1",
            "type": "digital_not",
            "params": {"delay": 1e-6},
            "pins": {"in": "d_comp", "out": "d_inv"}
        },
        {
            "id": "INT1",
            "type": "digital_interface_out",
            "params": {"V": 5.0, "delay": 0.0},
            "pins": {"digital_in": "d_inv", "analog_out": "v_out"}
        }
    ]
    edges = []
    
    analog = ContinuousSolver(nodes, edges)
    digital = DiscreteEventScheduler()
    # Initialize the queue
    digital.schedule_event(0.0, "d_comp", "0")
    
    cosim = MixedSignalCoSimulator(analog, digital)
    res = cosim.step_co_simulation(0.0, 0.001, 1e-4)
    
    a_waves = res["analog_waveforms"]
    a_map = res["analog_map"]
    
    # At late simulation time (t=1ms), v_out should be 0.0V since d_comp = 1 -> d_inv = 0 -> v_out = 0V
    v_out_final = a_waves[a_map["v_out"]][-1]
    assert np.isclose(v_out_final, 0.0, atol=1e-3)


