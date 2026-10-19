# Ribbon alignment and field style (2026-10-19)

Maintainer report: in the Image panel ribbon, ROIs tab, the "Groups" caption sits lower than its neighbours
and its label icon is off; in the Background tab the captions above the number fields sit lower. Goal:
captions on one line, icons one size, fields as tall as icons, fewer kinds of control.

All numbers below were measured offscreen with real Segoe UI loaded and the app stylesheet applied
(geometry and pixel ink only, no screenshots). Positions are from the top of the ribbon content row.

## What was wrong

| Where | Measured | Cause |
|---|---|---|
| ROIs > Groups | caption y 34 (others 26); buttons: Group at y 0, label menu at y 4 | `labeled_icon_group` only centred its content. `GroupControls` is a plain `QWidget` row, so the group's vertical layout stretched it from 24 to 32 px (the ribbon row is 46, the group needs 38, the 8 spare px went to the one content that could grow). Every other content is a fixed-size button, so they could not grow and the spare px went to their caption box instead. Fixed-size buttons inside the 32 px row were centred, hence the label button 4 px low. |
| Background > Sigma, Bin, Excl. dilatation | fields 26 px high, captions at y 28 | A framed field is content + padding + border: the old rule asked for 20 px content, + 2x2 padding + 2x1 border = 26. |
| Mask > Auto mask | field 25 px, caption at y 27 | Same family: nothing fixed the field's height. |
| Group-label icon | visible part 12 x 16 px; other Tabler icons 16 x 16 | `label_menu_icon` drew the label at 62 % and the three dots only across 0.26-0.74 of the canvas. |

## What changed (repo: apps/LSPRi/eva, branch `rewrite`)

- `panels/ribbon_group.py` `labeled_icon_group`: content and caption are both pinned to the top at their
  natural height, the spare room is left empty below. A caption now sits `content height + 2 px` down, whatever
  the content is. Caption text did not move on any tab (measured: same ink rows for a 20 px and a 12 px box).
- New `panels/ribbon_fields.py`: `style_ribbon_number_field` (frameless, 24 px = `BUTTON_SIZE`, no arrows,
  right-aligned, width from the widest text) and `style_ribbon_combo`. It is the Mask tab's old private
  `_narrow_spinbox`, moved and given a fixed height; Mask spin boxes keep exactly their old widths.
- `panels/image/background_tab.py`: Sigma and Excl. dilatation use the number-field style (widest text
  "99 px" / "100 px"; Sigma was "2000 px" at first, narrowed to two digits on request, so a 3-digit sigma clips); Bin uses the combo style. The "binning was lowered"
  cue used to fade in a coloured *border*, which would add 2 px of height to a frameless field and, when it
  ended, `setStyleSheet("")` would have brought the framed look back. It is now a background tint and puts the
  field's own style back.
- `panels/image/group_label_menu.py` `label_menu_icon`: label at 72 %, dots across 0.16-0.84; visible part is
  now exactly 30 x 30 of 36 px, like the Tabler icons.
- `panels/image/mask_edit_panels.py`, `mask_scan_panels.py`: use the shared helper instead of the private one.

Result: every caption on the ROIs, Background, Mask, View and Chromatic tabs is at y 26; every field is 24 px.

## Width arithmetic (corrected)

In a Qt style sheet `min-width` / `max-width` are the width of the *content*; padding is added outside. A
field is therefore widest text + 14 px (e.g. 54 px for "2000 px" = 40 px of text), not +10 as the old
`_narrow_spinbox` comment said. Left as it is so the Mask widths the maintainer approved on 2026-10-16 do not change.

## Not changed, for the maintainer to decide

1. **Done later on 2026-10-19 (maintainer noticed): Image tools tab captions were at y 30, all other tabs at
   y 26.** `TransformsSection` had a 4 px margin on every side (icons and captions 4 px lower and 4 px further
   right than elsewhere), and as the tallest tab (38 + 8 = 46) it set the ribbon height, leaving every other tab
   8 px empty under its captions. The margin is removed (`transforms_settings.py` `ROW_HEIGHT`, layout margins):
   all six tabs have captions at y 26 and the ribbon is 38 px, 8 px shorter, so the image gets 8 px more height.
   To get the old height back, add 8 px to `_ROW_HEIGHT` in `tool_ribbon.py`.
2. **Icon drawings that are not the standard 16 x 16 ink** (button boxes are all 24 x 24, the two 20 x 24 are the
   +/- pairs): ROI display menu 18 x 18, spot icon (Background "ignore ROI areas") 10 x 10, shape circle 14 x 14,
   crop 12 x 12, scope "persistent" 14 x 17. Most are the glyph's natural shape (a car is wide and low); the
   spot / ring / display family is deliberately drawn together and reused on the View tab, so it was left alone.
3. **Other number-field styles still in use, none of them in a ribbon row:** `group_label_menu.py`
   (`_SPIN_STYLE`, own width rule, inside the group-label pop-up strip), the Chromatic and Array settings
   pop-ups (framed spin boxes in forms: a different job, a form needs labels and frames to read), and the two
   floating boxes over the image, `measure_controls.py` (framed) and `crop_size_controls.py` (frameless, own
   style). Only the group-label strip is a candidate for `style_ribbon_number_field`.
4. **Apply buttons of other sizes**, both in the floating boxes over the image: `crop_size_controls.py`
   22 x 22, `measure_controls.py` 26 x 26 (ribbon icon buttons: 24 x 24).
5. **Drop-downs:** the Bin combo is the only `QComboBox` left in the ribbon (the group-label pop-up already
   replaced its drop-downs by icon pickers, test "no drop-downs"). Replacing it by a `ChoicePicker` of 1/2/4/8
   would leave two control kinds (icon buttons and numbers) in the ribbon.

## How to check by eye

Open the ROIs tab (Groups caption level with Array; the label button level with Group) and the Background tab
(Sigma / Bin / Excl. dilatation captions level with Removal and View). Tool: none needed; the probe scripts were
throw-away.
