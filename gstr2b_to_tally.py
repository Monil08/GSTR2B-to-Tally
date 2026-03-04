"""
GSTR-2B to TallyPrime Import Tool
Requirements: pip install fastapi uvicorn requests python-multipart openpyxl pandas
Run with:     python gstr2b_to_tally.py
Open:         http://localhost:8000/docs

By Monil Shah

Workflow:
  Step 1 - POST /check-ledgers        find missing ledgers from GSTR-2B JSON (no entries created)
  Step 2 - POST /upload-gstr2b        import from GSTR-2B JSON
  Step 1 - POST /check-ledgers-excel  find missing ledgers from Excel/CSV (no entries created)
  Step 2 - POST /upload-excel         import from Excel/CSV
  Debug  - GET  /debug-tally          see last raw Tally response
  Debug  - GET  /debug-xml            export a real voucher from Tally to see its field names
"""

import json
import re
import html
import requests
import uvicorn
import pandas as pd
from io import BytesIO
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, UploadFile, File, Query
from fastapi.responses import JSONResponse, PlainTextResponse

TALLY_URL       = "http://localhost:9000"
LEDGER_PURCHASE = "Purchase"
LEDGER_CGST     = "Input CGST"
LEDGER_SGST     = "Input SGST"
LEDGER_IGST     = "Input IGST"
DEBUG_FILE      = "tally_debug.txt"
MISSING_FILE    = "missing_ledgers.txt"
DATE_DEBUG_FILE = "date_errors.txt"

app = FastAPI(
    title="GSTR-2B to TallyPrime Import — By Monil Shah",
    description="Import B2B purchase invoices from GSTR-2B JSON or Excel/CSV into TallyPrime.\n\n**By Monil Shah**",
    version="1.0.0",
)


# ─────────────────────────────────────────────────────────────
#  SHARED HELPERS
# ─────────────────────────────────────────────────────────────

