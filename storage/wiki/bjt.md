---
family: bjt
iec_code: IEC-60617-5
equation: Ic = Is*(exp(Vbe/Vt) - exp(Vbc/Vt)) (Ebers-Moll)
characteristics:
  - Ebers-Moll model (types bjt_npn / bjt_pnp)
  - Params: Is [A], beta_f, beta_r, Vt [V]
  - Active-region current gain Ic/Ib = beta_f
---
# Bipolar Junction Transistors
Current-controlled transistors modeled with the full Ebers-Moll
two-junction equations, valid in cutoff, active, and saturation regions.
