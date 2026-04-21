# Design tokens

`src/trace_to_eval/web/tokens.css` is the single source of colour, spacing, radius, motion,
and type values for the UI. Components reference token names only; no component declares a
raw colour or spacing value. This file records the measured contrast for every token pair
that carries meaning, so a token change is a checked change rather than a guess.

## Theme strategy

Light is the default; dark follows `prefers-color-scheme`. Both themes expose the same token
names, so no component branches on theme. `prefers-reduced-motion` sets both motion tokens
to `0ms`.

## Measured contrast

Ratios computed with the WCAG 2.x relative-luminance formula against the token values in
`tokens.css`. Text pairs are held to 4.5:1 (AA normal text); control boundaries, focus rings,
and state indicators to 3:1 (1.4.11 non-text contrast).

| Use | Pair | Light / dark value | Light | Dark | Required | Result |
| --- | --- | --- | --- | --- | --- | --- |
| Body text on cards | `--color-ink` on `--color-surface` | #1f1d19 / #f0ebe2 | 16.55:1 | 14.14:1 | 4.5:1 | pass |
| Body text on the page | `--color-ink` on `--color-canvas` | #1f1d19 / #f0ebe2 | 15.31:1 | 15.35:1 | 4.5:1 | pass |
| Body text on sunken rows | `--color-ink` on `--color-surface-sunken` | #1f1d19 / #f0ebe2 | 14.27:1 | 12.65:1 | 4.5:1 | pass |
| Secondary text and metadata | `--color-ink-muted` on `--color-surface` | #6b655b / #b0a89b | 5.68:1 | 7.13:1 | 4.5:1 | pass |
| Secondary text on the page | `--color-ink-muted` on `--color-canvas` | #6b655b / #b0a89b | 5.25:1 | 7.74:1 | 4.5:1 | pass |
| Links and eyebrow labels | `--color-accent` on `--color-surface` | #8a4b16 / #e0a060 | 6.67:1 | 7.49:1 | 4.5:1 | pass |
| Links on the page | `--color-accent` on `--color-canvas` | #8a4b16 / #e0a060 | 6.17:1 | 8.13:1 | 4.5:1 | pass |
| Primary button label | `--color-accent-ink` on `--color-accent` | #fffaf2 / #241708 | 6.52:1 | 7.80:1 | 4.5:1 | pass |
| Pass pill | `--color-success` on `--color-success-surface` | #1f6b43 / #7bc79a | 5.68:1 | 6.83:1 | 4.5:1 | pass |
| Fail pill and destructive label | `--color-danger` on `--color-danger-surface` | #a32a1f / #f09388 | 6.15:1 | 6.63:1 | 4.5:1 | pass |
| Warning pill | `--color-warning` on `--color-warning-surface` | #8a5a00 / #e8bd6b | 5.28:1 | 7.98:1 | 4.5:1 | pass |
| Info pill | `--color-info` on `--color-info-surface` | #24557d / #8ec5e8 | 6.80:1 | 7.82:1 | 4.5:1 | pass |
| Control border | `--color-border-strong` on `--color-surface` | #857c70 / #7d766a | 4.04:1 | 3.73:1 | 3:1 | pass |
| Control border on input background | `--color-border-strong` on `--color-canvas` | #857c70 / #7d766a | 3.74:1 | 4.05:1 | 3:1 | pass |
| Focus ring | `--color-focus` on `--color-surface` | #1e5aa8 / #8db9f0 | 6.70:1 | 8.26:1 | 3:1 | pass |
| Focus ring on the page | `--color-focus` on `--color-canvas` | #1e5aa8 / #8db9f0 | 6.20:1 | 8.96:1 | 3:1 | pass |

`--color-border` (1.48:1 light, 1.45:1 dark) is a decorative card and table rule. It is
exempt from 1.4.11 because no information depends on it: cards are also separated by
`--color-surface`, and tables by cell padding and header weight. Control boundaries use
`--color-border-strong` instead, which meets 3:1 in both themes.

`--color-border-strong` was `#b8b0a1` (2.12:1) in light and `#575047` (2.11:1) in dark.
Inputs sit on `--color-canvas`, which is 1.04:1 against `--color-surface`, so the border was
the only indicator of the control boundary and failed 1.4.11. Both values were darkened to
the ratios above.

## Adding or changing a token

1. Change the value in `tokens.css`, not in a component.
2. Recompute the ratio for every pair the token appears in and update this table.
3. Re-run the browser gates in `docs/development.md`; a failing ratio is a release blocker,
   not a note.
