"""Tests for sequential digital logic: D flip-flops, clock generators,
counters, shift registers, and mixed-signal clocking."""
import os

import numpy as np
import pytest
from fastapi.testclient import TestClient

from voltcraft.app import app, SCHEMATICS_DIR, active_schematics
from voltcraft.engine.solver import ContinuousSolver, DiscreteEventScheduler, MixedSignalCoSimulator

client = TestClient(app)
SEQ_FILE = "test_seq_digital.vcg.json"


@pytest.fixture(autouse=True)
def cleanup_files():
    yield
    for name in (SEQ_FILE, SEQ_FILE.replace(".vcg.json", ".drawio.xml")):
        path = os.path.join(SCHEMATICS_DIR, name)
        active_schematics.pop(path, None)
        if os.path.exists(path):
            os.remove(path)


def dff(node_id="FF1", d="d", clk="clk", q="q", q_bar="qb", delay=1e-9, init="0"):
    return {"id": node_id, "type": "digital_dff",
            "params": {"delay": delay, "init": init},
            "pins": {"d": d, "clk": clk, "q": q, "q_bar": q_bar}}


def clock(node_id="CLK1", out="clk", freq=1000.0, duty=0.5):
    return {"id": node_id, "type": "digital_clock",
            "params": {"freq": freq, "duty": duty}, "pins": {"out": out}}


def test_dff_latches_only_on_rising_edge():
    sched = DiscreteEventScheduler()
    comps = [dff()]
    # d goes high, then clock rises, then d drops, then clock falls
    sched.schedule_event(1e-6, "d", "1")
    sched.schedule_event(2e-6, "clk", "1")
    sched.schedule_event(3e-6, "d", "0")
    sched.schedule_event(4e-6, "clk", "0")
    logs = sched.run_until(1e-5, comps)

    # q latched "1" at the rising edge and held it through the d change
    # and the falling edge
    assert sched.states["q"] == "1"
    assert sched.states["qb"] == "0"
    q_log = logs["q"]
    assert len(q_log) == 1
    assert q_log[0][0] == pytest.approx(2e-6 + 1e-9, abs=1e-12)   # edge + delay
    assert q_log[0][1] == "1"


def test_dff_ignores_d_when_clock_is_idle():
    sched = DiscreteEventScheduler()
    comps = [dff()]
    sched.schedule_event(1e-6, "d", "1")
    sched.schedule_event(2e-6, "d", "0")
    sched.schedule_event(3e-6, "d", "1")
    logs = sched.run_until(1e-5, comps)
    # No clock edge ever fired: q keeps its seeded init and never logs
    assert sched.states["q"] == "0"
    assert "q" not in logs or len(logs["q"]) == 0


def test_dff_init_seeds_feedback():
    # Divide-by-two wiring (d <- q_bar): the first latched value follows
    # from the initial q state
    for init, first in (("0", "1"), ("1", "0")):
        sched = DiscreteEventScheduler()
        comps = [dff(d="qb", init=init)]
        sched.schedule_event(1e-6, "clk", "1")
        logs = sched.run_until(1e-5, comps)
        assert logs["q"][0][1] == first, f"init={init}"


def test_divide_by_two():
    # d <- q_bar toggles q on every rising clock edge: q runs at f/2
    sched = DiscreteEventScheduler()
    comps = [clock(freq=1000.0), dff(d="qb", clk="clk")]
    logs = sched.run_until(9.5e-3, comps)   # rising edges at 0..9 ms -> 10 edges

    clk_rises = [t for t, v in logs["clk"] if v == "1"]
    assert len(clk_rises) == 10

    q_log = logs["q"]
    assert len(q_log) == 10                 # one q toggle per rising edge
    assert [v for _, v in q_log] == ["1", "0"] * 5   # strict alternation
    # q period is twice the clock period
    q_rises = [t for t, v in q_log if v == "1"]
    spacings = np.diff(q_rises)
    assert np.allclose(spacings, 2e-3, atol=1e-9)


def test_ripple_counter_divides_by_four():
    # Stage 2 clocked by q_bar of stage 1: q2 toggles at f/4
    sched = DiscreteEventScheduler()
    comps = [
        clock(freq=1000.0),
        dff("FF1", d="qb1", clk="clk", q="q1", q_bar="qb1"),
        dff("FF2", d="qb2", clk="qb1", q="q2", q_bar="qb2"),
    ]
    logs = sched.run_until(16.5e-3, comps)  # ~17 clock rising edges

    q1_rises = [t for t, v in logs["q1"] if v == "1"]
    q2_rises = [t for t, v in logs["q2"] if v == "1"]
    # q1 at f/2 (period 2ms), q2 at f/4 (period 4ms)
    assert np.allclose(np.diff(q1_rises), 2e-3, atol=1e-8)
    assert np.allclose(np.diff(q2_rises), 4e-3, atol=1e-8)


