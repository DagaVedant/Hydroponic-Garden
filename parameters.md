# parameters - the onshape variable studio

i broke it up into sections here so its easier to see what values for what part (on onshape its all one large table)

## global

| name | type | value |
|---|---|---|
| `MODULE_COUNT` | Number | `4` |
| `MODULE_DIA` | Length | `190 mm` |
| `MODULE_HEIGHT` | Length | `200 mm` |
| `WALL_THICKNESS` | Length | `3 mm` |
| `CHAMBER_DIA` | Length | `#MODULE_DIA - 2 * #WALL_THICKNESS` |
| `CHAMBER_RADIUS` | Length | `#CHAMBER_DIA / 2` |
| `TOWER_HEIGHT` | Length | `#MODULE_COUNT * #MODULE_HEIGHT` |

## pipe

| name | type | value |
|---|---|---|
| `PIPE_OUTER_DIA` | Length | `21.34 mm` |
| `PIPE_INNER_DIA` | Length | `15.8 mm` |
| `PIPE_TUNNEL_INNER_DIA` | Length | `#PIPE_OUTER_DIA + 1.66 mm` |
| `PIPE_TUNNEL_OUTER_DIA` | Length | `#PIPE_TUNNEL_INNER_DIA + 2 * #WALL_THICKNESS` |
| `PIPE_TUNNEL_RADIUS` | Length | `#PIPE_TUNNEL_OUTER_DIA / 2` |

## net pot

| name | type | value |
|---|---|---|
| `POT_LIP_DIA` | Length | `52.8 mm` |
| `POT_MOUTH_DIA` | Length | `48 mm` |
| `POT_BASE_DIA` | Length | `30.5 mm` |
| `POT_HEIGHT` | Length | `50.3 mm` |
| `POT_TAPER` | Number | `(#POT_LIP_DIA - #POT_BASE_DIA) / #POT_HEIGHT` |

## socket

| name | type | value |
|---|---|---|
| `SOCKET_COUNT` | Number | `4` |
| `SOCKET_ANGLE` | Angle | `45 deg` |
| `SOCKET_HEIGHT` | Length | `125 mm` |
| `SOCKET_FACE_OFFSET` | Length | `8 mm` |
| `SOCKET_TUBE_LENGTH` | Length | `22 mm` |
| `SOCKET_TUBE_WALL` | Length | `3 mm` |
| `SOCKET_TOP_DIA` | Length | `50.6 mm` |
| `SOCKET_DEPTH` | Length | `15 mm` |
| `SOCKET_BOTTOM_DIA` | Length | `#SOCKET_TOP_DIA - #POT_TAPER * #SOCKET_DEPTH` |
| `SOCKET_TUBE_OUTER_DIA` | Length | `#SOCKET_TOP_DIA + 2 * #SOCKET_TUBE_WALL` |
| `SOCKET_CLEARANCE_DIA` | Length | `#SOCKET_BOTTOM_DIA + 1 mm` |

## spreader

| name | type | value |
|---|---|---|
| `SPREADER_DIA` | Length | `142 mm` |
| `SPREADER_RADIUS` | Length | `#SPREADER_DIA / 2` |
| `SPREADER_SLOPE` | Angle | `20 deg` |
| `SPREADER_HEIGHT` | Length | `172 mm` |
| `SPOUT_COUNT` | Number | `4` |
| `SPOUT_REACH` | Length | `#CHAMBER_RADIUS - 7 mm` |
| `SPLITTER_ANGLE` | Angle | `45 deg` |

## floor and gutter

| name | type | value |
|---|---|---|
| `GUTTER_RADIUS` | Length | `60 mm` |
| `GUTTER_INNER_DIA` | Length | `108 mm` |
| `GUTTER_OUTER_DIA` | Length | `132 mm` |
| `GUTTER_FLOOR_HEIGHT` | Length | `0 mm` |
| `GUTTER_LIP_HEIGHT` | Length | `8 mm` |
| `FLOOR_SLOPE` | Angle | `45 deg` |
| `FLOOR_HEIGHT_AT_WALL` | Length | `#GUTTER_LIP_HEIGHT + (#CHAMBER_RADIUS - #GUTTER_OUTER_DIA / 2)` |
| `FLOOR_PEAK_HEIGHT` | Length | `#GUTTER_LIP_HEIGHT + (#GUTTER_INNER_DIA / 2 - #PIPE_TUNNEL_RADIUS)` |
| `DRIP_HOLE_DIA` | Length | `8 mm` |
| `DRIP_HOLE_COUNT` | Number | `8` |
| `DRIP_HOLE_CIRCLE_DIA` | Length | `#GUTTER_RADIUS * 2` |

## grate

| name | type | value |
|---|---|---|
| `GRATE_HEIGHT` | Length | `#FLOOR_HEIGHT_AT_WALL` |
| `GRATE_DIA` | Length | `#CHAMBER_DIA - 4 mm` |
| `GRATE_THICKNESS` | Length | `4 mm` |
| `GRATE_CUTOUT_DIA` | Length | `52 mm` |
| `GRATE_GAP` | Length | `8 mm` |
| `GRATE_RIB` | Length | `3 mm` |

## joints and ribs

| name | type | value |
|---|---|---|
| `ROD_DIA` | Length | `5 mm` |
| `ROD_COUNT` | Number | `4` |
| `ROD_CLEARANCE_DIA` | Length | `6.5 mm` |
| `ROD_CIRCLE_DIA` | Length | `#MODULE_DIA + #ROD_BOSS_DIA / 2` |
| `ROD_BOSS_DIA` | Length | `16 mm` |
| `ROD_BOSS_HEIGHT` | Length | `20 mm` |
| `ROD_LENGTH` | Length | `#TOWER_HEIGHT + 90 mm` |
| `PIN_DIA` | Length | `5 mm` |
| `PIN_COUNT` | Number | `2` |
| `JOINT_LIP_HEIGHT` | Length | `2.5 mm` |
| `JOINT_LIP_CLEARANCE` | Length | `0.3 mm` |
| `RIB_COUNT` | Number | `4` |
| `RIB_WIDTH` | Length | `14 mm` |
| `RIB_THICKNESS` | Length | `8 mm` |
| `RIB_ANGLE` | Angle | `45 deg` |

## tank

| name | type | value |
|---|---|---|
| `BUCKET_DIA` | Length | `302 mm` |
| `BUCKET_HEIGHT` | Length | `368 mm` |
| `TANK_PLATE_DIA` | Length | `235 mm` |