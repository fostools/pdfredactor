# PDF Redactor - offline PDF redaction tool
# Copyright (C) 2026 Kiran
# SPDX-License-Identifier: AGPL-3.0-or-later
# This program comes with ABSOLUTELY NO WARRANTY. See the LICENSE file.
"""
PDF Redactor - offline desktop app.
Open a PDF (it scans automatically) -> review / untick findings -> Save redacted PDF.
Network access is disabled for the whole process before anything else loads.
"""
import sys

from redact_core import block_network
block_network()  # must run first

if __name__ == "__main__" and "--selftest" in sys.argv:   # headless check, no window needed
    from redact_core import self_test
    sys.exit(0 if self_test() else 1)

import os
import re
import shutil
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from redact_core import Redactor

AI_CHOICES = ["Name", "Organization", "Place", "Facility"]
OCR_MODES = {"Scanned pages only": "auto",
             "Scanned pages + images in normal pages": "all",
             "Off": "off"}
SOURCE_LABEL = {"rule": "pattern", "label": "form label", "names": "name list", "ai": "AI",
                "barcode": "barcode", "custom": "your list", "repeat": "repeat of name"}
def _settings_dir():
    """Per-user settings folder on this computer (never synced anywhere by the app)."""
    if sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Application Support/PDF Redactor")
    elif os.name == "nt":
        base = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "PDF Redactor")
    else:
        base = os.path.expanduser("~/.config/pdf-redactor")
    os.makedirs(base, exist_ok=True)
    return base