def safe_float(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def parse_date(raw: str) -> str:
    if not raw:
        return ""
    raw = str(raw).strip()
    formats = [
        "%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d", "%Y%m%d",
        "%d-%b-%Y", "%d-%B-%Y", "%d %b %Y", "%d %B %Y",
        "%d-%m-%y", "%d/%m/%y", "%m/%d/%Y", "%m-%d-%Y",
        "%b %d, %Y", "%B %d, %Y",
    ]
    for fmt in formats:
        try:
            dt = datetime.strptime(raw, fmt)
            if dt.year < 2000:
                dt = dt.replace(year=dt.year + 100)
            return dt.strftime("%Y%m%d")
        except ValueError:
            continue
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 8:
        for fmt in ["%d%m%Y", "%Y%m%d"]:
            try:
                return datetime.strptime(digits, fmt).strftime("%Y%m%d")
            except ValueError:
                continue
    with open(DATE_DEBUG_FILE, "a", encoding="utf-8") as f:
        f.write(f"Unrecognised date: '{raw}'\n")
    return ""


def xml_escape(text: str) -> str:
    return html.escape(str(text), quote=False)


# ─────────────────────────────────────────────────────────────
#  JSON EXTRACTION
# ─────────────────────────────────────────────────────────────

def extract_b2b(raw: dict) -> list:
    b2b = None
    if "b2b" in raw:
        b2b = raw["b2b"]
    elif isinstance(raw.get("data"), dict):
        data = raw["data"]
        if "b2b" in data:
            b2b = data["b2b"]
        elif isinstance(data.get("docdata"), dict) and "b2b" in data["docdata"]:
            b2b = data["docdata"]["b2b"]
    if not b2b:
        return []
    invoices = []
    for sup in b2b:
        party = (
            sup.get("trdnm") or sup.get("tradeName")
            or sup.get("ctin") or sup.get("gstin") or ""
        ).strip()
        for inv in (sup.get("inv") or sup.get("invs") or []):
            taxable = cgst = sgst = igst = 0.0
            itms = inv.get("itms") or []
            if itms:
                for item in itms:
                    det = item.get("itm_det") or item
                    taxable += safe_float(det.get("txval"))
                    cgst    += safe_float(det.get("camt"))
                    sgst    += safe_float(det.get("samt"))
                    igst    += safe_float(det.get("iamt"))
            else:
                taxable = safe_float(inv.get("txval") or inv.get("taxablevalue") or inv.get("val"))
                cgst    = safe_float(inv.get("camt") or inv.get("cgst"))
                sgst    = safe_float(inv.get("samt") or inv.get("sgst"))
                igst    = safe_float(inv.get("iamt") or inv.get("igst"))
            raw_date = str(inv.get("idt") or inv.get("invdt") or inv.get("dt") or "")
            invoices.append({
                "party":    party,
                "inv_no":   str(inv.get("inum") or inv.get("invnum") or "").strip(),
                "date":     parse_date(raw_date),
                "raw_date": raw_date,
                "taxable":  round(taxable, 2),
                "cgst":     round(cgst, 2),
                "sgst":     round(sgst, 2),
                "igst":     round(igst, 2),
            })
    return invoices


# ─────────────────────────────────────────────────────────────
#  EXCEL / CSV EXTRACTION
# ─────────────────────────────────────────────────────────────

COLUMN_ALIASES = {
    "party":   ["party_name", "party name", "supplier", "tradename", "trade name",
                 "supplier name", "vendor name", "vendor"],
    "inv_no":  ["invoice_number", "invoice number", "invoice no", "invoice_no",
                 "inv no", "inv_no", "bill no", "bill_no", "bill number"],
    "date":    ["invoice_date", "invoice date", "inv date", "inv_date",
                 "date", "bill date", "bill_date"],
    "taxable": ["taxable_value", "taxable value", "taxable amt", "taxable_amt",
                 "taxable amount", "basic value", "basic_value", "base amount"],
    "cgst":    ["cgst", "cgst_amount", "cgst amount", "central gst"],
    "sgst":    ["sgst", "sgst_amount", "sgst amount", "state gst"],
    "igst":    ["igst", "igst_amount", "igst amount", "integrated gst"],
}

def _normalise(col: str) -> str:
    return re.sub(r"[\s_]+", " ", col.strip().lower())

def _map_columns(df_columns: list) -> dict:
    norm_to_actual = {_normalise(c): c for c in df_columns}
    mapping = {}
    for field, aliases in COLUMN_ALIASES.items():
        for alias in aliases:
            key = _normalise(alias)
            if key in norm_to_actual:
                mapping[field] = norm_to_actual[key]
                break
    return mapping

def extract_excel(file_bytes: bytes, filename: str) -> tuple[list, list]:
    fname = filename.lower()
    if fname.endswith(".csv"):
        try:
            df = pd.read_csv(BytesIO(file_bytes), dtype=str, keep_default_na=False)
        except Exception as e:
            raise ValueError(f"Cannot read CSV: {e}")
    elif fname.endswith((".xlsx", ".xls")):
        try:
            df = pd.read_excel(BytesIO(file_bytes), dtype=str, keep_default_na=False)
        except Exception as e:
            raise ValueError(f"Cannot read Excel: {e}")
    else:
        raise ValueError("Unsupported file type. Upload .xlsx, .xls, or .csv")

    df = df.dropna(how="all").reset_index(drop=True)
    if df.empty:
        raise ValueError("File has no data rows.")

    col_map = _map_columns(list(df.columns))

    for req in ("party", "inv_no", "date", "taxable"):
        if req not in col_map:
            human = {"party": "Party Name", "inv_no": "Invoice Number",
                     "date": "Invoice Date", "taxable": "Taxable Value"}[req]
            raise ValueError(
                f"Required column '{human}' not found. "
                f"Columns detected: {list(df.columns)}. "
                f"Rename your header to match one of: {COLUMN_ALIASES[req]}"
            )

    invoices = []
    warnings = []

    for idx, row in df.iterrows():
        row_num  = idx + 2
        party    = str(row[col_map["party"]]).strip()
        inv_no   = str(row[col_map["inv_no"]]).strip()
        raw_date = str(row[col_map["date"]]).strip()
        taxable  = safe_float(row[col_map["taxable"]])
        cgst     = safe_float(row[col_map["cgst"]]) if "cgst" in col_map else 0.0
        sgst     = safe_float(row[col_map["sgst"]]) if "sgst" in col_map else 0.0
        igst     = safe_float(row[col_map["igst"]]) if "igst" in col_map else 0.0

        if not party and not inv_no and not raw_date:
            continue

        parsed_date = parse_date(raw_date)
        if not parsed_date and raw_date:
            warnings.append(f"Row {row_num}: unrecognised date '{raw_date}' for invoice '{inv_no}'")

        invoices.append({
            "party":    party,
            "inv_no":   inv_no,
            "date":     parsed_date,
            "raw_date": raw_date,
            "taxable":  round(taxable, 2),
            "cgst":     round(cgst, 2),
            "sgst":     round(sgst, 2),
            "igst":     round(igst, 2),
        })

    return invoices, warnings


# ─────────────────────────────────────────────────────────────
#  TALLY XML BUILDER
# ─────────────────────────────────────────────────────────────

def build_voucher_xml(inv: dict) -> str:
    party  = xml_escape(inv["party"])
    inv_no = xml_escape(inv["inv_no"])
    total  = round(inv["taxable"] + inv["cgst"] + inv["sgst"] + inv["igst"], 2)

    tax_lines = ""
    if inv["cgst"]:
        tax_lines += f"""
          <ALLLEDGERENTRIES.LIST>
            <LEDGERNAME>{LEDGER_CGST}</LEDGERNAME>
            <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
            <AMOUNT>-{inv['cgst']:.2f}</AMOUNT>
          </ALLLEDGERENTRIES.LIST>"""
    if inv["sgst"]:
        tax_lines += f"""
          <ALLLEDGERENTRIES.LIST>
            <LEDGERNAME>{LEDGER_SGST}</LEDGERNAME>
            <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
            <AMOUNT>-{inv['sgst']:.2f}</AMOUNT>
          </ALLLEDGERENTRIES.LIST>"""
    if inv["igst"]:
        tax_lines += f"""
          <ALLLEDGERENTRIES.LIST>
            <LEDGERNAME>{LEDGER_IGST}</LEDGERNAME>
            <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
            <AMOUNT>-{inv['igst']:.2f}</AMOUNT>
          </ALLLEDGERENTRIES.LIST>"""

    return f"""<ENVELOPE>
  <HEADER>
    <TALLYREQUEST>Import Data</TALLYREQUEST>
  </HEADER>
  <BODY>
    <IMPORTDATA>
      <REQUESTDESC>
        <REPORTNAME>Vouchers</REPORTNAME>
      </REQUESTDESC>
      <REQUESTDATA>
        <TALLYMESSAGE xmlns:UDF="TallyUDF">
          <VOUCHER VCHTYPE="Purchase" ACTION="Create" OBJVIEW="Accounting Voucher View">
            <DATE>{inv['date']}</DATE>
            <VOUCHERTYPENAME>Purchase</VOUCHERTYPENAME>
            <ISINVOICE>No</ISINVOICE>
            <PARTYLEDGERNAME>{party}</PARTYLEDGERNAME>
            <REFERENCE>{inv_no}</REFERENCE>
            <BASICBUYERDATE>{inv['date']}</BASICBUYERDATE>
            <NARRATION>GSTR-2B | {party} | {inv_no}</NARRATION>
            <ALLLEDGERENTRIES.LIST>
              <LEDGERNAME>{party}</LEDGERNAME>
              <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
              <AMOUNT>{total:.2f}</AMOUNT>
            </ALLLEDGERENTRIES.LIST>
            <ALLLEDGERENTRIES.LIST>
              <LEDGERNAME>{LEDGER_PURCHASE}</LEDGERNAME>
              <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
              <AMOUNT>-{inv['taxable']:.2f}</AMOUNT>
            </ALLLEDGERENTRIES.LIST>
            {tax_lines}
          </VOUCHER>
        </TALLYMESSAGE>
      </REQUESTDATA>
    </IMPORTDATA>
  </BODY>
</ENVELOPE>"""


# ─────────────────────────────────────────────────────────────
#  TALLY COMMUNICATION
# ─────────────────────────────────────────────────────────────

def push_to_tally(xml_payload: str) -> str:
    try:
        resp = requests.post(
            TALLY_URL,
            data=xml_payload.encode("utf-8"),
            headers={"Content-Type": "application/xml"},
            timeout=30,
        )
        resp.raise_for_status()
    except requests.exceptions.ConnectionError:
        raise RuntimeError("Cannot connect to TallyPrime on port 9000.")
    except requests.exceptions.Timeout:
        raise RuntimeError("TallyPrime timed out. Dismiss any open dialogs and retry.")
    except Exception as e:
        raise RuntimeError(f"HTTP error: {e}")
    text = resp.text
    Path(DEBUG_FILE).write_text(text, encoding="utf-8", errors="replace")
    if "Unknown Request" in text or "cannot be processed" in text:
        return "unknown_request"
    if "<LINEERROR>" in text:
        errs = re.findall(r"<LINEERROR>(.*?)</LINEERROR>", text, re.IGNORECASE)
        combined = " | ".join(errs)
        m = re.search(
            r"(?:Ledger\s+[\"']?([^\"'<|]+?)[\"']?\s+(?:is not available|not found)"
            r"|\"([^\"]+)\"\s+is not available)",
            combined, re.IGNORECASE
        )
        if m:
            name = next(g for g in m.groups() if g)
            return f"missing_ledger:{name.strip()}"
        return f"error:{combined}"
    created = re.search(r"<CREATED>(\d+)</CREATED>", text)
    altered = re.search(r"<ALTERED>(\d+)</ALTERED>", text)
    errors  = re.search(r"<ERRORS>(\d+)</ERRORS>",   text)
    if errors and int(errors.group(1)) > 0:
        return f"error:Tally reported {errors.group(1)} error(s). Check tally_debug.txt."
    if (created and int(created.group(1)) >= 1) or (altered and int(altered.group(1)) >= 1):
        return "ok"
    return "edu_blocked"


def probe_ledger_exists(ledger_name: str, probe_date: str) -> bool:
    probe_xml = f"""<ENVELOPE>
  <HEADER>
    <TALLYREQUEST>Import Data</TALLYREQUEST>
  </HEADER>
  <BODY>
    <IMPORTDATA>
      <REQUESTDESC>
        <REPORTNAME>Vouchers</REPORTNAME>
      </REQUESTDESC>
      <REQUESTDATA>
        <TALLYMESSAGE xmlns:UDF="TallyUDF">
          <VOUCHER VCHTYPE="Purchase" ACTION="Create">
            <DATE>{probe_date}</DATE>
            <VOUCHERTYPENAME>Purchase</VOUCHERTYPENAME>
            <VOUCHERNUMBER>__LEDGER_PROBE__</VOUCHERNUMBER>
            <PARTYLEDGERNAME>{xml_escape(ledger_name)}</PARTYLEDGERNAME>
            <ALLLEDGERENTRIES.LIST>
              <LEDGERNAME>{xml_escape(ledger_name)}</LEDGERNAME>
              <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
              <AMOUNT>0.00</AMOUNT>
            </ALLLEDGERENTRIES.LIST>
          </VOUCHER>
        </TALLYMESSAGE>
      </REQUESTDATA>
    </IMPORTDATA>
  </BODY>
</ENVELOPE>"""
    try:
        resp = requests.post(
            TALLY_URL,
            data=probe_xml.encode("utf-8"),
            headers={"Content-Type": "application/xml"},
            timeout=15,
        )
        text = resp.text
    except Exception:
        raise RuntimeError("Cannot connect to TallyPrime on port 9000.")
    if "<LINEERROR>" in text:
        errs = " | ".join(re.findall(r"<LINEERROR>(.*?)</LINEERROR>", text, re.IGNORECASE))
        if re.search(r"(?:is not available|not found|does not exist)", errs, re.IGNORECASE):
            return False
        return True
    return True


# ─────────────────────────────────────────────────────────────
#  SHARED IMPORT ENGINE
# ─────────────────────────────────────────────────────────────

def _run_import(invoices: list) -> JSONResponse:
    imported        = 0
    failed          = []
    missing_ledgers = set()
    edu_blocked     = 0
    format_rejected = 0

    for inv in invoices:
        if not inv["inv_no"]:
            failed.append({"invoice": "(blank)", "error": "Missing invoice number."})
            continue
        if not inv["date"]:
            failed.append({"invoice": inv["inv_no"], "error": f"Unrecognised date. Raw value: '{inv['raw_date']}'"})
            continue
        if not inv["party"]:
            failed.append({"invoice": inv["inv_no"], "error": "Missing party name."})
            continue
        try:
            result = push_to_tally(build_voucher_xml(inv))
        except RuntimeError as e:
            failed.append({"invoice": inv["inv_no"], "error": str(e)})
            continue
        if result == "ok":
            imported += 1
        elif result == "unknown_request":
            format_rejected += 1
            break
        elif result.startswith("missing_ledger:"):
            ledger = result.split(":", 1)[1]
            missing_ledgers.add(ledger)
            failed.append({"invoice": inv["inv_no"], "error": f"Ledger not found: '{ledger}'"})
        elif result == "edu_blocked":
            edu_blocked += 1
            failed.append({"invoice": inv["inv_no"], "error": "CREATED=0 - press F2 in Tally to check period."})
        else:
            failed.append({"invoice": inv["inv_no"], "error": result})

    if missing_ledgers:
        Path(MISSING_FILE).write_text(
            "Missing Ledgers\n" + "=" * 60 + "\n\n"
            + "\n".join(f"  * {n}" for n in sorted(missing_ledgers))
            + "\n\nCreate as Sundry Creditors with exact name as above.\n",
            encoding="utf-8"
        )

    if format_rejected > 0:
        return JSONResponse(status_code=503, content={
            "status": "error",
            "reason": "TallyPrime returned Unknown Request. Enable XML gateway: F12 > Advanced Config > Enable XML Server, port 9000.",
        })
    if imported == 0 and edu_blocked > 0:
        return JSONResponse(status_code=503, content={
            "status": "error",
            "reason": "All vouchers rejected (CREATED=0). Press F2 in TallyPrime to set the correct financial year period.",
        })
    if missing_ledgers:
        return JSONResponse(status_code=422, content={
            "status":          "error",
            "reason":          "Missing ledger(s). See missing_ledgers.txt.",
            "missing_ledgers": sorted(missing_ledgers),
            "failed_invoices": failed,
        })
    if failed:
        return JSONResponse(status_code=207, content={
            "status":          "partial",
            "imported":        imported,
            "failed_count":    len(failed),
            "failed_invoices": failed,
            "message":         f"{imported} imported. {len(failed)} failed.",
        })
    return JSONResponse(status_code=200, content={
        "status":   "success",
        "imported": imported,
        "message":  f"All {imported} invoice(s) successfully imported into TallyPrime.",
    })


# ─────────────────────────────────────────────────────────────
#  API ENDPOINTS — JSON
# ─────────────────────────────────────────────────────────────

@app.post("/check-ledgers")
async def check_ledgers(file: UploadFile = File(...)):
    """
    Upload your GSTR-2B JSON to get a full list of ledgers missing in TallyPrime.
    NO voucher entries are created in Tally. Completely safe to run anytime.
    """
    try:
        content = await file.read()
        raw     = json.loads(content)
    except json.JSONDecodeError as e:
        return JSONResponse(status_code=400, content={"status": "error", "reason": f"Invalid JSON: {e}"})
    try:
        invoices = extract_b2b(raw)
    except Exception as e:
        return JSONResponse(status_code=500, content={"status": "error", "reason": f"Parse error: {e}"})
    if not invoices:
        return JSONResponse(status_code=400, content={"status": "error", "reason": "No B2B invoices found."})

    required_ledgers = {LEDGER_PURCHASE, LEDGER_CGST, LEDGER_SGST, LEDGER_IGST}
    for inv in invoices:
        if inv["party"]:
            required_ledgers.add(inv["party"])

    today = datetime.today()
    probe_date = f"{today.year}0401" if today.month >= 4 else f"{today.year - 1}0401"

    missing = []
    present = []

    for ledger_name in sorted(required_ledgers):
        try:
            exists = probe_ledger_exists(ledger_name, probe_date)
        except RuntimeError as e:
            return JSONResponse(status_code=503, content={"status": "error", "reason": str(e)})
        if exists:
            present.append(ledger_name)
        else:
            missing.append(ledger_name)

    if missing:
        lines = [
            "Missing Ledgers - Create these in TallyPrime before importing",
            "=" * 60, "",
        ]
        for name in missing:
            if name in (LEDGER_PURCHASE, LEDGER_CGST, LEDGER_SGST, LEDGER_IGST):
                lines.append(f"  * {name}  <-- create as tax/purchase ledger")
            else:
                lines.append(f"  * {name}  <-- create as Sundry Creditor")
        lines += ["", "Once all ledgers are created, use /upload-gstr2b to import."]
        Path(MISSING_FILE).write_text("\n".join(lines), encoding="utf-8")

    return JSONResponse(status_code=200, content={
        "status":          "ok",
        "total_required":  len(required_ledgers),
        "missing_count":   len(missing),
        "present_count":   len(present),
        "missing_ledgers": missing,
        "present_ledgers": present,
        "message": (
            f"All {len(required_ledgers)} ledgers present. Ready to import."
            if not missing else
            f"{len(missing)} ledger(s) missing. Create them in TallyPrime, then use /upload-gstr2b."
        ),
        "missing_ledgers_file": str(Path(MISSING_FILE).resolve()) if missing else None,
    })


@app.post("/upload-gstr2b")
async def upload_gstr2b(file: UploadFile = File(...)):
    """Upload GSTR-2B JSON to import all B2B invoices as Purchase vouchers into TallyPrime."""
    Path(DATE_DEBUG_FILE).write_text("", encoding="utf-8")
    try:
        content = await file.read()
        raw     = json.loads(content)
    except json.JSONDecodeError as e:
        return JSONResponse(status_code=400, content={"status": "error", "reason": f"Invalid JSON: {e}"})
    try:
        invoices = extract_b2b(raw)
    except Exception as e:
        return JSONResponse(status_code=500, content={"status": "error", "reason": f"Parse error: {e}"})
    if not invoices:
        return JSONResponse(status_code=400, content={"status": "error", "reason": "No B2B invoices found."})

    bad_dates = [inv for inv in invoices if not inv["date"] and inv["raw_date"]]
    if len(bad_dates) == len(invoices):
        sample = list({inv["raw_date"] for inv in bad_dates})[:5]
        return JSONResponse(status_code=422, content={
            "status": "error",
            "reason": "Could not parse any invoice dates. Share sample_date_values so the format can be added.",
            "sample_date_values_from_json": sample,
        })

    return _run_import(invoices)


# ─────────────────────────────────────────────────────────────
#  API ENDPOINTS — EXCEL / CSV
# ─────────────────────────────────────────────────────────────

@app.post("/upload-excel")
async def upload_excel(file: UploadFile = File(...)):
    """
    Upload an Excel (.xlsx / .xls) or CSV file to import invoices as Purchase vouchers.

    Required columns (flexible naming — exact spelling not needed):
      Party Name | Invoice Number | Invoice Date | Taxable Value

    Optional columns:
      CGST | SGST | IGST
    """
    Path(DATE_DEBUG_FILE).write_text("", encoding="utf-8")

    content  = await file.read()
    filename = file.filename or ""

    try:
        invoices, warnings = extract_excel(content, filename)
    except ValueError as e:
        return JSONResponse(status_code=400, content={"status": "error", "reason": str(e)})
    except Exception as e:
        return JSONResponse(status_code=500, content={"status": "error", "reason": f"Parse error: {e}"})

    if not invoices:
        return JSONResponse(status_code=400, content={"status": "error", "reason": "No invoice rows found in file."})

    bad_dates = [inv for inv in invoices if not inv["date"] and inv["raw_date"]]
    if len(bad_dates) == len(invoices):
        sample = list({inv["raw_date"] for inv in bad_dates})[:5]
        return JSONResponse(status_code=422, content={
            "status": "error",
            "reason": "Could not parse any invoice dates.",
            "sample_date_values": sample,
            "tip": "Use DD-MM-YYYY or DD/MM/YYYY format in Invoice Date column.",
        })

    response = _run_import(invoices)

    if warnings:
        body = json.loads(response.body)
        body["date_warnings"] = warnings
        return JSONResponse(status_code=response.status_code, content=body)

    return response


@app.post("/check-ledgers-excel")
async def check_ledgers_excel(file: UploadFile = File(...)):
    """
    Upload Excel/CSV to check which ledgers are missing in TallyPrime.
    No entries are created. Safe to run anytime.
    """
    content  = await file.read()
    filename = file.filename or ""

    try:
        invoices, _ = extract_excel(content, filename)
    except ValueError as e:
        return JSONResponse(status_code=400, content={"status": "error", "reason": str(e)})
    except Exception as e:
        return JSONResponse(status_code=500, content={"status": "error", "reason": f"Parse error: {e}"})

    if not invoices:
        return JSONResponse(status_code=400, content={"status": "error", "reason": "No invoice rows found."})

    required_ledgers = {LEDGER_PURCHASE, LEDGER_CGST, LEDGER_SGST, LEDGER_IGST}
    for inv in invoices:
        if inv["party"]:
            required_ledgers.add(inv["party"])

    today = datetime.today()
    probe_date = f"{today.year}0401" if today.month >= 4 else f"{today.year - 1}0401"

    missing, present = [], []
    for ledger_name in sorted(required_ledgers):
        try:
            exists = probe_ledger_exists(ledger_name, probe_date)
        except RuntimeError as e:
            return JSONResponse(status_code=503, content={"status": "error", "reason": str(e)})
        (present if exists else missing).append(ledger_name)

    if missing:
        lines = [
            "Missing Ledgers - Create these in TallyPrime before importing",
            "=" * 60, "",
        ]
        for name in missing:
            if name in (LEDGER_PURCHASE, LEDGER_CGST, LEDGER_SGST, LEDGER_IGST):
                lines.append(f"  * {name}  <-- create as tax/purchase ledger")
            else:
                lines.append(f"  * {name}  <-- create as Sundry Creditor")
        lines += ["", "Once all ledgers are created, use /upload-excel to import."]
        Path(MISSING_FILE).write_text("\n".join(lines), encoding="utf-8")

    return JSONResponse(status_code=200, content={
        "status":          "ok",
        "total_required":  len(required_ledgers),
        "missing_count":   len(missing),
        "present_count":   len(present),
        "missing_ledgers": missing,
        "present_ledgers": present,
        "message": (
            f"All {len(required_ledgers)} ledgers present. Ready to import."
            if not missing else
            f"{len(missing)} ledger(s) missing. Create them in TallyPrime, then use /upload-excel."
        ),
        "missing_ledgers_file": str(Path(MISSING_FILE).resolve()) if missing else None,
    })


# ─────────────────────────────────────────────────────────────
#  DEBUG ENDPOINTS
# ─────────────────────────────────────────────────────────────

@app.get("/debug-xml", response_class=PlainTextResponse)
def debug_xml(voucher_num: str = Query(..., description="The Purchase voucher number shown in Tally, e.g. 435")):
    """
    Exports a real voucher from Tally and shows all its internal XML fields.
    Usage: http://localhost:8000/debug-xml?voucher_num=435
    """
    export_xml = f"""<ENVELOPE>
  <HEADER>
    <TALLYREQUEST>Export Data</TALLYREQUEST>
  </HEADER>
  <BODY>
    <EXPORTDATA>
      <REQUESTDESC>
        <REPORTNAME>Voucher Register</REPORTNAME>
        <STATICVARIABLES>
          <SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>
          <VOUCHERTYPENAME>Purchase</VOUCHERTYPENAME>
          <VOUCHERNUMBER>{xml_escape(voucher_num)}</VOUCHERNUMBER>
        </STATICVARIABLES>
      </REQUESTDESC>
    </EXPORTDATA>
  </BODY>
</ENVELOPE>"""
    try:
        resp = requests.post(
            TALLY_URL,
            data=export_xml.encode("utf-8"),
            headers={"Content-Type": "application/xml"},
            timeout=15,
        )
        return resp.text
    except Exception as e:
        return f"Error connecting to Tally: {e}"


@app.get("/debug-tally", response_class=PlainTextResponse)
def debug_tally():
    """Shows the last raw XML response received from TallyPrime."""
    if Path(DEBUG_FILE).exists():
        return Path(DEBUG_FILE).read_text(encoding="utf-8", errors="replace")
    return "No debug file yet."


@app.get("/debug-dates", response_class=PlainTextResponse)
def debug_dates():
    """Shows any invoice dates that could not be parsed."""
    if Path(DATE_DEBUG_FILE).exists():
        c = Path(DATE_DEBUG_FILE).read_text(encoding="utf-8", errors="replace").strip()
        return c if c else "All dates parsed successfully."
    return "No date debug file yet."


@app.get("/")
def root():
    return {
        "tool":         "GSTR-2B to TallyPrime Import — By Monil Shah",
        "json_step_1":  "POST /check-ledgers            <- find missing ledgers from GSTR-2B JSON",
        "json_step_2":  "POST /upload-gstr2b            <- import from GSTR-2B JSON",
        "excel_step_1": "POST /check-ledgers-excel      <- find missing ledgers from Excel/CSV",
        "excel_step_2": "POST /upload-excel             <- import from Excel/CSV",
        "debug_xml":    "GET  /debug-xml?voucher_num=X  <- export voucher XML from Tally",
        "docs":         "http://localhost:8000/docs",
        "debug":        "http://localhost:8000/debug-tally",
        "dates":        "http://localhost:8000/debug-dates",
    }


# ─────────────────────────────────────────────────────────────
#  ENTRY POINT
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("  GSTR-2B to TallyPrime Import Tool  (JSON + Excel/CSV)")
    print("  By Monil Shah")
    print("=" * 60)
    print()
    print("  JSON  - POST /check-ledgers        (find missing ledgers)")
    print("        - POST /upload-gstr2b         (import JSON)")
    print()
    print("  Excel - POST /check-ledgers-excel  (find missing ledgers)")
    print("        - POST /upload-excel          (import Excel/CSV)")
    print()
    print("  Open: http://localhost:8000/docs")
    print()
    uvicorn.run(app, host="0.0.0.0", port=8000)