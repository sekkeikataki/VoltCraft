import pytest
from voltcraft.engine.solver import DiscreteEventScheduler, ContinuousSolver, MixedSignalCoSimulator

def test_digital_logic_gate_eval():
    scheduler = DiscreteEventScheduler()
    
    # Test NAND evaluations
    val1 = scheduler.evaluate_gate("digital_nand", {"a": "1", "b": "1"}, {})
    assert val1 == "0"
    
    val2 = scheduler.evaluate_gate("digital_nand", {"a": "1", "b": "0"}, {})
    assert val2 == "1"
    
    val3 = scheduler.evaluate_gate("digital_nand", {"a": "X", "b": "0"}, {})
    assert val3 == "1"  # NAND of anything and 0 is 1

def test_digital_event_scheduler_delay():
    scheduler = DiscreteEventScheduler()
    
    # 2 inverters in series
    # U1 (NOT): in=in_net, out=mid_net, delay=2ns
    # U2 (NOT): in=mid_net, out=out_net, delay=3ns
    components = [
        {
            "id": "U1",
            "type": "digital_not",
            "params": {"delay": 2e-9},
            "pins": {"a": "in_net", "out": "mid_net"}
        },
        {
            "id": "U2",
            "type": "digital_not",
            "params": {"delay": 3e-9},
            "pins": {"a": "mid_net", "out": "out_net"}
        }
    ]
    
    # Initial input node step to 1 at t=0
    scheduler.schedule_event(0.0, "in_net", "1")
    
    logs = scheduler.run_until(10e-9, components)
    
    # Input set to 1 at t=0
    assert scheduler.states["in_net"] == "1"
    # Mid-node should resolve to 0 at t=2ns
    assert scheduler.states["mid_net"] == "0"
    # Out-node should resolve to 1 at t=5ns (2ns + 3ns)
    assert scheduler.states["out_net"] == "1"
    
    # Check timings in logs
    mid_transitions = logs["mid_net"]
    assert mid_transitions[0] == (2e-9, "0")
    
    out_transitions = logs["out_net"]
    assert out_transitions[0] == (5e-9, "1")

def test_mixed_signal_co_simulation():
    # AC input sine voltage source, Analog comparator, and Digital inverter
    # Comparator threshold is 2.5V. When sine > 2.5V, digital input becomes 1.
    # Inverter flips digital input with 1ns delay.
    nodes = [
        {
            "id": "V1",
            "type": "voltage_source",
            "params": {"V": 5.0, "freq": 1000.0},  # 5V peak, 1kHz sine (T=1ms)
            "pins": {"a": "analog_sine", "b": "n0"}
        },
        {
            "id": "COMP1",
            "type": "analog_comparator",
            "params": {"threshold": 2.5},
            "pins": {"analog_in": "analog_sine", "digital_out": "cmp_out"}
        },
        {
            "id": "U1",
            "type": "digital_not",
            "params": {"delay": 1e-6},  # 1us delay
            "pins": {"a": "cmp_out", "out": "inv_out"}
        }
    ]
    edges = []
    
    analog_solver = ContinuousSolver(nodes, edges)
    digital_scheduler = DiscreteEventScheduler()
    
    cosim = MixedSignalCoSimulator(analog_solver, digital_scheduler)
    
    # Run co-simulation for 0.2ms
    res = cosim.step_co_simulation(0.0, 0.0002, 1e-5)
    
    # Verify comparator output crossing trigger
    assert "cmp_out" in res["digital_waveforms"]
    assert "inv_out" in res["digital_waveforms"]
    
    cmp_log = res["digital_waveforms"]["cmp_out"]
    inv_log = res["digital_waveforms"]["inv_out"]
    
    # Sine is 5 * sin(2*pi*1000 * t)
    # Reaches 2.5V when sin(2*pi*1000 * t) = 0.5 -> 2*pi*1000*t = pi/6 -> t = 1/12000 = 83.3us
    # Verify that first transition on cmp_out happens near 83.3us
    assert len(cmp_log) > 0
    t_crossing = cmp_log[0][0]
    assert abs(t_crossing - 8.33e-5) < 1e-5
    
    # Inverter fires 1us later
    assert len(inv_log) > 0
    t_inv = inv_log[0][0]
    assert abs(t_inv - (t_crossing + 1e-6)) < 1e-12
