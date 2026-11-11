# Security policy

## Supported versions

Only the latest release receives fixes.

## Reporting a vulnerability

Please **don't open a public issue**. Email
**[grabbyhands.bulb292@passinbox.com](mailto:grabbyhands.bulb292@passinbox.com)** instead.

Include what you found, how to reproduce it, and what an attacker could do with it. You'll get
a reply as soon as the maintainer can manage. This is a volunteer project, so please allow
reasonable time for a fix before disclosing publicly.

## Scope notes

GrabbyHands handles screenshots, which can contain sensitive information. Things we especially
care about:

- Redaction (blur, pixelate, black-out, Auto-Redact) that can be reversed in a copied or
  exported image
- Captures, recognized text or decoded QR content leaking to logs, other apps or the network
- Files written outside the save folder or the app's data directory
- The Window Helper GNOME Shell extension sharing more than window positions and app names
  (it must never expose window titles or contents), or changing more of GNOME's interface
  than the one screenshot button the user asked it to
- The Flatpak asking for more than it needs. Its permissions are listed, with reasons, in
  `build-aux/flatpak/io.github.al3phnul.GrabbyHands.yml`

Auto-Redact is a helper, not a guarantee: it can miss things, and GrabbyHands says so. A missed
detection is a bug worth reporting as a normal issue (with made-up example data), not a
vulnerability.

## Verifying releases

Release files are built by GitHub Actions from this repository. Every release has a
`SHA256SUMS` file and build provenance attestations:

```sh
sha256sum -c SHA256SUMS --ignore-missing
gh attestation verify <file> --repo al3phnul/GrabbyHands
```
