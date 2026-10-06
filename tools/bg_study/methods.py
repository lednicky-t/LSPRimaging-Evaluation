import numpy as np
import cv2
from lspr_imaging_app.image_tools.background.estimate import estimate_background_profile
from lspr_imaging_app.roi.model import AreaRoiDetectionSettings

_S = AreaRoiDetectionSettings(ignore_marked_pixels=True)


def m_none(img, excl):
    return np.full(img.shape, np.median(img[~excl]), np.float32)


def make_app(sigma, binning):
    def f(img, excl):
        return estimate_background_profile(
            img, sigma_px=sigma, binning=binning, mask_settings=_S, external_mask=excl
        )

    return f


def make_nconv(f, sigma):
    """same maths as the app (masked Gaussian / weights) but cv2: area-downsample, cv2 blur, linear upsample"""

    def fn(img, excl):
        h, w = img.shape
        sh, sw = -(-h // f), -(-w // f)
        wt = (~excl).astype(np.float32)
        num = cv2.resize(img * wt, (sw, sh), interpolation=cv2.INTER_AREA)
        den = cv2.resize(wt, (sw, sh), interpolation=cv2.INTER_AREA)
        s = max(sigma / f, 1.0)
        num = cv2.GaussianBlur(num, (0, 0), s, borderType=cv2.BORDER_REPLICATE)
        den = cv2.GaussianBlur(den, (0, 0), s, borderType=cv2.BORDER_REPLICATE)
        small = np.where(den > 1e-6, num / np.maximum(den, 1e-6), np.median(img[~excl]))
        return cv2.resize(
            small.astype(np.float32), (w, h), interpolation=cv2.INTER_LINEAR
        )

    return fn


def make_poly(deg, dec=8):
    def fn(img, excl):
        h, w = img.shape
        ys, xs = np.mgrid[0:h:dec, 0:w:dec]
        v = ~excl[::dec, ::dec]
        X = xs[v] / w * 2 - 1
        Y = ys[v] / h * 2 - 1
        z = img[::dec, ::dec][v]

        def design(X, Y):
            return np.stack(
                [X**i * Y**j for i in range(deg + 1) for j in range(deg + 1 - i)], -1
            )

        A = design(X, Y)
        c, *_ = np.linalg.lstsq(A, z, rcond=None)
        r = z - A @ c
        keep = np.abs(r) < 3 * 1.4826 * np.median(np.abs(r))
        c, *_ = np.linalg.lstsq(A[keep], z[keep], rcond=None)
        gy, gx = np.mgrid[0:h, 0:w]
        return (design(gx / w * 2 - 1, gy / h * 2 - 1) @ c).astype(np.float32)

    return fn


def make_block(t, sm):
    def fn(img, excl):
        h, w = img.shape
        sh, sw = -(-h // t), -(-w // t)
        P = np.full((sh * t, sw * t), np.nan, np.float32)
        P[:h, :w] = np.where(excl, np.nan, img)
        blocks = P.reshape(sh, t, sw, t).transpose(0, 2, 1, 3).reshape(sh, sw, -1)
        cnt = np.isfinite(blocks).sum(-1)
        med = np.nanmedian(np.where(np.isfinite(blocks), blocks, np.nan), axis=-1)
        med[cnt < 0.3 * t * t] = np.nan
        ok = np.isfinite(med)
        m0 = np.where(ok, med, 0).astype(np.float32)
        wt = ok.astype(np.float32)
        num = cv2.GaussianBlur(m0, (0, 0), sm, borderType=cv2.BORDER_REPLICATE)
        den = cv2.GaussianBlur(wt, (0, 0), sm, borderType=cv2.BORDER_REPLICATE)
        small = np.where(den > 1e-6, num / np.maximum(den, 1e-6), np.nanmedian(med))
        return cv2.resize(
            small.astype(np.float32), (sw * t, sh * t), interpolation=cv2.INTER_CUBIC
        )[:h, :w]

    return fn


METHODS = {
    "none": m_none,
    "app s48 b1": make_app(48, 1),
    "app s48 b2 (current)": make_app(48, 2),
    "app s48 b4": make_app(48, 4),
    "app s48 b8": make_app(48, 8),
    "app s24 b2": make_app(24, 2),
    "app s96 b2": make_app(96, 2),
    "app s150 b4": make_app(150, 4),
    "cv s48 f4": make_nconv(4, 48),
    "cv s48 f8": make_nconv(8, 48),
    "cv s48 f16": make_nconv(16, 48),
    "cv s96 f16": make_nconv(16, 96),
    "poly2": make_poly(2),
    "poly3": make_poly(3),
    "poly4": make_poly(4),
    "poly6": make_poly(6),
    "block32 s1.5": make_block(32, 1.5),
    "block32 s3": make_block(32, 3),
}
