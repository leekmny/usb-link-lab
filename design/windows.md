# Windows desktop design

The light theme is the first-run default. Neutral surfaces distinguish navigation from the working area. Dark teal identifies primary actions, with amber for visible plaintext or HMAC content, red for failures, and green for verified completion. `apps/windows/usblinklab/theme.py` is the palette source for both themes.

The device rail keeps USB selection and setup recovery together. The native app groups attachment actions separately from protection and sending. The dedicated details pane presents confidentiality, authenticity, status and local previews. At narrower widths the same detail controls open in a compact dialog. Current selection, keyboard focus and button states remain visible. Large text and English are verified in addition to the Korean first-run screen.

The application uses Segoe UI Variable when available, with Segoe UI fallback. `apps/windows/usblinklab/icons.py` draws original monoline glyphs with Qt. The first-run list stays empty. A separate native window capture script labels synthetic transfer examples.

The layout, type hierarchy and command placement draw on [Microsoft Windows content layout](https://learn.microsoft.com/en-us/windows/apps/design/basics/content-basics), [Windows typography](https://learn.microsoft.com/en-us/windows/apps/design/signature-experiences/typography) and [Windows commanding basics](https://learn.microsoft.com/en-us/windows/apps/design/basics/commanding-basics).
