"""Docker build-time check: the bundled ecCodes library decodes a real ECMWF GRIB2 tp message."""
import sys

import eccodes

path = sys.argv[1]
with open(path, "rb") as f:
    g = eccodes.codes_grib_new_from_file(f)
    assert eccodes.codes_get(g, "shortName") == "tp", "unexpected shortName"
    assert eccodes.codes_get(g, "units") == "m", "unexpected units"
    n = eccodes.codes_get_values(g).size
    eccodes.codes_release(g)
assert n == 1440 * 721, n
print("ecCodes", eccodes.codes_get_api_version(), "self-test OK:", n, "values")
