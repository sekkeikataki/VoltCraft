"""Analytic tests for the level-1 MOSFET and Ebers-Moll BJT models."""
import math

import numpy as np
import pytest

from voltcraft.engine.solver import ContinuousSolver


def nmos_common_source(vg: float):
    # Vdd=10V, Rd=5k drain resistor, gate driven directly, source grounded.
    # K=2mA/V^2, Vth=1V, lambda=0.
    return [
        {"id": "VDD", "type": "voltage_source", "params": {"V": 10.0}, "pins": {"a": "n_vdd", "b": "n0"}},
        {"id": "VG", "type": "voltage_source", "params": {"V": vg}, "pins": {"a": "n_g", "b": "n0"}},
        {"id": "RD", "type": "resistor", "params": {"R": 5000.0}, "pins": {"a": "n_vdd", "b": "n_d"}},
        {"id": "M1", "type": "nmos", "params": {"K": 2e-3, "Vth": 1.0, "lambda": 0.0},
         "pins": {"gate": "n_g", "drain": "n_d", "source": "n0"}}
    ]


def test_nmos_saturation_operating_point():
    # vgs=2, Vth=1 -> Id = K/2 * (1)^2 = 1 mA -> Vd = 10 - 5k*1mA = 5V.
    # Consistency: vds=5 > vov=1 confirms saturation.
    solver = ContinuousSolver(nmos_common_source(2.0), [])
    x, cmap = solver.solve_dc()

    assert x[cmap["n_d"]] == pytest.approx(5.0, abs=1e-3)
    assert solver.last_solve_stats["converged"] is True

    report = solver.dc_operating_report(x)
    assert report["M1"]["i"] == pytest.approx(1e-3, abs=1e-6)
    assert report["M1"]["vgs"] == pytest.approx(2.0, abs=1e-3)
    assert report["M1"]["gm"] == pytest.approx(2e-3, abs=1e-5)


def test_nmos_triode_operating_point():
    # vgs=5 -> vov=4. Triode: Id = K((vov)vds - vds^2/2), KVL 10 = 5000*Id + vds
    # => 5*vds^2 - 41*vds + 10 = 0 -> vds = 0.25184 V
    solver = ContinuousSolver(nmos_common_source(5.0), [])
    x, cmap = solver.solve_dc()

    vds_expected = (41.0 - math.sqrt(41.0 ** 2 - 200.0)) / 10.0
    assert x[cmap["n_d"]] == pytest.approx(vds_expected, abs=1e-3)


def test_nmos_cutoff():
    # vgs=0.5 < Vth -> no drain current -> Vd pulled to Vdd
    solver = ContinuousSolver(nmos_common_source(0.5), [])
    x, cmap = solver.solve_dc()
    assert x[cmap["n_d"]] == pytest.approx(10.0, abs=1e-2)


def test_pmos_saturation_operating_point():
    # Source at Vdd=10, gate at 8 -> vsg=2, |vov|=1 -> Id = 1 mA through
    # 5k to ground -> Vd = 5V. vsd=5 > 1 confirms saturation.
    nodes = [
        {"id": "VDD", "type": "voltage_source", "params": {"V": 10.0}, "pins": {"a": "n_vdd", "b": "n0"}},
        {"id": "VG", "type": "voltage_source", "params": {"V": 8.0}, "pins": {"a": "n_g", "b": "n0"}},
        {"id": "M1", "type": "pmos", "params": {"K": 2e-3, "Vth": 1.0, "lambda": 0.0},
         "pins": {"gate": "n_g", "drain": "n_d", "source": "n_vdd"}},
        {"id": "RD", "type": "resistor", "params": {"R": 5000.0}, "pins": {"a": "n_d", "b": "n0"}}
    ]
    solver = ContinuousSolver(nodes, [])
    x, cmap = solver.solve_dc()
    assert x[cmap["n_d"]] == pytest.approx(5.0, abs=1e-3)


def test_nmos_common_source_ac_gain():
    # Small-signal gain of the saturated common-source stage:
    # |Av| = gm * Rd = 2mA/V * 5k = 10 -> 20 dB, phase 180 (inverting).
    nodes = nmos_common_source(2.0)
    nodes[1]["params"]["ac_mag"] = 1.0  # drive the gate source
    solver = ContinuousSolver(nodes, [])
    freqs, mag_db, phase_deg, cmap = solver.solve_ac(1.0, 1e3, 10)

    idx_d = cmap["n_d"]
    assert mag_db[idx_d][0] == pytest.approx(20.0, abs=0.1)
    assert abs(phase_deg[idx_d][0]) == pytest.approx(180.0, abs=1.0)
    # Resistive stage: flat across the sweep
    assert mag_db[idx_d][-1] == pytest.approx(mag_db[idx_d][0], abs=0.05)


