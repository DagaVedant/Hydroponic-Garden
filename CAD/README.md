# cad

i modelled everything on onshape and have the .STEP export here

## parts

| # | name | status | file |
|---|---|---|---|
| 00 | 50.6 test socket | done | `50.6 Test Socket.step` |
| 01 | grow module | done | `Grow Module.step` |
| 02 | root grate | done | `Root Grate.step` |
| 03 | water spreader | done | `Water Spreader.step` |
| 04 | tower lid | done | `Tower Lid.step` |
| 05 | lid cap | done | `Lid Cap.step` |
| 06 | drain base | done | `Drain Base.step` |
| 07 | tank lid plate | done | `Tank Lid Plate.step` |

05 was going to be a jet splitter. the pump doesnt make a jet, so the lid does the
distributing and 05 is just a cap now.

there was going to be a level sensor riser. a post above the plate cannot see the
water, because the plate is the lid. the fix was the fill line: MAX_FILL_DEPTH is
now 165 so the flush sensor clears its 200mm blind zone, and the sensor sits in a
pod on the plate instead of on a riser.

## sizes

| part | volume | mass at 15% infill |
|---|---|---|
| grow module | 656.61 cm3 | ~470 g |
| root grate | 50.51 cm3 | ~40 g |
| water spreader | 36.72 cm3 | ~30 g |
| tower lid | 247.22 cm3 | ~100 g |
| lid cap | 80.57 cm3 | ~79 g |
| drain base | 121.43 cm3 | ~92 g |
| tank lid plate | 136.11 cm3 | ~95 g |

one tower is 4 modules, 4 grates, 4 spreaders, 1 lid, 1 cap, 1 drain base, 1 tank plate.

## assembly

`Tower Assembly` on onshape has all 16 parts placed. z = 0 is module 1's underside.

| part | count | z bottom .. z top |
|---|---|---|
| grow module | 4 | 0..202.2, then every 200 up to 600..802.2 |
| root grate | 4 | 30..34, then every 200 |
| water spreader | 4 | 44.8..190, then every 200 |
| tower lid | 1 | 800..819 |
| lid cap | 1 | 816..831 |
| drain base | 1 | -25..2.2 |
| tank lid plate | 1 | -31..-19 |

862 mm from the underside of the plate to the top of the cap, 266.8 across at the
widest. the grate is not on a ledge, it wedges on the module's funnel cone, which
hits r90 at z 32. rod holes are D6.5 at r99 on 45 / 135 / 225 / 315 and line up
through the module bosses, the lid, the cap and the base.
