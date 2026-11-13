# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 al3phnul and contributors
"""QR code and barcode reading with zxing-cpp. Decoded content is never logged."""

from __future__ import annotations

from dataclasses import dataclass

from PIL import Image

_NAMES = {
    "QRCode": "QR code",
    "MicroQRCode": "Micro QR code",
    "RMQRCode": "rMQR code",
    "DataMatrix": "Data Matrix code",
    "Aztec": "Aztec code",
    "PDF417": "PDF417 code",
    "MaxiCode": "MaxiCode",
    "EAN13": "EAN-13 barcode",
    "EAN8": "EAN-8 barcode",
    "UPCA": "UPC-A barcode",
    "UPCE": "UPC-E barcode",
    "Code128": "Code 128 barcode",
    "Code39": "Code 39 barcode",
    "Code93": "Code 93 barcode",
    "ITF": "ITF barcode",
    "Codabar": "Codabar barcode",
}


@dataclass(frozen=True)
class Code:
    text: str
    kind: str


def available() -> bool:
    try:
        import zxingcpp  # noqa: F401
    except ImportError:
        return False
    return True


def _kind(fmt: object) -> str:
    name = getattr(fmt, "name", None) or str(fmt).rsplit(".", 1)[-1]
    return _NAMES.get(name, "barcode")


def read_codes(image: Image.Image) -> list[Code]:
    import zxingcpp

    rgb = image.convert("RGB")
    results = zxingcpp.read_barcodes(rgb)
    if not results and max(rgb.size) < 800:
        # BOX, not NEAREST: doubling gives the same pixels, and NEAREST fails in one Pillow
        # build that Ubuntu 26.04 shipped (python3-pil 12.1.1-2ubuntu1.5).
        bigger = rgb.resize((rgb.width * 2, rgb.height * 2), Image.Resampling.BOX)
        results = zxingcpp.read_barcodes(bigger)
    seen: set[str] = set()
    codes: list[Code] = []
    for result in results:
        # (a code with no text in it, e.g. raw binary data, has nothing to copy)
        if result.valid and result.text and result.text not in seen:
            seen.add(result.text)
            codes.append(Code(text=result.text, kind=_kind(result.format)))
    return codes
