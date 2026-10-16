# Third-party components

PDF Redactor is licensed under **AGPL-3.0-or-later** (see `LICENSE`). It's AGPL because it is built on
PyMuPDF/MuPDF, which is licensed under AGPL-3.0 (or a commercial license from Artifex).

The source code and the packaged Mac app include or use the following components. Their license
texts ship inside each package (in the app: `PDF Redactor.app/Contents/Resources` and
`Contents/Frameworks`, in `*.dist-info` folders).

| Component | Used for | License |
|---|---|---|
| [PyMuPDF](https://github.com/pymupdf/PyMuPDF) / [MuPDF](https://mupdf.com) (Artifex Software) | Reading, redacting and saving PDFs; built-in OCR engine | AGPL-3.0 (or Artifex commercial) |
| MuPDF's bundled libraries (FreeType, HarfBuzz, jbig2dec, lcms2, libjpeg, OpenJPEG, zlib, Gumbo, Tesseract, Leptonica, extract) | Inside MuPDF | Various (FTL, MIT, BSD, IJG, zlib, Apache-2.0, AGPL-3.0) as distributed by Artifex |
| [Tesseract `eng.traineddata`](https://github.com/tesseract-ocr/tessdata_fast) (`tessdata/`) | English OCR model | Apache-2.0 |
| [spaCy](https://spacy.io) and [thinc](https://github.com/explosion/thinc) (Explosion AI) | Local AI name detection | MIT |
| spaCy English models `en_core_web_sm/md/lg` 3.8.0 | Local AI model | MIT |
| srsly, catalogue, confection, cymem, preshed, murmurhash, wasabi, weasel, spacy-legacy, spacy-loggers | spaCy dependencies | MIT |
| blis | spaCy dependency | BSD-3-Clause |
| pydantic, pydantic-core, typer, rich, smart-open, cloudpathlib | spaCy dependencies | MIT |
| [NumPy](https://numpy.org) | Image arrays | BSD-3-Clause (and bundled BSD/MIT/zlib/CC0 parts) |
| [zxing-cpp](https://github.com/zxing-cpp/zxing-cpp) | QR code / barcode detection | Apache-2.0 |
| [PyInstaller](https://pyinstaller.org) | Packaging the Mac app | GPL-2.0-or-later **with bootloader exception**: apps built with it may use any license |
| Python (CPython) | Runtime inside the app | PSF License 2.0 |
| Tcl/Tk | App window | Tcl/Tk license (BSD-style) |
| US Census Bureau 1990 surname and first-name frequency lists (`data/`) | Name detection | Public domain (US Government work) |

Other small pure-Python dependencies installed by the above (requests, urllib3, idna, certifi,
Jinja2, MarkupSafe, click, packaging, typing-extensions, tqdm, etc.) are under MIT, BSD,
Apache-2.0, MPL-2.0 or PSF licenses.

"Mac", "macOS" and "Apple Silicon" are trademarks of Apple Inc. Bank names that appear in the
detection rules are used only to recognise them in documents and imply no affiliation.
