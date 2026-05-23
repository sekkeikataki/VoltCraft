import json
import numpy as np
from voltcraft.engine.solver import ContinuousSolver, DiscreteEventScheduler, MixedSignalCoSimulator

def main():
    json_path = "voltcraft/storage/schematics/calculator.vcg.json"
    print(f"[VOLTCRAFT-SIMULATOR] Loading mechatronic calculator schematic: {json_path}")
    with open(json_path, "r", encoding="utf-8") as f:
        graph = json.load(f)
        
    print("[VOLTCRAFT-SIMULATOR] Compiling hybrid mechatronic nodes...")
    analog = ContinuousSolver(graph["nodes"], graph["edges"])
    digital = DiscreteEventScheduler()
    
    # Initialize digital inputs in queue
    digital.schedule_event(0.0, "d_A", "0")
    digital.schedule_event(0.0, "d_B", "0")
    digital.schedule_event(0.0, "d_Sub", "0")
    
    cosim = MixedSignalCoSimulator(analog, digital)
    
    # Run mixed continuous-discrete integrated solving from 0 to 5ms with base 10us step
    t_stop = 0.005
    dt = 1e-5
    print(f"[VOLTCRAFT-SIMULATOR] Running Mixed Co-Simulation [t=0..{t_stop}s, base dt={dt}s]...")
    res = cosim.step_co_simulation(0.0, t_stop, dt)
    
    print("[VOLTCRAFT-SIMULATOR] Simulation completed.")
    print("[VOLTCRAFT-SIMULATOR] ANALOG SIGNAL PROBE TELEMETRY:")
    
    a_times = res["analog_times"]
    a_waves = res["analog_waveforms"]
    a_map = res["analog_map"]
    
    # Let's extract values for v_Sum and v_Cout at specific points
    print(f"Number of analog steps: {len(a_times)}")
    
    # Print logic trace changes inside the digital scheduler
    d_waves = res["digital_waveforms"]
    print("\n[VOLTCRAFT-SIMULATOR] DIGITAL LOGIC ANALYZER TRANSITIONS:")
    for net in sorted(d_waves.keys()):
        log_list = d_waves[net]
        print(f"  Net '{net}': {[f'{t*1e3:.2f}ms->{val}' for t, val in log_list[:10]]}")

    # Compile the final addition/subtraction calculator truth table!
    print("\n[VOLTCRAFT-SIMULATOR] CALCULATOR ALU LOGIC TRUTH TABLE:")
    print("-----------------------------------------------------------------")
    print(" TIME (ms) | IN_A (V) | IN_B (V) | SUB (V) || OUT_SUM (V) | COUT (V)")
    print("-----------------------------------------------------------------")
    
    # Read samples at 0.5ms intervals
    for sample_t in [0.0, 0.0005, 0.001, 0.0015, 0.002, 0.0025, 0.003, 0.0035, 0.004, 0.0045]:
        idx = min(range(len(a_times)), key=lambda i: abs(a_times[i] - sample_t))
        
        # Analog voltages
        v_a = a_waves[a_map["in_A"]][idx]
        v_b = a_waves[a_map["in_B"]][idx]
        v_sub = a_waves[a_map["ctrl_Sub"]][idx]
        v_sum = a_waves[a_map["v_Sum"]][idx]
        v_cout = a_waves[a_map["v_Cout"]][idx]
        
        print(f"  {sample_t*1e3:6.2f} ms | {v_a:7.3f}V | {v_b:7.3f}V | {v_sub:6.3f}V || {v_sum:10.3f}V | {v_cout:7.3f}V")
        
    print("-----------------------------------------------------------------")
    print("[VOLTCRAFT-SIMULATOR] SUCCESS.")

if __name__ == "__main__":
    main()
