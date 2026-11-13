# Changelog

All notable changes to GrabbyHands are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed
- Pixelate and reading QR codes in small pictures work again on Ubuntu 26.04. A Pillow
  security update there (python3-pil 12.1.1-2ubuntu1.5) broke the image call both used.

## [0.1.0] - 2026-11-11

First public release.

### Added

**Capture**
- Region, whole-screen and window capture from one overlay, with a pixel magnifier, a delay
  timer and "capture the last area again".
- Native window capture: press W, hover a window, click it. Uses the new GrabbyHands Window
  Helper GNOME Shell extension, which shares window positions and app names only.
- Identify Color: hover any pixel for its HEX, RGB and CMYK values; click for a window with a
  copy button per value.
- Global shortcuts through GNOME (Print Screen, Ctrl+Shift+Print Screen, Super+Shift+C,
  Super+Shift+T); GrabbyHands stays ready in the background and can start at login.
- A small "Allow Screenshots" window asks for GNOME's screenshot permission the first time.
- A Getting Started guide on first launch: what GrabbyHands does, its shortcuts and whether
  they are active, and one button that gives it the Print Screen key on GNOME (GNOME's own
  tool moves to Ctrl+Print Screen; Undo is right there).
- A Keyboard Shortcuts list (Ctrl+? or F1), and hints on the capture screen that only name
  keys that work right now.
- Keys work on Russian, Greek, Arabic, Hebrew and other non-Latin keyboard layouts.
- Optional: the screenshot button in GNOME's system menu can start GrabbyHands instead of
  GNOME's own tool (Settings → General; needs the Window Helper). Off by default, undone with
  the same switch, and the button falls back to GNOME's tool if GrabbyHands can't be started.

**After the capture**
- Action bar: Copy, Save, Pin, Edit, Redact, Text, QR Code and Save As.
- Every capture can be saved automatically to `Pictures/GrabbyHands`.
- History: thumbnails of every capture; reopen, re-edit, delete one (with Undo) or clear
  everything (saved files optionally go to the Trash).
- Pinned screenshots that float above other windows.
- Save As with PNG, JPEG or WebP, quality and size.

**Editor**
- Arrow, line, box, circle, pen, highlighter, text, callout, numbered steps, stickers, crop,
  spotlight and magnify.
- Blur, pixelate and black-out with a gradual 0–100% strength.
- An eraser that removes only what you drew, never the screenshot, plus Erase All.
- Select, move, resize, recolor and delete anything you drew; undo and redo; zoom and pan.
- Markup stays editable in History; closing the editor updates the saved file.
- Auto-Redact (Ctrl+Shift+R): finds emails, phone numbers, card numbers (Luhn-checked), ID
  numbers, IPv4 addresses and API keys or tokens with on-device text recognition and covers
  them in one undoable step.

**Text and codes**
- Copy Text shows the capture with its recognized words: drag across or click the words you
  want (double-click for a line), or use the plain-text tab. Nothing is copied until you choose.
- Reads QR codes and barcodes.

**Look and settings**
- Color schemes: Violet (default), Ocean, Mint, Sunset, Rose, Amber, Graphite or the GNOME
  accent color, in light, dark or system style. Everything follows the scheme, including the
  capture overlay.
- Labeled tool tiles in framed groups in the editor, on the capture toolbar and on the action
  bar; keys are shown in tooltips.
- Every color scheme meets WCAG contrast in light and dark, and the selection frame shows on
  any background.
- Settings: what Enter does, the magnifier, the delay, the color format, start at login, a
  shortcut overview, the save folder, the file name pattern with a live example, format and
  quality, history size, the text-recognition language and the Window Helper status.

**Privacy and safety**
- No network access at all. Captures, recognized text and decoded codes never appear in logs.
- History lives in a folder only you can read; Clear History removes every capture from disk.
- Redacted areas are destroyed in every saved or copied picture, and a damaged capture can
  never come out with its redactions missing.
- Saving is all-or-nothing: a full disk can't leave a half-written picture. Quitting or
  logging out with the editor open keeps your markup.

**Packages**
- Flatpak bundle on the GNOME 50 runtime with Pillow, Tesseract (English) and zxing-cpp
  included, and no network permission.
- `.deb` package for Ubuntu 24.04 and newer and Debian 13.
- The Window Helper extension as a separate zip for Flatpak users.