TERMS_FILE = os.path.join(_settings_dir(), "always_redact.txt")
_OLD_TERMS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "always_redact.txt")
if not os.path.exists(TERMS_FILE) and os.path.exists(_OLD_TERMS):   # carry over from older versions
    try:
        shutil.copy(_OLD_TERMS, TERMS_FILE)
    except OSError:
        pass


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("PDF Redactor  •  OFFLINE")
        self.geometry("1150x720")
        self.minsize(900, 520)
        self.pdf_path = None
        self.findings = []
        self.redactor = None
        self._busy = False

        # ---- top bar ----
        top = ttk.Frame(self, padding=8)
        top.pack(fill="x")
        ttk.Button(top, text="Open PDF…", command=self.open_pdf).pack(side="left")
        self.scan_btn = ttk.Button(top, text="▶ Scan", command=self.scan)
        self.scan_btn.pack(side="left", padx=(6, 0))
        self.save_btn = ttk.Button(top, text="Save redacted PDF…", command=self.save)
        self.save_btn.pack(side="left", padx=(6, 0))
        self.file_lbl = ttk.Label(top, text="No file loaded", foreground="#666")
        self.file_lbl.pack(side="left", padx=10)
        ttk.Label(top, text="\U0001F512 Network disabled", foreground="#1a7f37").pack(side="right")

        # ---- options ----
        opts = ttk.LabelFrame(self, text="Options", padding=8)
        opts.pack(fill="x", padx=8)
        row = ttk.Frame(opts)
        row.pack(fill="x")
        self.use_ai = tk.BooleanVar(value=True)
        ttk.Checkbutton(row, text="Local AI finds:", variable=self.use_ai).pack(side="left")
        self.ai_vars = {}
        for c in AI_CHOICES:
            v = tk.BooleanVar(value=(c == "Name"))
            self.ai_vars[c] = v
            ttk.Checkbutton(row, text=c, variable=v).pack(side="left", padx=4)
        ttk.Label(row, text="   OCR:").pack(side="left")
        self.ocr_mode = tk.StringVar(value="Scanned pages only")
        ttk.Combobox(row, textvariable=self.ocr_mode, state="readonly", width=34,
                     values=list(OCR_MODES)).pack(side="left", padx=4)

        row2 = ttk.Frame(opts)
        row2.pack(fill="x", pady=(6, 0))
        ttk.Label(row2, text="Always redact (comma-separated, remembered on this computer):").pack(side="left")
        self.custom = ttk.Entry(row2)
        self.custom.pack(side="left", fill="x", expand=True, padx=6)
        self.custom.insert(0, self._load_terms())

        row3 = ttk.Frame(opts)
        row3.pack(fill="x", pady=(6, 0))
        self.barcodes = tk.BooleanVar(value=True)
        ttk.Checkbutton(row3, text="Remove QR codes & barcodes", variable=self.barcodes).pack(side="left")
        self.label_boxes = tk.BooleanVar(value=True)
        ttk.Checkbutton(row3, text="Label boxes (e.g. [SSN / ITIN])",
                        variable=self.label_boxes).pack(side="left", padx=12)
        self.flatten = tk.BooleanVar(value=False)
        ttk.Checkbutton(row3, text="Flatten output to images (max safety, no selectable text)",
                        variable=self.flatten).pack(side="left", padx=12)

        # ---- findings table ----
        mid = ttk.Frame(self, padding=(8, 8))
        mid.pack(fill="both", expand=True)
        cols = ("redact", "page", "type", "found by", "text")
        self.tree = ttk.Treeview(mid, columns=cols, show="headings", selectmode="extended")
        for c, w in zip(cols, (70, 50, 170, 130, 600)):
            self.tree.heading(c, text=c.title())
            self.tree.column(c, width=w, anchor="w", stretch=(c == "text"))
        self.tree.tag_configure("off", foreground="#999")
        sb = ttk.Scrollbar(mid, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.tree.bind("<Double-1>", self.toggle)
        self.tree.bind("<space>", self.toggle)

        # ---- bottom bar ----
        bot = ttk.Frame(self, padding=8)
        bot.pack(fill="x")
        ttk.Label(bot, text="Double-click or Space toggles a row.").pack(side="left")
        ttk.Button(bot, text="Add term…", command=self.add_manual).pack(side="left", padx=10)
        ttk.Button(bot, text="Tick all", command=lambda: self.set_all(True)).pack(side="left")
        ttk.Button(bot, text="Untick all", command=lambda: self.set_all(False)).pack(side="left", padx=4)
        self.status = ttk.Label(bot, text="")
        self.status.pack(side="right", padx=10)

    # ------------------------------------------------------------------
    @staticmethod
    def _load_terms():
        try:
            with open(TERMS_FILE, encoding="utf-8") as fh:
                return fh.read().strip()
        except OSError:
            return ""

    def _save_terms(self):
        try:
            with open(TERMS_FILE, "w", encoding="utf-8") as fh:
                fh.write(self.custom.get().strip())
        except OSError:
            pass

    def _set_busy(self, busy):
        self._busy = busy
        for b in (self.scan_btn, self.save_btn):
            b.config(state="disabled" if busy else "normal")

    def _run_bg(self, work, done):
        """Run work() on a thread, then done(result) on the UI thread."""
        box = {"progress": "Working…"}

        def runner():
            try:
                box["result"] = work(box)
            except Exception as e:  # noqa
                box["error"] = e

        def poll():
            if "result" in box or "error" in box:
                self._set_busy(False)
                if "error" in box:
                    self.status.config(text="")
                    messagebox.showerror("PDF Redactor", str(box["error"]))
                else:
                    done(box["result"])
                return
            self.status.config(text=box["progress"])
            self.after(150, poll)

        self._set_busy(True)
        threading.Thread(target=runner, daemon=True).start()
        poll()

    # ------------------------------------------------------------------
    def open_pdf(self):
        if self._busy:
            return
        p = filedialog.askopenfilename(filetypes=[("PDF", "*.pdf"), ("All files", "*.*")])
        if p:
            self.pdf_path = p
            self.file_lbl.config(text=os.path.basename(p), foreground="#000")
            self.findings = []
            self.refresh()
            self.scan()

    def scan(self):
        if self._busy:
            return
        if not self.pdf_path:
            return messagebox.showinfo("PDF Redactor", "Open a PDF first.")
        self._save_terms()
        cats = [c for c, v in self.ai_vars.items() if v.get()]
        opts = dict(use_ai=self.use_ai.get() and bool(cats), ai_categories=cats,
                    custom_terms=self.custom.get().split(","),
                    ocr_mode=OCR_MODES[self.ocr_mode.get()], barcodes=self.barcodes.get())
        path = self.pdf_path

        def work(box):
            box["progress"] = "Loading local AI model…"
            r = Redactor(**opts)

            def prog(i, n):
                box["progress"] = f"Scanning page {i}/{n}…"
            return (r, *r.scan(path, progress=prog))

        self._run_bg(work, self._scan_done)

    def _scan_done(self, result):
        self.redactor, self.findings, info = result
        self.refresh()
        msg = f"{len(self.findings)} items found"
        if info["ocr_pages"]:
            msg += f"  •  OCR on page(s) {', '.join(map(str, info['ocr_pages']))}"
        if info.get("model"):
            msg += f"  •  AI: {info['model']}"
        self.status.config(text=msg)

        warnings = []
        if info["unreadable_pages"]:
            why = ("OCR is turned off" if info["ocr_available"] else
                   "Tesseract OCR isn't installed (see README)")
            warnings.append(f"Pages {info['unreadable_pages']} are scans with no text and {why}. "
                            "Nothing on those pages was found.")
        if info.get("barcode_lib_missing"):
            warnings.append("QR/barcode removal is unavailable: run  pip install zxing-cpp numpy")
        if info.get("unlocated"):
            items = "\n".join(f"  page {p}: {k} — {v}" for p, k, v in info["unlocated"][:10])
            warnings.append("Found but couldn't pinpoint on the page (redact these by hand "
                            "or add them to Always redact):\n" + items)
        if info["ocr_pages"]:
            warnings.append(f"Page(s) {info['ocr_pages']} were read with OCR, which can misread "
                            "blurry or handwritten text. Glance over them in the output.")
        if warnings:
            messagebox.showwarning("Please review", "\n\n".join(warnings))

    def refresh(self):
        self.tree.delete(*self.tree.get_children())
        for i, f in enumerate(self.findings):
            src = SOURCE_LABEL.get(f.source, f.source) + (" +OCR" if f.ocr else "")
            self.tree.insert("", "end", iid=str(i), tags=() if f.keep else ("off",), values=(
                "✔ yes" if f.keep else "✘ no", f.page + 1, f.kind, src, f.text))

    def toggle(self, _=None):
        sel = self.tree.selection()
        for iid in sel:
            self.findings[int(iid)].keep = not self.findings[int(iid)].keep
        self.refresh()
        self.tree.selection_set(sel)

    def set_all(self, keep):
        for f in self.findings:
            f.keep = keep
        self.refresh()

    def add_manual(self):
        if not self.pdf_path or self._busy:
            return
        win = tk.Toplevel(self)
        win.title("Add term")
        ttk.Label(win, text="Word or phrase to always redact:").pack(padx=10, pady=(10, 0))
        e = ttk.Entry(win, width=50)
        e.pack(padx=10, pady=10)
        e.focus()

        def ok():
            term = e.get().strip()
            win.destroy()
            if term:
                cur = self.custom.get().strip()
                self.custom.delete(0, "end")
                self.custom.insert(0, f"{cur}, {term}" if cur else term)
                self.scan()
        ttk.Button(win, text="Add & rescan", command=ok).pack(pady=(0, 10))
        win.bind("<Return>", lambda _: ok())

    def _safe_filename(self):
        """Default output name, with any redacted value removed from the filename."""
        base = os.path.splitext(os.path.basename(self.pdf_path))[0]
        for f in self.findings:
            if f.keep and f.kind != "QR / barcode":
                for part in [f.text] + f.text.replace(",", " ").split():
                    if len(part) >= 3:
                        base = re.sub(re.escape(part), "X", base, flags=re.I)
        return base + "_REDACTED.pdf"

    def save(self):
        if self._busy:
            return
        if not self.findings:
            return messagebox.showinfo("PDF Redactor", "Nothing to redact yet — open a PDF first.")
        out = filedialog.asksaveasfilename(defaultextension=".pdf", initialfile=self._safe_filename(),
                                           initialdir=os.path.dirname(self.pdf_path))
        if not out:
            return
        if os.path.abspath(out) == os.path.abspath(self.pdf_path):
            return messagebox.showerror("PDF Redactor", "Save to a new file, not over the original.")
        src, findings, r = self.pdf_path, self.findings, self.redactor
        label, flatten = self.label_boxes.get(), self.flatten.get()

        def work(box):
            box["progress"] = "Redacting…"
            Redactor.apply(src, out, findings, label=label, flatten=flatten)
            box["progress"] = "Double-checking the saved file…"
            return r.verify(out, findings)

        self._run_bg(work, lambda rep: self._save_done(out, rep))

    def _save_done(self, out, rep):
        self.status.config(text="Saved")
        problems = []
        if rep["leaks"]:
            problems.append("Still readable in the output:\n  " + "\n  ".join(rep["leaks"]))
        if rep["barcodes"]:
            problems.append("Barcodes still detected:\n  " + "\n  ".join(rep["barcodes"]))
        if rep["metadata"]:
            problems.append("Hidden data still present:\n  " + "\n  ".join(rep["metadata"]))
        if problems:
            messagebox.showwarning("Check needed", "\n\n".join(problems) +
                                   "\n\nTry 'Flatten output to images' for a guaranteed-clean file.")
        else:
            n = sum(f.keep for f in self.findings)
            messagebox.showinfo("Done", f"Redacted {n} items.\n\nDouble-checked the saved file:\n"
                                "  ✔ none of the redacted text is readable\n"
                                "  ✔ no QR codes / barcodes left\n"
                                "  ✔ no metadata, comments, attachments or form data\n\n" + out)


if __name__ == "__main__":
    App().mainloop()
