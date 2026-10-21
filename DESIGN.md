# Design tokens

Taken from collegewes.com (Elementor globals, 2026-10-21) so the library feels like part of the church's site.
One blue family only. No purple, no decorative gradients.

| Token | Value | Why |
|---|---|---|
| `--sky` | `#0099CC` | Their primary. Graphic accents only: progress bar, focus ring, "now" glow. |
| `--sky-ink` | `#007CA6` | Their primary darkened so white text passes AA (4.7:1). Top bar and main buttons. |
| `--blue` | `#005A9E` | Their heading/link blue (7.1:1). Links, time pills. |
| `--deep` | `#00345C` | Their deepest blue (12.8:1). Headings, ticker, selected chips, footer. |
| `--green` | `#70BF44` | Their secondary. Scripture tags, best match, ticker underline. Deep-blue text on it, never white. |
| `--ink` / `--muted` / `--faint` | `#1F2A33` / `#5B6670` / `#677079` | Text. All pass AA on white and on `--soft`. |
| `--soft` / `--line` / `--tint` | `#F4F7FA` / `#E3E8ED` / `#E8F5FB` | Results column, borders, selected moment. |
| `--mark` | `#FFF0A6` | Highlighted search words. |
| Font | Open Sans | Their site font. Source Serif 4 for transcript text, so preaching reads like a page. |
| Radius | 50px pills, 14px cards, 18px player and big cards | |
| Shadows | `--shadow-sm/md/lg`, tinted with `--deep` | Soft lift instead of hard borders. |

## Motion
Clicks and toggles 0.12–0.25s ease-out. Pane slide and sermon swap 0.3–0.45s. First load rises in once (0.6s).
The ticker eases toward the spoken word every frame instead of stepping. Everything collapses to instant under
`prefers-reduced-motion`.

## Layout
Search rail | results column (slides out after a search) | sermon. The video is capped so the moments card
(numbered timeline + moment cards) shows without scrolling; "Follow along" (ticker + full transcript) comes next.
Under 860px it becomes one pane at a time with a back link. Checked at 390, 768, 1280, 1920, 2560, 3440px with axe
(WCAG 2.2 AA): clean.
