# Design tokens

Taken from collegewes.com (2026-10-21) so the library feels like part of the church's site.

| Token | Value | Why |
|---|---|---|
| `--cwc-sky` | `#007CA6` | Top bar and main buttons. Their `#0099CC`, darkened just enough for white text to pass WCAG AA (4.7:1). |
| `--cwc-blue` | `#005A9E` | Headings and links, as on their site. |
| `--cwc-navy` | `#1A3368` | Search rail and karaoke ticker: calm, high contrast for white text (12:1). |
| `--cwc-green` | `#70BF44` | Scripture tags, the "spoken now" glow, best match. Navy text on it (5.4:1), never white. |
| `--soft` / `--line` | `#F3F6F9` / `#E2E5E9` | Results column and borders. |
| `--mark` | `#FFF1A8` | Highlighted search words. |
| Font | Open Sans | Their site font. Source Serif 4 for transcript text, so preaching reads like a page, not a UI. |
| Radius | 50px pills, 10px cards | Their pill buttons; cards stay quieter. |

## Motion
Clicks and toggles 0.12–0.25s ease-out. Pane slide and sermon swap 0.3–0.45s. First load fades in once (0.6s).
The ticker eases toward the spoken word every frame instead of stepping. Everything collapses to instant under
`prefers-reduced-motion`.

## Layout
Search rail | results column (slides out after a search) | sermon. Under 860px it becomes one pane at a time with a
back link. Checked at 390, 768, 1280, 1920, 2560, 3440px.
