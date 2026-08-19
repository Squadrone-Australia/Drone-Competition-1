# UI design system

The browser UI is one dark theme, one accent, one radius scale. Everything below is enforced by
convention, not by a build step, so it has to be read before adding a panel.

## Brand

The accent is the Squadrone orange from the logo: **`#f7941d`** (the logo's own pixels sample at
`#f89026`; `#f7941d` is what the simulator was already painting the drone arms with, so the two
agree). It is defined once, as `--brand` in `comp1/frontend/style.css`, and appears as:

- filled primary actions (Run, "Apply to detector", "Find marker for me"), always with
  `--brand-ink` text, never white on orange
- the active state of every toggle: view-mode chips, arena chips, code-inspector tabs
- panel accents: block-description label, calibration step captions, slider tracks
- the found-targets chip in the app bar
- the Mission block category (Blockly hue 32) and the drone's arms in the 3D view

The logo lives at `comp1/frontend/assets/squadrone-logo.webp` (520 px wide, downscaled from the
2560 px original so the app bar does not pull 75 kB for a 30 px lockup). It is also the favicon.

**Orange is the only accent.** Green, amber and red are *semantic* and never stand in for it:
green = connected / mission success, amber = warning, red = the target, the live-camera dot
and EMERGENCY STOP. Cyan survives in exactly two places, both of which are annotations drawn *over
imagery* rather than UI chrome, where orange would sit on top of a red marker: the calibration
region-of-interest box (`calibration.js`) and the flight trail plus camera frustum in the 3D view
(`scene3d.js`).

## Theme lock

The whole page is dark. That is why `app.js` builds a Blockly theme (`squadroneTheme()`) instead of
leaving the workspace on Blockly's white default: the workspace is the largest surface in the app,
and a bright slab beside a dark one reads as two applications. The theme call is wrapped in
`try/catch` and returns `undefined` on failure, because a Blockly build without `defineTheme` must
still inject or nothing runs at all.

Blockly's plain-DOM chrome (toolbox rows, flyout background) cannot be themed from JS and is styled
in `style.css` under the `.blockly*` selectors.

Block category hues moved off two collisions: sensing was hue 0, the same red as a target in
the camera and on the plan, and mission was purple. Mission is now the brand hue.

## Tokens

All colour, radius, font and focus values are CSS custom properties on `:root`. Add a token rather
than a literal; a hex in a rule body is a bug waiting for the next re-theme. The radius scale is
`--r-sm` 4 / `--r-md` 8 / `--r-lg` 12: chips and inputs small, buttons and panels medium, dialog
large.

## Panel pattern

Every panel in the right-hand column has the same header: a `.bar` with a `.bar-title` in small
uppercase and its chip buttons pushed right. The 3D view, the arena and the console all use it, so a
new panel should too rather than inventing a heading.

## Rules that are easy to break

- **No em-dashes in user-visible strings.** They read as machine-written. Use a comma, a colon or a
  second sentence. Code comments are exempt.
- **Every state gets a design**, not just the happy one: the console has an empty state, the mask
  preview's alt text is styled because it is visible before the first refresh, disabled buttons drop
  to 45% rather than disappearing.
- **`* { margin: 0 }` kills `<dialog>`'s auto margins**, which is what centres it. `#vision-dialog`
  restores `margin: auto` explicitly.
- **Contrast is checked on fills, not just text**: orange fills carry `--brand-ink` (near-black),
  which is why Run is legible and a white-on-orange button would not be.
- Motion is limited to 120 ms hover/press feedback and is disabled wholesale under
  `prefers-reduced-motion`. Nothing on this page animates for decoration.
