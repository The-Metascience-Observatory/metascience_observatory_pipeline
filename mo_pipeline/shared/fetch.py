"""The one import path for fetchpdf in this repo.

fetchpdf reads ELSEVIER_TDM_API_KEY once, when it is first imported, and the key
lives in fetchpdf_grey's .env.local, which only `import fetchpdf_grey` loads. A
module that imported fetchpdf directly therefore fetched without the key (Elsevier
supplies most corpus XML), and silently: corpus backfill and stage 5 both did.
Import fetchpdf through here and the order is always right.

Importing fetchpdf_grey also installs its last-resort sources. Callers that must
stay on legal sources call `disable_last_resorts()`, as `--legalonly` does.
"""
import fetchpdf_grey  # noqa: F401  -- must precede `import fetchpdf`
from fetchpdf import batch_fetch_pdfs, fetch_pdf_from_doi
from fetchpdf_grey import disable_last_resorts

__all__ = ["batch_fetch_pdfs", "fetch_pdf_from_doi", "disable_last_resorts"]
