# -*- coding: utf-8 -*-
import pdfplumber
pdf_path = r"C:\Users\Alberto\Downloads\oc medias navidad 1.pdf"
with pdfplumber.open(pdf_path) as pdf:
    print("pages:", len(pdf.pages))
    for i, page in enumerate(pdf.pages):
        print("=== PAGE", i + 1, page.width, page.height, "===")
        t = page.extract_text() or ""
        print(t)
        print("--- chars:", len(page.chars), "images:", len(page.images), "rects:", len(page.rects), "lines:", len(page.lines))
        tabs = page.extract_tables() or []
        print("tables:", len(tabs))
        for tb in tabs:
            for row in tb:
                print(row)
