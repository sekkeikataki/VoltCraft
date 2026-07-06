---
family: mosfet
iec_code: IEC-60617-5
equation: Id = K/2 * (Vgs - Vth)^2 * (1 + lambda*Vds)
characteristics:
  - Level-1 square-law model (types nmos / pmos)
  - Params: K [A/V^2], Vth [V], lambda [1/V]
  - Regions: cutoff, triode, saturation; symmetric drain/source
---
# MOSFETs
Voltage-controlled transistors. The gate voltage relative to the source
sets the channel current; VoltCraft solves the square-law model with
Newton-Raphson companion linearization.
