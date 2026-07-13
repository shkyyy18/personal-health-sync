# Contributing

Small, reviewable contributions are welcome. Open an issue before adding a vendor connector so its export format, authentication model, privacy risks, and test fixtures can be agreed first.

```bash
python -m pip install -e .
python -m unittest discover -s tests -v
```

Never commit real health records, tokens, account identifiers, QR login artifacts, or vendor session files. Tests must use synthetic fixtures.
