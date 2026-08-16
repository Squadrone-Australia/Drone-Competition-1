# Colour calibration for beginners

The drone finds fires by looking for a red circle in its camera picture. A red marker can look
bright scarlet outdoors, dark brown in a shadow, or pale pink under strong indoor lights. Colour
calibration teaches the program what **red looks like in the current room**.

You do not need to know OpenCV, image processing, or programming to use the calibration panel.

## Before you begin

- Land the drone and stop any running mission. Calibration is disabled while a mission is active.
- Put the real competition marker where the drone can see it clearly.
- Use the same room lights that will be used during the activity.
- Let the camera run for a few seconds so its automatic exposure has settled.
- Stand at a representative distance. Do not fill the entire picture with the marker.

Calibration changes what the camera recognises; it does not move the drone.

## Recommended calibration procedure

1. Click **Tune colour** above the drone-camera picture.
2. Click **Capture current frame** if the displayed frame does not show the marker clearly.
3. Drag a box **inside the red marker**. Avoid its edge, the surrounding wall, bright glare, and
   deep shadow where possible.
4. Wait for the suggested settings and mask preview.
5. Inspect the preview:
   - the marker should be highlighted across most of its surface;
   - the floor, walls, people, and unrelated objects should remain dark;
   - a small missing highlight or shadow is usually harmless;
   - large highlighted background areas are a warning sign.
6. If the preview looks good, click **Apply to detector**.
7. Move the marker around the camera view and try it at the expected competition distances. Check
   that the green detection circle remains stable.
8. Give the settings a useful name, such as `sports hall morning`, and click **Save locally**.

Automatic selection is the normal workflow. The sliders are available for difficult lighting and
small corrections; they are not values that every operator must learn.

## What the camera is deciding

The camera describes each pixel using three properties:

| Plain-language question | Name in the panel | What it means |
|---|---|---|
| Which colour is it? | Hue | Red, orange, yellow, green, blue, purple, and back to red |
| How colourful is it? | Saturation | Vivid colour versus pale, grey, or white |
| How light is it? | Brightness | Bright versus dark |

A pixel enters the highlighted mask only when it passes **all three** checks. After that, the
program still checks whether the highlighted area is large enough and circular enough. Seeing a
few red pixels does not automatically count as finding a fire.

## Hue bands: which colours are accepted?

Hue is a colour wheel. The OpenCV numbers used by this program run from `0` to `180`. Approximate
positions are:

| Colour | Approximate hue |
|---|---:|
| Red | `0` or `180` |
| Yellow | `30` |
| Green | `60` |
| Blue | `120` |

Red appears at **both ends** because `0` and `180` meet at the same point on the wheel. This is why
the panel has two hue bands. A common starting point is:

```text
Hue band 1: 0 to 10
Hue band 2: 170 to 180
```

The start and end values define which part of the wheel each band accepts:

- Making a band wider accepts more shades. This can help when different parts of the marker look
  slightly orange or purple, but it can also accept unrelated objects.
- Making a band narrower rejects nearby colours. It can reduce false detections, but may lose part
  of the marker as lighting changes.
- **Hue band 1 end** is the setting most likely to include or exclude orange-red shades.
- **Hue band 2 start** is the setting most likely to include or exclude purple-red shades.

Use the automatic suggestion first. Change a hue endpoint only when the preview shows a clear
reason.

## Minimum saturation: how colourful must it be?

Saturation runs from `0` to `255`:

- `0` means there is almost no colour, such as white or grey.
- `255` means a very strong, vivid colour.

The minimum rejects pixels below the selected strength.

- **Raise minimum saturation** when white glare, grey walls, or washed-out areas are highlighted.
- **Lower minimum saturation** when pale or strongly lit parts of the red marker disappear.

Lowering it too far can make the mask noisy because nearly grey pixels do not have a reliable
colour.

## Minimum brightness: how light must it be?

Brightness runs from `0` to `255`:

- `0` is black.
- `255` is as bright as the camera can record.

The minimum rejects pixels darker than the selected value.

- **Lower minimum brightness** when the marker disappears in shadow.
- **Raise minimum brightness** when dark red, brown, or very dark background objects are accepted.

Lowering it too far can include noisy pixels from dark parts of the picture.

## Quick problem guide

| What you see | First adjustment to try |
|---|---|
| The entire marker is dark in the preview | Capture again and select a clean area inside it |
| Only the shadowed part is missing | Lower minimum brightness a little |
| Only pale or shiny parts are missing | Lower minimum saturation a little |
| White or grey areas are highlighted | Raise minimum saturation |
| Orange objects are highlighted | Reduce the end of hue band 1 |
| Purple or pink objects are highlighted | Increase the start of hue band 2 |
| Dark red or brown objects are highlighted | Raise minimum brightness, then check saturation |
| The mask looks good but no circle is detected | Move closer and make sure the whole round marker is visible |
| Detection works in one direction but not another | Test the marker under the different lights and shadows in the room |

Make small adjustments and wait for the preview after each one. Large changes can solve one view
while breaking several others.

## Understanding the preview

The preview deliberately dims rejected pixels and highlights accepted pixels. It answers “which
pixels match the configured colour?” It does not mean every highlighted patch is a detected fire.

A good preview has:

- one mostly solid highlighted marker;
- little or no highlighted background;
- enough of the marker edge visible for the program to judge its circular shape.

Small isolated specks are normally removed by the detector. Large background patches are more
important because they may be mistaken for targets or merge with the marker.

## Saving and restoring settings

- **Save locally** stores a named profile in the current browser on the current computer. Use names
  that identify the venue and lighting, such as `classroom lights on` or `hall afternoon`.
- **Load** places a saved profile into the controls and shows its preview. It does not become active
  until **Apply to detector** is clicked.
- **Download TOML** creates a small configuration file that can be copied to another computer or
  kept with event records.
- **Restore startup settings** returns to the values that were active when the application began.

Downloaded settings can be used when starting the application:

```powershell
venv\Scripts\python -m comp1 --vision-config vision_config.toml
```

## Technical reference

OpenCV calls this colour representation **HSV**, meaning Hue, Saturation, and Value. The panel uses
the friendlier word “brightness” for Value. Internally, the test for each pixel is:

```text
hue is inside band 1 OR hue is inside band 2
AND saturation is at least the selected minimum
AND brightness is at least the selected minimum
```

The program then removes small specks and checks area and circularity. Those shape checks are kept
separate from colour calibration so an operator cannot accidentally change flight-distance or
target-shape safety settings while tuning the room colour.
