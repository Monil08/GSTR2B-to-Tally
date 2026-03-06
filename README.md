# 📊 GSTR-2B → TallyPrime Import Tool

**Automatically import B2B purchase invoices from GSTR-2B into TallyPrime — no manual data entry.**

Supports GSTR-2B JSON (downloaded from GST Portal) and Excel/CSV files. Runs locally on your machine and talks directly to TallyPrime via its XML gateway.

> Built by **Monil Shah**

---

## ✨ Features

- ✅ Import from **GSTR-2B JSON** (direct download from GST Portal)
- ✅ Import from **Excel (.xlsx / .xls) or CSV** files
- ✅ **Ledger check** before importing — no surprise errors mid-import
- ✅ Generates `missing_ledgers.txt` listing every party ledger to create in Tally
- ✅ Handles both **intra-state** (CGST + SGST) and **inter-state** (IGST) invoices
- ✅ Flexible column name matching for Excel — no rigid template required
- ✅ Supports multiple date formats automatically
- ✅ Clean browser-based UI at `http://localhost:8000/docs`
- ✅ Debug tools to diagnose Tally errors

---

## 🖥️ Demo

```
POST /check-ledgers        ← check missing ledgers from GSTR-2B JSON (safe, no entries)
POST /upload-gstr2b        ← import from GSTR-2B JSON
POST /check-ledgers-excel  ← check missing ledgers from Excel/CSV (safe, no entries)
POST /upload-excel         ← import from Excel/CSV
```

Open `http://localhost:8000/docs` in your browser after starting the tool.

---

## ⚙️ Requirements

- Python 3.9+
- TallyPrime (any version) running on the same machine
- Windows (TallyPrime is Windows-only)

---

## 🚀 Quick Start

### 1. Clone the repository

```bash
git clone https://github.com/Monil08/gstr2b-to-tally.git
cd gstr2b-to-tally
```

### 2. Install dependencies

```bash
pip install fastapi uvicorn requests python-multipart openpyxl pandas
```

### 3. Configure TallyPrime

Open TallyPrime and go to:

**F1 → Settings → Connectivity → Client/Server Configuration**

| Setting | Value |
|---------|-------|
| TallyPrime acts as | `Server` |
| Enable ODBC | `Yes` |
| Port Number | `9000` |

Save and restart TallyPrime if prompted.

### 4. Run the tool

```bash
python gstr2b_to_tally.py
```

Then open **http://localhost:8000/docs** in your browser.

---

## 📋 How to Use

### Recommended workflow (both JSON and Excel)

```
Step 1 → Run "Check Ledgers"   (safe — no entries created in Tally)
Step 2 → Create any missing ledgers in TallyPrime as Sundry Creditors
Step 3 → Run "Import"
```

### Excel / CSV Column Format

Your file needs these columns (flexible naming — exact spelling not required):

| Required Column | Accepted Header Names |
|----------------|----------------------|
| Party Name | Party Name, Supplier, Vendor, Trade Name |
| Invoice Number | Invoice Number, Invoice No, Bill No |
| Invoice Date | Invoice Date, Inv Date, Bill Date, Date |
| Taxable Value | Taxable Value, Taxable Amount, Basic Value |

| Optional Column | Accepted Header Names |
|----------------|----------------------|
| CGST | CGST, CGST Amount, Central GST |
| SGST | SGST, SGST Amount, State GST |
| IGST | IGST, IGST Amount, Integrated GST |

Date format: `DD-MM-YYYY` or `DD/MM/YYYY` recommended.

---

## 📁 Repository Structure

```
gstr2b-to-tally/
│
├── gstr2b_to_tally.py       ← main application (run this)
├── generate_sample.py       ← generates a sample Excel file for testing
├── README.md                ← this file
├── INSTRUCTIONS.md          ← detailed instructions for non-technical users
└── requirements.txt         ← Python dependencies
```

---

## 📦 requirements.txt

```
fastapi
uvicorn
requests
python-multipart
openpyxl
pandas
```

---

## 🔧 Debug Tools

| URL | Purpose |
|-----|---------|
| `/debug-tally` | Last raw XML response from TallyPrime |
| `/debug-dates` | Invoice dates that failed to parse |
| `/debug-xml?voucher_num=X` | Export XML of a specific voucher from Tally |

---

## ❗ Common Errors

| Error | Fix |
|-------|-----|
| Cannot connect to TallyPrime | Enable XML gateway in Tally: F1 → Settings → Connectivity → Port 9000 |
| Unknown Request | XML gateway not enabled — see Tally configuration above |
| Missing ledger | Run Check Ledgers first, create missing parties as Sundry Creditors |
| CREATED=0 | Press F2 in TallyPrime to set the correct financial year |
| Unrecognised date | Use DD-MM-YYYY format in your Excel/CSV file |

---

## 📄 License

MIT License — free to use, modify, and distribute.

---

*Made with AI by Monil Shah*
