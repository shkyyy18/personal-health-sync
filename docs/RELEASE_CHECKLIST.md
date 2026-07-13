# Release checklist

- [ ] Version and changelog agree.
- [ ] `python -m unittest discover -s tests -v` passes.
- [ ] `python -m compileall -q healthsync tests` passes.
- [ ] The synthetic demo generates a store and portable dashboard.
- [ ] Connector status in README matches actual support.
- [ ] No health records, tokens, sessions, QR files, account IDs, or local dashboards are staged.
- [ ] Windows and Linux GitHub Actions jobs pass.
- [ ] Tag and GitHub Release use the same version.
