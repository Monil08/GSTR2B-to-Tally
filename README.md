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
