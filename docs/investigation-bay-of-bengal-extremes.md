# Investigation: extreme `tp` values in the SW Bay of Bengal (IFS 2026-11-15 00z)

**Question.** The first decode showed 330.7 mm in 0–6 h at 9.00°N 83.75°E. Is that a decoding
or processing error, or genuine model output?

**Method.** `backend/scripts/investigate_extremes.py data/store/ifs/2026111500`, run on 3-hourly
intervals for 0–48 h. It checks the neighbouring cells, the evolution over later steps, and
independent fields from the same run (`msl`, `10u`, `10v`).

## Evidence

| Check | Finding |
|---|---|
| Metadata | All 17 messages pass `validate_meta` (`tp`, `m`, `accum`, `startStep=0`, `regular_ll` 0.25°). ecCodes and cfgrib agree exactly (M0 test). |
| Accumulation consistency | No interval decreases by more than the packing quantum. The largest clamped negative is −0.2441 mm, equal to the 12-bit quantum of the 24 h field. |
| Neighbourhood (0–6 h, 7×7 cells) | This is a **coherent rain area, not an isolated spike**. In the SW Bay box, 172 cells exceed 20 mm and 57 exceed 50 mm. The peak sits in a 3–4 cell core of 190–330 mm, surrounded by 50–100 mm. |
| Temporal evolution | The box maximum stays at about 9–10.75°N, 83.75°E for 0–15 h (195–308 mm/3 h). It weakens to 40–100 mm/3 h from 15 to 45 h while drifting NNE to about 13°N 85°E, then intensifies again (268.6 mm/3 h at 45–48 h, 13.00°N 85.25°E). |
| Dynamical context (same run) | An MSLP minimum **deepens and tracks with the rain maximum**: 1004.7 hPa at T+6 (9.25°N 84.50°E), 1000.2 hPa at T+24 (10.50°N 85.50°E), 995.7 hPa at T+48 (13.00°N 85.50°E). The box median is about 1009–1011 hPa. The maximum 10 m wind is 18.0 m/s at T+6, 16.9 m/s at T+24 and 23.5 m/s at T+48. |

## Conclusion

These values are **genuine IFS forecast output**, not pipeline artefacts. The rain is co-located
with a deepening, north-moving low-pressure system that the model forecasts in the
south-west Bay of Bengal. A low over the Bay in mid-November fits the post-monsoon cyclone season.

**We keep the values unchanged.** We do not cap, smooth or discard them.

## Caveats (also shown to users)

- These are **model forecasts on 0.25° cells (about 28 km)**, not rain-gauge observations. Peaks
  of 200–300 mm/3 h at single grid points come from parametrised tropical convection and are
  highly uncertain in position and magnitude. Ensemble guidance should be used for decisions.
- This investigation does not check whether a real system exists. That would need
  observations or official IMD/RSMC bulletins, which are out of scope.

---

# Addendum (M3): the 874 mm/24 h global and 701 mm/24 h India maxima

**701 mm (India box, 10.75°N 83.75°E).** This is the Bay of Bengal system above, accumulated over
0–24 h. The India preset is a rectangle (68–97.5°E, 6–37.5°N), so it includes this part of the Bay.

**874 mm (global, 2.25°S 90.5°E, equatorial Indian Ocean).** We ran
`python scripts/investigate_extremes.py data/store/ifs/2026111500 85,-8,96,4`:

| Check | Finding |
|---|---|
| Neighbourhood (24 h total) | Coherent rain: 21 cells exceed 200 mm within ±3°. The peak cell (874 mm) is surrounded by a ring of 379–561 mm. The peak is about 1.6× its strongest neighbours, which is sharp but not a lone pixel. |
| Temporal evolution | The cluster is quasi-stationary at about 2–3°S, 90–91°E. **The same single cell** gets 168–244 mm every 3 h from 15 to 24 h, then the system decays and drifts south-west. |
| Dynamical context | Only a weak low: 1008.5 to 1002.3 hPa, with a box median of about 1010 hPa and maximum winds of 14–16 m/s. At 2°S the Coriolis effect is too weak to support a tropical cyclone. |
| Processing | Interval QC passed (no negatives beyond packing precision). The cumulative value equals the sum of the intervals at the cell. |

**Interpretation.** The value is a correctly decoded IFS output. Physically it looks like a
**"grid-point storm"**: organised equatorial convection whose intense rain locks onto a single
0.25° cell for several hours. This is a known behaviour of NWP models at this resolution, and
its single-cell magnitude should not be read as a credible point rainfall total. We keep it
unaltered, since editing model output would be fabrication. The UI must show such maxima as
model grid-cell values. Satellite estimates (e.g. GPM IMERG) would be needed to verify it,
which is out of scope here.
