"""Analytic tests for oscilloscope-style waveform measurements."""
import math
import os

import numpy as np
import pytest
from fastapi.testclient import TestClient

from voltcraft.app import app, SCHEMATICS_DIR, active_schematics
from voltcraft.engine import measurements as m

client = TestClient(app)
MEAS_FILE = "test_measure_rc.vcg.json"


@pytest.fixture(autouse=True)
def cleanup_files():
    yield
    for name in (MEAS_FILE, MEAS_FILE.replace(".vcg.json", ".drawio.xml")):
        path = os.path.join(SCHEMATICS_DIR, name)
        active_schematics.pop(path, None)
        if os.path.exists(path):
            os.remove(path)


def sine(freq=1000.0, amp=2.0, offset=0.0, cycles=5, pts_per_cycle=400):
    n = int(cycles * pts_per_cycle)
    t = np.linspace(0.0, cycles / freq, n + 1)
    v = offset + amp * np.sin(2.0 * math.pi * freq * t)
    return t.tolist(), v.tolist()


def rc_step(tau=1e-3, final=5.0, pts=4000, span_taus=8):
    t = np.linspace(0.0, span_taus * tau, pts)
    v = final * (1.0 - np.exp(-t / tau))
    return t.tolist(), v.tolist()


def test_basic_stats_of_sine():
    t, v = sine(amp=2.0, offset=1.0, cycles=6)
    s = m.basic_stats(t, v)
    # +/-2 around a 1.0 offset
    assert s["max"] == pytest.approx(3.0, abs=1e-3)
    assert s["min"] == pytest.approx(-1.0, abs=1e-3)
    assert s["peak_to_peak"] == pytest.approx(4.0, abs=1e-3)
    # Integer cycles: mean is the offset, RMS is sqrt(offset^2 + (amp/sqrt2)^2)
    assert s["average"] == pytest.approx(1.0, abs=1e-3)
    assert s["rms"] == pytest.approx(math.sqrt(1.0 + 2.0), abs=1e-3)


def test_rms_of_zero_mean_sine():
    t, v = sine(amp=3.0, offset=0.0, cycles=8)
    s = m.basic_stats(t, v)
    assert s["average"] == pytest.approx(0.0, abs=1e-3)
    assert s["rms"] == pytest.approx(3.0 / math.sqrt(2.0), abs=2e-3)


def test_rc_rise_time_is_2p2_tau():
    # 10%-90% rise time of a first-order step is -tau*ln(0.9/0.1) = 2.1972*tau
    tau = 1e-3
    t, v = rc_step(tau=tau, final=5.0)
    rt = m.rise_time(t, v)
    assert rt == pytest.approx(2.19722 * tau, rel=2e-3)


def test_rc_step_has_no_overshoot_and_settles():
    tau = 1e-3
    t, v = rc_step(tau=tau, final=5.0, span_taus=10)
    assert m.overshoot_pct(t, v) == pytest.approx(0.0, abs=1e-6)
    # 2% settling of a first-order response is at t = -tau*ln(0.02) = 3.912*tau
    st = m.settling_time(t, v, tol=0.02)
    assert st == pytest.approx(3.912 * tau, rel=5e-3)


def test_fall_time_symmetric_to_rise():
    tau = 1e-3
    t = np.linspace(0.0, 10 * tau, 4000)
    v = 5.0 * np.exp(-t / tau)  # decays 5 -> 0
    ft = m.fall_time(t.tolist(), v.tolist())
    assert ft == pytest.approx(2.19722 * tau, rel=3e-3)


def test_overshoot_of_damped_step():
    # Underdamped second-order step: known peak overshoot for zeta=0.5 is
    # exp(-pi*zeta/sqrt(1-zeta^2)) = 16.30%
    zeta = 0.5
    wn = 2.0 * math.pi * 1000.0
    wd = wn * math.sqrt(1.0 - zeta * zeta)
    t = np.linspace(0.0, 5e-3, 8000)
    v = 1.0 - np.exp(-zeta * wn * t) * (np.cos(wd * t) + (zeta / math.sqrt(1 - zeta * zeta)) * np.sin(wd * t))
    expected = math.exp(-math.pi * zeta / math.sqrt(1 - zeta * zeta)) * 100.0
    assert m.overshoot_pct(t.tolist(), v.tolist()) == pytest.approx(expected, rel=1e-2)


