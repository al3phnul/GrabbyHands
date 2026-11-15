# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 al3phnul and contributors
"""Smart Edit: describe an edit in plain words and Claude makes it with the editor's own tools.

Nothing here runs code it receives. Claude can only call the tools defined in tools.py, and
every call is checked the same way a saved document is checked before it is drawn.
"""
