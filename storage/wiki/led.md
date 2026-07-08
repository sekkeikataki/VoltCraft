---
family: led
iec_code: IEC-60617-5
equation: i = Is*(exp(v/(N*Vt)) - 1)
characteristics:
  - Forward drop typically 1.8V to 3.3V
  - Modeled as a Shockley junction with a higher turn-on
---
# Light-Emitting Diodes
A diode with a higher forward voltage; VoltCraft models it with the same
junction equation tuned for a ~2V drop.