def test_frequency_of_sine():
    t, v = sine(freq=2500.0, amp=1.0, cycles=10)
    period, freq = m.period_frequency(t, v)
    assert freq == pytest.approx(2500.0, rel=1e-3)
    assert period == pytest.approx(1.0 / 2500.0, rel=1e-3)


def test_duty_cycle_of_square_waves():
    # 50% duty square wave
    def square(duty, freq=1000.0, cycles=6, pts=6000):
        t = np.linspace(0.0, cycles / freq, pts)
        phase = (t * freq) % 1.0
        v = np.where(phase < duty, 1.0, -1.0)
        return t.tolist(), v.tolist()

    t, v = square(0.5)
    assert m.duty_cycle(t, v) == pytest.approx(0.5, abs=0.02)

    t, v = square(0.25)
    assert m.duty_cycle(t, v) == pytest.approx(0.25, abs=0.02)


def test_measure_all_omits_undefined():
    # A pure monotonic ramp has no periodicity, so no frequency keys appear
    t = np.linspace(0.0, 1.0, 100)
    v = 2.0 * t
    result = m.measure_all(t.tolist(), v.tolist())
    assert "frequency" not in result
    assert "duty_cycle" not in result
    assert "rms" in result and "peak_to_peak" in result


def test_input_validation():
    with pytest.raises(ValueError, match="equal length"):
        m.basic_stats([0.0, 1.0], [0.0])
    with pytest.raises(ValueError, match="two samples"):
        m.basic_stats([0.0], [1.0])


def test_api_measure_rc_rise_time():
    # RC low-pass: R=1k, C=1uF -> tau=1ms, analytic rise time ~2.197ms
    path = os.path.join(SCHEMATICS_DIR, MEAS_FILE)
    graph = {
        "schema_version": "1.0.0",
        "metadata": {"name": "Measure RC", "created_utc": "2026-05-23T20:38:00Z", "author_agent": "T"},
        "nodes": [
            {"id": "V1", "type": "voltage_source", "params": {"V": 5.0},
             "pos": {"x": 100, "y": 100}, "rot": 0, "pins": {"a": "n1", "b": "n0"}},
            {"id": "R1", "type": "resistor", "params": {"R": 1000.0},
             "pos": {"x": 200, "y": 100}, "rot": 0, "pins": {"a": "n1", "b": "n2"}},
            {"id": "C1", "type": "capacitor", "params": {"C": 1e-6},
             "pos": {"x": 300, "y": 100}, "rot": 0, "pins": {"a": "n2", "b": "n0"}}
        ],
        "edges": [],
        "nets": ["n0", "n1", "n2"]
    }
    assert client.post("/api/agent/action", json={
        "action": "save_schematic", "params": {"path": path, "graph": graph}
    }).json()["status"] == "ok"
    assert client.post("/api/agent/action", json={
        "action": "load_schematic", "params": {"path": path}
    }).json()["status"] == "ok"

    r = client.post("/api/agent/action", json={
        "action": "measure",
        "params": {"path": path, "nets": ["n2"],
                   "params": {"t_stop": 0.01, "dt": 1e-5, "method": "trapezoidal", "uic": True}}
    })
    body = r.json()
    assert body["status"] == "ok"
    meas = body["data"]["measurements"]["n2"]
    assert meas["rise_time"] == pytest.approx(2.197e-3, rel=2e-2)
    assert meas["max"] == pytest.approx(5.0, abs=0.02)
    assert meas["overshoot_pct"] == pytest.approx(0.0, abs=1e-3)

    # Default (no nets) measures every non-ground net
    r = client.post("/api/agent/action", json={
        "action": "measure",
        "params": {"path": path, "params": {"t_stop": 0.01, "dt": 1e-4, "uic": True}}
    })
    body = r.json()
    assert body["status"] == "ok"
    assert "n1" in body["data"]["measurements"]
    assert "n2" in body["data"]["measurements"]

    # Unknown net is a clean error
    r = client.post("/api/agent/action", json={
        "action": "measure", "params": {"path": path, "nets": ["nonexistent"], "params": {"t_stop": 0.01, "dt": 1e-4}}
    })
    assert r.status_code == 400
