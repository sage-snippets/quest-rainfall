# Test fixtures

## `ifs_tp_20261115_00z_steps_0_6_12.grib2`

Genuine ECMWF Open Data, unmodified bytes as returned by `ecmwf-opendata` 0.3.35
(byte-range retrieval of the `tp` messages only).

- Model: IFS (HRES), stream `oper`, type `fc`, resolution 0.25° (`regular_ll`, 1440×721)
- Run: 2026-11-15 00 UTC; steps 0, 0-6, 0-12 h (accumulated from forecast start)
- Source URLs: `https://data.ecmwf.int/forecasts/20261115/00z/ifs/0p25/oper/20261115000000-{0,6,12}h-oper-fc.grib2`
- Retrieved: 2026-11-15
- Licence: CC BY 4.0 — © ECMWF. Used here only as a regression fixture.

It is a *real historical forecast*, used to test decoding against known values. It is never
served by the application as a current forecast.

## `aifs-single_tp_20261115_06z_steps_0_6_12_24.grib2`

Genuine ECMWF Open Data, unmodified bytes as returned by `ecmwf-opendata` 0.3.35.

- Model: AIFS Single (data-driven), stream `oper`, type `fc`, class `ai`, 0.25° `regular_ll` (1440×721)
- Run: 2026-11-15 06 UTC; steps 0, 0-6, 0-12, 0-24 h (accumulated from forecast start)
- **tp encoding differs from IFS:** paramId 228228, units `kg m**-2` (= mm of water), not metres
- Source URLs: `https://data.ecmwf.int/forecasts/20261115/06z/aifs-single/0p25/oper/20261115060000-{0,6,12,24}h-oper-fc.grib2`
- Retrieved: 2026-11-15
- Licence: CC BY 4.0, © ECMWF. Used only as a regression fixture.
