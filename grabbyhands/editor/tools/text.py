# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 al3phnul and contributors
"""Text tool: click to type, click a text to edit it."""

from __future__ import annotations

from grabbyhands.editor.document import CalloutItem, TextItem
from grabbyhands.editor.tools.base import HandleAwareTool


class TextTool(HandleAwareTool):
    name = "text"
    cursor = "text"

    def start(self, x, y, state):
        hit = self.canvas.doc.item_at(x, y, self.canvas.image_tolerance())
        if isinstance(hit, TextItem | CalloutItem):
            self.canvas.select(hit)
            self.canvas.begin_text_edit(hit, new=False)
            return
        s = self.style
        item = TextItem(x=x, y=y - s.font_size * 0.6, text="", size=s.font_size, color=s.color)
        self.canvas.begin_text_edit(item, new=True)