def test_npn_common_emitter_bias():
    # Vcc=10, Rc=1k, base fed from 5V through Rb=430k, beta=100.
    # Ib = (5 - vbe)/430k with vbe ~= 0.6V -> Ic ~= 1.02 mA -> Vc ~= 9V.
    nodes = [
        {"id": "VCC", "type": "voltage_source", "params": {"V": 10.0}, "pins": {"a": "n_vcc", "b": "n0"}},
        {"id": "VBB", "type": "voltage_source", "params": {"V": 5.0}, "pins": {"a": "n_bb", "b": "n0"}},
        {"id": "RC", "type": "resistor", "params": {"R": 1000.0}, "pins": {"a": "n_vcc", "b": "n_c"}},
        {"id": "RB", "type": "resistor", "params": {"R": 430000.0}, "pins": {"a": "n_bb", "b": "n_b"}},
        {"id": "Q1", "type": "bjt_npn", "params": {"Is": 1e-15, "beta_f": 100.0, "beta_r": 1.0},
         "pins": {"collector": "n_c", "base": "n_b", "emitter": "n0"}}
    ]
    solver = ContinuousSolver(nodes, [])
    x, cmap = solver.solve_dc()

    v_c = x[cmap["n_c"]]
    v_b = x[cmap["n_b"]]
    assert 0.55 < v_b < 0.75           # forward-biased junction
    assert 8.7 < v_c < 9.2             # Ic ~= 1 mA through 1k from 10V
    assert solver.last_solve_stats["converged"] is True

    report = solver.dc_operating_report(x)
    ic, ib = report["Q1"]["ic"], report["Q1"]["ib"]
    assert ic / ib == pytest.approx(100.0, rel=0.05)  # beta holds in active region

    # KCL through the collector resistor
    i_rc = (x[cmap["n_vcc"]] - v_c) / 1000.0
    assert ic == pytest.approx(i_rc, abs=1e-7)


def test_pnp_common_emitter_bias():
    # Mirror circuit: emitter at 10V, base pulled down through 430k,
    # collector through 1k to ground. |Ib| ~= 9.38V/430k -> Ic ~= 2.2 mA.
    nodes = [
        {"id": "VCC", "type": "voltage_source", "params": {"V": 10.0}, "pins": {"a": "n_vcc", "b": "n0"}},
        {"id": "RB", "type": "resistor", "params": {"R": 430000.0}, "pins": {"a": "n_b", "b": "n0"}},
        {"id": "RC", "type": "resistor", "params": {"R": 1000.0}, "pins": {"a": "n_c", "b": "n0"}},
        {"id": "Q1", "type": "bjt_pnp", "params": {"Is": 1e-15, "beta_f": 100.0, "beta_r": 1.0},
         "pins": {"collector": "n_c", "base": "n_b", "emitter": "n_vcc"}}
    ]
    solver = ContinuousSolver(nodes, [])
    x, cmap = solver.solve_dc()

    v_b = x[cmap["n_b"]]
    v_c = x[cmap["n_c"]]
    assert 9.25 < v_b < 9.45           # one vbe below the 10V emitter
    # Ic = beta * (v_b/430k); v_b ~9.35 -> Ib ~21.7uA -> Ic ~2.17mA -> Vc ~2.17V
    assert 1.9 < v_c < 2.5
    assert solver.last_solve_stats["converged"] is True


def test_bjt_current_gain_tracks_beta():
    # Sweep beta and verify the collector/base current ratio follows it
    for beta in (50.0, 200.0):
        nodes = [
            {"id": "VCC", "type": "voltage_source", "params": {"V": 10.0}, "pins": {"a": "n_vcc", "b": "n0"}},
            {"id": "VBB", "type": "voltage_source", "params": {"V": 5.0}, "pins": {"a": "n_bb", "b": "n0"}},
            {"id": "RC", "type": "resistor", "params": {"R": 100.0}, "pins": {"a": "n_vcc", "b": "n_c"}},
            {"id": "RB", "type": "resistor", "params": {"R": 430000.0}, "pins": {"a": "n_bb", "b": "n_b"}},
            {"id": "Q1", "type": "bjt_npn", "params": {"Is": 1e-15, "beta_f": beta, "beta_r": 1.0},
             "pins": {"collector": "n_c", "base": "n_b", "emitter": "n0"}}
        ]
        solver = ContinuousSolver(nodes, [])
        x, _ = solver.solve_dc()
        report = solver.dc_operating_report(x)
        assert report["Q1"]["ic"] / report["Q1"]["ib"] == pytest.approx(beta, rel=0.05)


def test_mosfet_transient_inverter_switches():
    # NMOS inverter driven by a 1kHz square wave: output must swing
    # between Vdd (input low, cutoff) and near-ground (input high, triode)
    nodes = [
        {"id": "VDD", "type": "voltage_source", "params": {"V": 5.0}, "pins": {"a": "n_vdd", "b": "n0"}},
        {"id": "VIN", "type": "voltage_source", "params": {"V": 2.5, "freq": 1000.0, "wave": "square", "offset": 2.5},
         "pins": {"a": "n_g", "b": "n0"}},
        {"id": "RD", "type": "resistor", "params": {"R": 10000.0}, "pins": {"a": "n_vdd", "b": "n_d"}},
        {"id": "M1", "type": "nmos", "params": {"K": 2e-3, "Vth": 1.0},
         "pins": {"gate": "n_g", "drain": "n_d", "source": "n0"}}
    ]
    solver = ContinuousSolver(nodes, [])
    results, times, cmap = solver.solve_transient(0.0, 2e-3, 1e-5, uic=False)

    out = results[cmap["n_d"]]
    assert np.max(out) > 4.9    # cutoff: pulled to Vdd
    assert np.min(out) < 0.5    # on: pulled near ground