def test_shift_register_stage_race():
    # Two DFFs on the SAME clock, d2 <- q1. When an edge fires, stage 2
    # must capture stage 1's OLD value (the propagation delay guarantees
    # q1 updates after the edge), so data marches exactly one stage/clock
    sched = DiscreteEventScheduler()
    comps = [
        clock(freq=1000.0),
        dff("FF1", d="din", clk="clk", q="q1", q_bar="qb1"),
        dff("FF2", d="q1", clk="clk", q="q2", q_bar="qb2"),
    ]
    sched.schedule_event(0.5e-3, "din", "1")   # goes high between edges 0 and 1
    logs = sched.run_until(4.5e-3, comps)

    q1_first = [t for t, v in logs["q1"] if v == "1"][0]
    q2_first = [t for t, v in logs["q2"] if v == "1"][0]
    assert q1_first == pytest.approx(1e-3 + 1e-9, abs=1e-12)   # edge at 1ms
    assert q2_first == pytest.approx(2e-3 + 1e-9, abs=1e-12)   # one clock later


def test_clock_edges_and_duty():
    sched = DiscreteEventScheduler()
    comps = [clock(freq=2000.0, duty=0.25)]
    logs = sched.run_until(4.75e-3, comps)   # rising at 0..4.5ms -> 10 edges

    rises = [t for t, v in logs["clk"] if v == "1"]
    falls = [t for t, v in logs["clk"] if v == "0"]
    assert len(rises) == 10
    assert np.allclose(np.diff(rises), 0.5e-3, atol=1e-12)
    # 25% duty: falling edge an eighth of a millisecond after each rise
    assert falls[0] == pytest.approx(0.125e-3, abs=1e-12)


def test_clock_idempotent_across_repeated_runs():
    # Extending the window must not duplicate or reorder edges
    sched = DiscreteEventScheduler()
    comps = [clock(freq=1000.0)]
    sched.run_until(4.5e-3, comps)
    logs = sched.run_until(9.5e-3, comps)

    ref = DiscreteEventScheduler()
    ref_logs = ref.run_until(9.5e-3, [clock(freq=1000.0)])
    assert logs["clk"] == ref_logs["clk"]

    # Strictly alternating values (a duplicate edge would break this)
    vals = [v for _, v in logs["clk"]]
    assert all(a != b for a, b in zip(vals, vals[1:]))


def test_clock_requires_positive_freq():
    sched = DiscreteEventScheduler()
    with pytest.raises(ValueError, match="positive freq"):
        sched.run_until(1e-3, [clock(freq=0.0)])


def test_mixed_mode_clocked_interface_drives_analog():
    # digital_clock -> DFF divide-by-two -> digital_interface_out -> RC:
    # the analog output must actually toggle between rails
    nodes = [
        clock(freq=2000.0),
        dff(d="qb", clk="clk"),
        {"id": "INT1", "type": "digital_interface_out", "params": {"V": 5.0},
         "pins": {"digital_in": "q", "analog_out": "v_out"}},
        {"id": "RL", "type": "resistor", "params": {"R": 1000.0},
         "pins": {"a": "v_out", "b": "n0"}},
    ]
    analog = ContinuousSolver(nodes, [])
    digital = DiscreteEventScheduler()
    cosim = MixedSignalCoSimulator(analog, digital)
    res = cosim.step_co_simulation(0.0, 4e-3, 5e-5)

    v_out = np.array(res["analog_waveforms"][res["analog_map"]["v_out"]])
    assert np.max(v_out) > 4.9
    assert np.min(v_out) < 0.1
    # q toggles at 1 kHz -> several full swings within 4 ms
    assert "q" in res["digital_waveforms"]
    assert len(res["digital_waveforms"]["q"]) >= 6


def test_api_digital_mode_ripple_counter():
    path = os.path.join(SCHEMATICS_DIR, SEQ_FILE)
    graph = {
        "schema_version": "1.0.0",
        "metadata": {"name": "Counter", "created_utc": "2026-05-23T20:38:00Z", "author_agent": "T"},
        "nodes": [
            dict(clock(freq=1000.0), pos={"x": 0, "y": 0}, rot=0),
            dict(dff("FF1", d="qb1", clk="clk", q="q1", q_bar="qb1"), pos={"x": 0, "y": 0}, rot=0),
            dict(dff("FF2", d="qb2", clk="qb1", q="q2", q_bar="qb2"), pos={"x": 0, "y": 0}, rot=0),
        ],
        "edges": [],
        "nets": ["n0", "clk", "q1", "qb1", "q2", "qb2"]
    }
    assert client.post("/api/agent/action", json={
        "action": "save_schematic", "params": {"path": path, "graph": graph}
    }).json()["status"] == "ok"
    assert client.post("/api/agent/action", json={
        "action": "load_schematic", "params": {"path": path}
    }).json()["status"] == "ok"

    r = client.post("/api/agent/action", json={
        "action": "run_simulation",
        "params": {"path": path, "mode": "digital", "params": {"t_stop": 0.0165}}
    })
    body = r.json()
    assert body["status"] == "ok"
    logs = body["data"]["logs"]
    q2_rises = [t for t, v in logs["q2"] if v == "1"]
    assert len(q2_rises) >= 3
    assert np.allclose(np.diff(q2_rises), 4e-3, atol=1e-8)   # f/4
