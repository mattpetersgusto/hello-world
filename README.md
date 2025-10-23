# Local Tax Elections Importer

CLI and library to resolve local taxes from Symmetry based on home address and import local tax elections into Workday.

## Setup

1. Create and activate a virtualenv (optional)
2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Create a `.env` from `.env.example` and fill in credentials.
4. Copy `mappings/local_tax_mappings.example.json` to `mappings/local_tax_mappings.json` and adapt mappings to your tenant codes.

## Usage

Single worker dry run:

```bash
python -m tax_importer.cli single \
  --worker-id W12345 \
  --street "123 Main St" \
  --city "Philadelphia" \
  --state PA \
  --postal-code 19103 \
  --dry-run
```

Batch from CSV (columns: worker_id,street,city,state,postal_code,country):

```bash
python -m tax_importer.cli batch --csv-path employees.csv --dry-run
```

## Notes
- Symmetry endpoint paths and response fields vary by subscription; adjust `symmetry_client.py` if needed.
- Workday SOAP operation and payload structure vary by tenant and version; update `workday_client.py` with your WSDL types and operation names.
