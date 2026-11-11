# Third-party software

GrabbyHands itself is licensed under GPL-3.0-or-later (see [LICENSE](../LICENSE)). Its icons,
and the pictures in this folder, are licensed
[CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/).

## Bundled in the Flatpak

The Flatpak contains the software below, built from the upstream sources pinned in
[`build-aux/flatpak/io.github.al3phnul.GrabbyHands.yml`](../build-aux/flatpak/io.github.al3phnul.GrabbyHands.yml).
The full license texts are installed inside the Flatpak under `/app/share/licenses/`.

| Component | Version | Used for | License | Source |
|---|---|---|---|---|
| [Pillow](https://python-pillow.github.io/) | 13.0.0 | Loading, saving and filtering images | MIT-CMU | <https://pypi.org/project/pillow/> |
| [zxing-cpp](https://github.com/zxing-cpp/zxing-cpp) | 2.3.0 | Reading QR codes and barcodes | Apache-2.0 | <https://github.com/zxing-cpp/zxing-cpp> |
| [pybind11](https://github.com/pybind/pybind11) | 3.1.0 | Compiled into the zxing-cpp Python module | BSD-3-Clause | <https://github.com/pybind/pybind11> |
| [Tesseract](https://github.com/tesseract-ocr/tesseract) | 5.5.4 | Text recognition (OCR) | Apache-2.0 | <https://github.com/tesseract-ocr/tesseract> |
| [tessdata_fast](https://github.com/tesseract-ocr/tessdata_fast) (English) | 4.1.0 | Language data for Tesseract | Apache-2.0 | <https://github.com/tesseract-ocr/tessdata_fast> |
| [Leptonica](http://www.leptonica.org/) | 1.87.0 | Image library Tesseract is built on | BSD-2-Clause | <https://github.com/DanBloomberg/leptonica> |

Everything else the Flatpak uses (Python, GTK, libadwaita, PyGObject, pycairo and the image
codecs) comes from the [GNOME runtime](https://gitlab.gnome.org/GNOME/gnome-build-meta),
which Flatpak installs separately.

## The .deb package and source installs

These bundle nothing. They use the packages your distribution provides: PyGObject, pycairo,
GTK 4, libadwaita, Pillow and, optionally, Tesseract and the zxing-cpp Python module.
