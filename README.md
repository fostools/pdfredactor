# PDF Redactor

Remove sensitive information from PDFs **on your own Mac** before you share them with an AI assistant, an accountant or anyone else.

- **Completely offline.** The app blocks its own network access, so your documents never leave your computer.
- **Finds things for you.** SSNs, names, addresses, phone numbers, bank details, e-file PINs, QR codes and barcodes, using pattern rules plus a small AI model that runs on your Mac.
- **Real redaction.** The text is deleted from the file, not just covered with a black box. Hidden metadata is stripped too.
- **Keeps the useful parts.** Dollar amounts, dates and form labels stay, so the document still makes sense.

> ⚠️ No tool catches everything. **Always look over the redacted PDF before you share it.** This software comes with no warranty (see [License](#license)).

---

## Download (Mac)

1. Go to the [**Releases**](../../releases/latest) page.
2. Download the right file for your Mac:
   - **Apple Silicon** (M1/M2/M3/M4 and later): `PDF-Redactor-AppleSilicon.dmg`
   - **Intel** (most Macs from before 2021): `PDF-Redactor-Intel.dmg`

   Not sure which you have? Apple menu → **About This Mac**. It says "Chip: Apple M…" for Apple Silicon, or "Processor: Intel".
3. Open the `.dmg` and drag **PDF Redactor** into **Applications**.
4. **First launch:** macOS will say it can't verify the developer, because the app isn't signed with a paid Apple developer account. Open **System Settings → Privacy & Security**, scroll down and click **Open Anyway**. You only need to do this once.
   If it says the app "is damaged" instead, run this once in Terminal:
   ```
   xattr -dr com.apple.quarantine "/Applications/PDF Redactor.app"
   ```

Nothing else to install: the AI model, the OCR data and the barcode reader are all inside the app.

## How to use

1. **Open PDF…**: it scans automatically.
2. Review the list. Double-click a row (or press Space) to untick anything you want to keep.
3. Missed something? Type it into **Always redact** (comma-separated) and click **▶ Scan**. Put your family's names, your street and your bank's name in there. The list is remembered, on your Mac only, in `~/Library/Application Support/PDF Redactor/`.
4. **Save redacted PDF…**: the app then re-reads the saved file and confirms nothing you redacted can still be read.

Options: **Label boxes** writes things like `[SSN / ITIN]` in each box so the reader knows what was removed. **Flatten output to images** turns every page into a picture, the most cautious option.

## What it finds

| Type | Examples |
|---|---|
| **SSN / ITIN / EIN** | `123-45-6789`, `123 45 6789`, `123456789` (labeled by the nearby form label), `12-3456789`, masked `XXX-XX-1234` |
| **Names** | Values under form labels ("Employee's name", "Recipient's name"), "Dear…", "Sincerely", Mr./Ms./Dr., US Census name lists and the local AI. Works on ALL CAPS. Every other mention of a found name is redacted too. |
| **Addresses** | Street (ALL CAPS, N/S/E/W, Apt/Suite), PO Box, City + State + ZIP, ZIP+4, lines under "Address" labels |
| **Phones** | `(217)555-0142`, `217.555.0142`, `2175550142`, `+1 217 555 0142`, international numbers, anything after Phone/Tel/Cell/Fax |
| **Banking** | Routing & account numbers (inline, under column headings, digit-per-box on the 1040), masked (`****0381`, "ending in 0381"), check MICR lines, IBAN, SWIFT/BIC, card numbers, long unlabeled numbers, bank names |
| **PINs** | 5-digit e-file PINs after "Taxpayer's PIN" / "Spouse's PIN" (Form 8879), 6-digit Identity Protection PINs |
| **Other** | Email, date of birth, loan/policy/member IDs, company names (LLC/Inc…), IP addresses |
| **QR codes & barcodes** | QR, PDF417 (the W-2 barcode holds your SSN and name), DataMatrix, 1-D barcodes |
| **Scanned pages** | Read with built-in offline OCR, then redacted the same way |

**Also removed from the file:** document properties (author, title…), XMP metadata, bookmarks, comments and sticky notes, attachments, form-field data, links, JavaScript, thumbnails, accessibility tags, photo EXIF data (camera, GPS) and earlier edit history.

## Limitations

- An unusual name inside ordinary sentences can be missed. Use **Always redact** for names that matter.
- OCR can misread blurry, skewed or handwritten scans.
- Password-protected PDFs: save an unlocked copy first.
- English documents, with a focus on US tax and banking forms.

---

## For developers

### Run from source
Needs Python 3.10–3.14 with Tk (python.org installers include it).
```
pip install -r requirements.txt
pip install https://github.com/explosion/spacy-models/releases/download/en_core_web_lg-3.8.0/en_core_web_lg-3.8.0-py3-none-any.whl
python pdf_redactor.py              # open the app
python pdf_redactor.py --selftest   # built-in check
```
English OCR data is bundled in `tessdata/`. `en_core_web_sm` works too, with slightly weaker name detection.

### Build the Mac app yourself
```
./build_mac_app.sh                          # ~1 GB app with the large AI model
MODEL=en_core_web_sm ./build_mac_app.sh     # ~300 MB app
```
It uses your Python 3.10–3.14 if you have one. Otherwise it downloads a private Python 3.12 into this folder, since spaCy has no ready-made packages for newer Pythons yet. It installs only ready-made packages and runs the self-test before and after packaging. Output is `dist/PDF Redactor.app` and a `.dmg`. The app runs on the same kind of Mac (Apple Silicon or Intel) it was built on.

### Releases (GitHub Actions)
`.github/workflows/build-mac.yml` builds on GitHub's Apple Silicon (`macos-15`) and Intel (`macos-15-intel`) Macs.
- To publish a release with both `.dmg` files, change the number in `VERSION` (e.g. `1.1.0`) and push to `main`. Pushing a tag like `v1.1.0` works too.
- Or use **Actions → Build Mac app → Run workflow** for a test build. The `.dmg` files are under the run's Artifacts.

### Files
| File | What it is |
|---|---|
| `pdf_redactor.py` | The app window |
| `redact_core.py` | Detection, redaction, cleanup, verification and self-test |
| `data/` | US Census name lists (public domain) |
| `tessdata/eng.traineddata` | English OCR model (Apache-2.0) |
| `build_mac_app.sh`, `pdf_redactor.spec`, `icon.png` | Mac app build |

## License

Copyright © 2026 Kiran.

PDF Redactor is free software under the **GNU Affero General Public License v3.0 or later** (see [`LICENSE`](LICENSE)). It's AGPL because it is built on [PyMuPDF](https://github.com/pymupdf/PyMuPDF), which is AGPL-licensed. You may use, share and modify it. If you distribute a modified version, you must share its source code under the same license.

Bundled third-party components and their licenses are listed in [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).

This program is provided **without any warranty**, including any guarantee that it finds all sensitive information. You are responsible for reviewing documents before sharing them.
