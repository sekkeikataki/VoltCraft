---
family: flipflop
iec_code: IEC-60617-12
equation: q <= d on rising clk edge (after delay)
characteristics:
  - Edge-triggered D flip-flop (type digital_dff) with q and q_bar
  - Params: delay [s], init ("0" or "1")
  - Pair with digital_clock (freq, duty) for counters and registers
---
# D Flip-Flops
The storage element of sequential logic. On each rising clock edge the d
input is latched into q (q_bar is its complement). Wiring d to q_bar
divides the clock by two; chained stages build ripple counters and shift
registers.
