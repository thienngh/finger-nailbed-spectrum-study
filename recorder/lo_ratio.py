"""LO-style demo ratio; not calibrated perfusion or oxygen saturation.

White division is our explicit approximation: the vendor's to_reflectance
helper was not supplied. Remaining normalization follows the pasted code.
"""
import numpy as np


def ratio_map(spectra, wavelengths, white):
    """Return per-point ratios and frame-wide normalization limits.

    Preserve the vendor's two-sample slices [nearest-1:nearest+1].
    Invalid inputs, zero log arguments, and near-zero denominators yield NaN,
    rather than invented values produced by clipping.
    """
    s = np.asarray(spectra, dtype=float)
    w = np.asarray(wavelengths, dtype=float)
    white = np.asarray(white, dtype=float)
    if s.ndim != 2 or s.shape[1] != len(w) or white.shape != w.shape:
        raise ValueError('White reference, spectra, and wavelength shapes disagree')
    if not np.isfinite(white).all() or np.any(white <= 0):
        raise ValueError('White reference must be finite and positive in every band')
    if not np.isfinite(w).all() or np.any(np.diff(w) <= 0) or w[0] > 548 or w[-1] < 599:
        raise ValueError('Ordered wavelengths must cover both 548 and 599 nm')
    hb, ref = [int(np.argmin(np.abs(w-target))) for target in (548, 599)]
    if min(hb, ref) < 1 or hb == ref:
        raise ValueError('Cannot construct distinct two-sample bands')
    valid = np.isfinite(s).all(axis=1) & (s >= 0).all(axis=1)
    out = np.full(len(s), np.nan)
    r = s[valid]/white[None, :]
    norms = np.linalg.norm(r, axis=1)
    nonzero = norms > 0
    ids = np.flatnonzero(valid)[nonzero]
    if not len(ids):
        return out, (None, None)
    unit = r[nonzero]/norms[nonzero, None]
    low, high = float(unit.min()), float(unit.max())
    if high-low <= np.finfo(float).eps*max(1., abs(high)):
        return out, (low, high)
    scaled = (unit-low)/(high-low)
    h, b = scaled[:, hb-1:hb+1], scaled[:, ref-1:ref+1]
    good = (h > 0).all(axis=1) & (b > 0).all(axis=1)
    with np.errstate(divide='ignore', invalid='ignore'):
        numerator = -np.log10(h).mean(axis=1)
        denominator = -np.log10(b).mean(axis=1)
        result = numerator/denominator
    good &= np.isfinite(result) & (denominator > 1e-12)
    out[ids[good]] = result[good]
    return out, (low, high)


def band_details(wavelengths):
    w = np.asarray(wavelengths)
    return {str(target): w[max(0, int(np.argmin(abs(w-target)))-1):
                           int(np.argmin(abs(w-target)))+1].tolist()
            for target in (548, 599)}
