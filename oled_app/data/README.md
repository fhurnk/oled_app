# Spectral sensitivity data

`spectral_sensitivity.csv` is the machine-readable calibration table used by
the spectral recalculation workflow.

Columns:

- `wavelength_nm` — wavelength grid from the supplied sensitivity workbook;
- `cie_v_lambda` — CIE photopic luminosity function;
- `bpw34_relative_response` — relative spectral sensitivity of the BPW34
  photodiode.

The application linearly interpolates both curves to the measured
spectrometer wavelength grid and evaluates the corrected integral
`spectrum * CIE / BPW34`.
