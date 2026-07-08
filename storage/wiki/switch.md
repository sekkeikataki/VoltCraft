---
family: switch
iec_code: IEC-60617-7
equation: G = 1/Ron if v(cp)-v(cn) >= threshold else 1/Roff
characteristics:
  - Voltage-controlled SPST
  - Params: threshold [V], Ron, Roff, inverted
---
# Voltage-Controlled Switches
An SPST switch whose on/off state is set by a control voltage relative to
a threshold, modeling relays, analog gates, and transmission gates.
