"""
Maps a sentier-inventory location code to a coarse region bucket, so
forecast_superstructure.py can anchor its objective on "similar
neighbors" instead of the whole-world mean (see that file's docstring for
why: a country's electricity mix looks more like its neighbors' than like
the global average).

sentier-inventory locations are a mix of real ISO 3166-1 alpha-2 country
codes (CH, DE, FR, ...) and ecoinvent-style regional aggregate codes (RER,
RAS, RAF, RLA, RNA, RME, GLO, ENTSO-E). We map both kinds onto the same
small set of region buckets:

    EU  Europe            AS  Asia (incl. Middle East -- see note below)
    AF  Africa            NA  North America
    SA  Latin America     OC  Oceania

This is an approximation, not an authoritative geography reference:
pycountry_convert's continent classification doesn't separate the Middle
East from the rest of Asia the way ecoinvent's RME code implies, and a few
countries sit awkwardly on a continent boundary (Russia, Turkey, Mexico).
For this tool's purpose -- giving the optimizer a *better* neighbor group
than "everyone on Earth", not a precise one -- this is good enough; treat
region_of() as a heuristic, not ground truth.
"""
try:
    import pycountry_convert as pc
except ImportError:  # pragma: no cover
    pc = None

# ecoinvent-style regional aggregate codes seen as real family members in
# sentier-inventory -- map them directly rather than through pycountry.
_AGGREGATE_REGIONS = {
    "RER": "EU",
    "ENTSO-E": "EU",
    "RAS": "AS",
    "RME": "AS",   # Middle East folded into Asia -- see module docstring
    "RAF": "AF",
    "RLA": "SA",
    "RNA": "NA",
    "GLO": None,   # global average -- not a "neighbor" of anything
}

_CONTINENT_TO_REGION = {
    "EU": "EU",
    "AS": "AS",
    "AF": "AF",
    "NA": "NA",
    "SA": "SA",
    "OC": "OC",
}

_cache = {}


def region_of(location_code: str):
    """Return a coarse region bucket for a sentier-inventory location code,
    or None if it can't be resolved (e.g. GLO, or an unrecognized code)."""
    if location_code in _cache:
        return _cache[location_code]

    region = None
    if location_code in _AGGREGATE_REGIONS:
        region = _AGGREGATE_REGIONS[location_code]
    elif pc is not None and isinstance(location_code, str) and len(location_code) == 2:
        try:
            continent = pc.country_alpha2_to_continent_code(location_code)
            region = _CONTINENT_TO_REGION.get(continent)
        except Exception:
            region = None

    _cache[location_code] = region
    return region
