import argparse
import copy
import hashlib
import json
import os
import re
import typing
from pathlib import Path

import construct
import numpy
from matplotlib.patches import Polygon as mtPolygon
from shapely.geometry import Point
from shapely.geometry.polygon import Polygon

from mercury_engine_data_structures import samus_returns_data
from mercury_engine_data_structures.file_tree_editor import FileTreeEditor
from mercury_engine_data_structures.formats import Bmsld, Bmscc
from mercury_engine_data_structures.game_check import Game

world_names = {
    'maps/levels/c10_samus/s000_surface/s000_surface.bmsld': "Surface - East",
    'maps/levels/c10_samus/s010_area1/s010_area1.bmsld': "Area 1",
    'maps/levels/c10_samus/s020_area2/s020_area2.bmsld': "Area 2 - Exterior",
    'maps/levels/c10_samus/s025_area2b/s025_area2b.bmsld': "Area 2 - Interior",
    'maps/levels/c10_samus/s028_area2c/s028_area2c.bmsld': "Area 2 - Entrance",
    'maps/levels/c10_samus/s030_area3/s030_area3.bmsld': "Area 3 - Exterior",
    'maps/levels/c10_samus/s033_area3b/s033_area3b.bmsld': "Area 3 - Interior (Lower)",
    'maps/levels/c10_samus/s036_area3c/s036_area3c.bmsld': "Area 3 - Interior (Upper)",
    'maps/levels/c10_samus/s040_area4/s040_area4.bmsld': "Area 4 - West",
    'maps/levels/c10_samus/s050_area5/s050_area5.bmsld': "Area 4 - East",
    'maps/levels/c10_samus/s060_area6/s060_area6.bmsld': "Area 5 - Entrance",
    'maps/levels/c10_samus/s065_area6b/s065_area6b.bmsld': "Area 5 - Exterior",
    'maps/levels/c10_samus/s067_area6c/s067_area6c.bmsld': "Area 5 - Interior",
    'maps/levels/c10_samus/s070_area7/s070_area7.bmsld': "Area 6",
    'maps/levels/c10_samus/s090_area9/s090_area9.bmsld': "Area 7",
    'maps/levels/c10_samus/s100_area10/s100_area10.bmsld': "Area 8",
    'maps/levels/c10_samus/s110_surfaceb/s110_surfaceb.bmsld': "Surface - West",
}
id_to_name = {
    os.path.splitext(path.split("/")[-1])[0]: name
    for path, name in world_names.items()
}
pickup_index = 0
bmscc: typing.Optional[Bmscc] = None
# brsa: typing.Optional[Brsa] = None
bmsld: typing.Optional[Bmsld] = None
bmsld_path: str = None
events: dict[str, dict] = {}

_polygon_override = {
    # Example
    # ("s010_area1", "collision_camera_016_B"): [
    #     [24900.0, 5100.0], [17800.0, 5100.0], [17800.0, 3400.0], [24900.0, 3400.0]
    # ],
}
_rooms_for_actors = {
    "s010_area1": {
        # Example:
        # "Door002": ['collision_camera_000 (area1)', 'collision_camera_003 (area1)'],
    }
}
_camera_skip = {
    ("s010_area1", "collision_camera_016_B"),
    ("s010_area1", "collision_camera_018_B"),
    ("s010_area1", "collision_camera_023_B"),
}
dock_weakness = {
    "closed": "Access Closed",
    "power": "Power Beam Door",
    "charge": "Charge Beam Door",
}
_weakness_table_for_def = {
    'doorchargecharge': (dock_weakness["charge"], dock_weakness["charge"]),
    'doorclosedcharge': (dock_weakness["closed"], dock_weakness["charge"]),
    'doorpowerpower': (dock_weakness["power"], dock_weakness["power"]),
    'doorclosedpower': (dock_weakness["closed"], dock_weakness["power"]),
    'doorpowerclosed': (dock_weakness["power"], dock_weakness["closed"]),
    'doorchargeclosed': (dock_weakness["charge"], dock_weakness["closed"]),
}


class NodeDefinition(typing.NamedTuple):
    name: str
    data: dict[str, typing.Any]


class ActorDetails:
    actor_type: str

    def __init__(self, name: str, actor: construct.Container, level_name: str, all_rooms: dict[str, Polygon],
                 layer_name: str = "default"):
        self.name = name
        self.actor = actor
        self.actor_type = actor.type
        self.actor_layer = layer_name
        self.position = Point([actor.x, actor.y])
        try:
            self.rooms = [room for room in _rooms_for_actors[level_name][name] if room in all_rooms]
        except KeyError:
            self.rooms: list[str] = [name for name, pol in all_rooms.items() if pol.contains(self.position)]

        self.is_door = self.actor_type.startswith("door")
        # self.is_start_point = "STARTPOINT" in actor.pComponents and "dooremmy" not in self.actor_type
        self.is_pickup = any(self.actor_type.startswith(prefix) for prefix in ["powerup_", "item_", "itemsphere_"])
        self.is_usable = self.actor_type == "weightactivatedplatform"

    def create_node_template(
            self, node_type: str,
            default_name: str,
            existing_data: typing.Optional[dict[str, NodeDefinition]],
    ) -> NodeDefinition:

        result: dict = {
            "node_type": node_type,
            "heal": False,
            "coordinates": {
                "x": self.actor.x,
                "y": self.actor.y,
                "z": self.actor.z,
            },
            "description": "",
            "extra": {
                "actor_name": self.name,
                "actor_type": self.actor.type,
            },
        }
        if self.actor_layer != "default":
            result["extra"]["actor_layer"] = self.actor_layer

        if node_type == "dock":
            result["destination"] = {
                "world_name": None,
                "area_name": None,
                "node_name": None,
            }
            result["dock_type"] = "other"
            result["dock_weakness"] = "Not Determined"

        elif node_type == "pickup":
            result["pickup_index"] = None
            result["major_location"] = None

        elif node_type == "teleporter":
            result["destination"] = {
                "world_name": None,
                "area_name": None,
            }
            result["keep_name_when_vanilla"] = True
            result["editable"] = True

        elif node_type == "event":
            result["event_name"] = None

        if existing_data is not None and self.name in existing_data:
            old_node_data = existing_data[self.name]
            node_name = old_node_data.name
            if node_type == "generic" and old_node_data.data["node_type"] != "generic":
                new_result = copy.deepcopy(old_node_data.data)
                new_result["coordinates"] = result["coordinates"]
                new_result["extra"].update(result["extra"])
                result = new_result
            else:
                result["heal"] = old_node_data.data["heal"]
                result["description"] = old_node_data.data["description"]
                result["connections"] = old_node_data.data["connections"]
                for extra_key in old_node_data.data["extra"]:
                    if extra_key not in result["extra"]:
                        result["extra"][extra_key] = old_node_data.data["extra"][extra_key]
        else:
            node_name = default_name
            result["connections"] = {}

        return NodeDefinition(node_name, result)


def _find_room_orientation(world: dict, room_a: str, room_b: str):
    a_bounds = world["areas"][room_a]["extra"]["total_boundings"]
    b_bounds = world["areas"][room_b]["extra"]["total_boundings"]
    a_cx = (a_bounds["x1"] + a_bounds["x2"]) / 2
    b_cx = (b_bounds["x1"] + b_bounds["x2"]) / 2
    if a_cx < b_cx:
        return 0, 1
    elif a_cx > b_cx:
        return 1, 0
    else:
        raise ValueError(f"{room_a} and {room_b} are aligned")


def current_world_file_name():
    return re.sub(r'[^a-zA-Z0-9\- ]', r'', world_names[bmsld_path]) + ".json"


def get_actor_name_for_node(node: dict) -> str:
    return node["extra"]["actor_name"]


def _get_area_name_from_actors_in_existing_db(out_path: Path) -> dict[str, dict[str, str]]:
    area_name_by_world_and_actor = {}

    for world_name in world_names.values():
        try:
            with out_path.joinpath(f"{world_name}.json").open() as f:
                area_name_by_world_and_actor[world_name] = {}
                for area_name, area_data in json.load(f)["areas"].items():
                    for node_data in area_data["nodes"].values():
                        for variable in ["actor_name", "start_point_actor_name"]:
                            if variable in node_data["extra"]:
                                area_name_by_world_and_actor[world_name][node_data["extra"][variable]] = area_name
        except FileNotFoundError:
            area_name_by_world_and_actor[world_name] = {}

    return area_name_by_world_and_actor


def _get_existing_node_data(world: dict, area_names: dict[str, str]) -> dict[str, dict[str, NodeDefinition]]:
    node_data_for_area: dict[str, dict[str, NodeDefinition]] = {}
    for area_name, area_data in world["areas"].items():
        if "asset_id" in area_data["extra"]:
            area_names[area_data["extra"]["asset_id"]] = area_name
            node_data_for_area[area_name] = {}
            for node_name, node_data in area_data["nodes"].items():
                node_data_for_area[area_name][get_actor_name_for_node(node_data)] = NodeDefinition(node_name, node_data)
    return node_data_for_area


def create_door_nodes_for_actor(
        details: ActorDetails,
        node_data_for_area: dict[str, dict[str, NodeDefinition]],
        world: dict
) -> list[NodeDefinition]:
    extra = {}
    # if "LIFE" in actor.pComponents:
    #     extra = {
    #         "left_shield_entity": actor.pComponents.LIFE.wpLeftDoorShieldEntity,
    #         "left_shield_def": get_def_link_for_entity(actor.pComponents.LIFE.wpLeftDoorShieldEntity),
    #         "right_shield_entity": actor.pComponents.LIFE.wpRightDoorShieldEntity,
    #         "right_shield_def": get_def_link_for_entity(actor.pComponents.LIFE.wpRightDoorShieldEntity),
    #     }

    simple = {"def": details.actor_type,
              "left": extra.get("left_shield_def"),
              "right": extra.get("right_shield_def")}

    left_room, right_room = _find_room_orientation(world, *details.rooms)

    custom_weakness = [None, None]
    if simple["left"] == simple["right"] and simple["left"] is None:
        if details.actor_type in _weakness_table_for_def:
            custom_weakness[left_room], custom_weakness[right_room] = _weakness_table_for_def[details.actor_type]
        else:
            print(f"no weakness for {details.actor_type} without shields")

    doors: list[NodeDefinition] = [
        details.create_node_template("dock", f"Door ({details.name})", node_data_for_area.get(room_name))
        for room_name in details.rooms
    ]
    for i, definition in enumerate(doors):
        definition.data["extra"].update(extra)
        definition.data["destination"]["world_name"] = world["name"]
        definition.data["destination"]["area_name"] = details.rooms[(i + 1) % 2]
        definition.data["destination"]["node_name"] = doors[(i + 1) % 2].name
        if custom_weakness[i] is not None:
            definition.data["dock_type"] = "door"
            definition.data["dock_weakness"] = custom_weakness[i]

    return doors


def decode_world(root: Path, target_level: str, out_path: Path, only_update_existing_areas: bool = False,
                 skip_existing_actors: bool = True):
    global pickup_index, bmscc, bmsld, bmsld_path
    all_names = samus_returns_data.all_asset_id_to_name()
    game = Game.SAMUS_RETURNS

    pkg_editor = FileTreeEditor(root, target_game=game)

    for asset_id, name in all_names.items():
        if target_level not in name:
            continue

        if name.endswith("bmscc"):
            print(f"Reading {name}...")
            bmscc = Bmscc.parse(pkg_editor.get_raw_asset(asset_id), game)

        elif name.endswith("bmsld"):
            print(f"Reading {name}...")
            bmsld = Bmsld.parse(pkg_editor.get_raw_asset(asset_id), game)
            bmsld_path = name

    if bmscc is None or bmsld is None:
        raise ValueError("DATA IS NONE")

    all_rooms = {}

    area_name_by_world_and_actor = _get_area_name_from_actors_in_existing_db(out_path)

    try:
        with out_path.joinpath(current_world_file_name()).open() as f:
            world: dict = json.load(f)
    except FileNotFoundError:
        world: dict = {
            "name": world_names[bmsld_path],
            "extra": {
                "asset_id": bmsld_path,
            },
            "areas": {}
        }

    world_unique_id = Path(bmsld_path).stem.split("_")[1]
    area_names = {
        entry.name: f"{entry.name} ({world_unique_id})"
        for entry in bmscc.raw.layers[0].entries
    }
    node_data_for_area = _get_existing_node_data(world, area_names)

    def rand_color(s):
        return [x / 300.0 for x in hashlib.md5(bytes(str(sorted(s)), 'ascii')).digest()[0:3]]

    handles = []
    import matplotlib.pyplot as plt
    plt.figure(1, figsize=(20, 10))
    plt.title(target_level)

    # Parse Camera Groups

    for entry in bmscc.raw.layers[0].entries:
        assert entry.type == "POLYCOLLECTION2D"
        x1, y1, x2, y2 = entry.data.total_boundings
        if abs(x1) > 59999 or abs(y1) > 59999 or abs(x2) > 59999 or abs(y2) > 59999:
            continue

        area_name = area_names[entry.name]
        if (target_level, entry.name) in _camera_skip:
            world["areas"].pop(area_name, None)
            continue

        assert len(entry.data.polys) == 1
        raw_vertices = _polygon_override.get((target_level, entry.name),
                                             [(v.x, v.y) for v in entry.data.polys[0].points])
        vertices = numpy.array(raw_vertices)

        c = [0.2, 0.7, 0.6]
        patch = mtPolygon(vertices, linewidth=1, edgecolor=c, facecolor=(c[0], c[1], c[2], 0.1))
        plt.gca().add_patch(patch)
        plt.text((x1 + x2) / 2, (y1 + y2) / 2, entry.name[17:], color=c, ha='center', size='x-small')
        handles.append(patch)

        all_rooms[area_name] = Polygon(vertices)
        if only_update_existing_areas and area_name in world["areas"]:
            continue

        world["areas"][area_name] = {
            "default_node": None,
            "valid_starting_location": False,
            "extra": {
                "total_boundings": {
                    "x1": x1,
                    "x2": x2,
                    "y1": y1,
                    "y2": y2,
                },
                "polygon": raw_vertices,
                "asset_id": entry.name,
            },
            "nodes": {},
        }

    # Parse Actors
    all_default_details: dict[str, ActorDetails] = {
        name: ActorDetails(name, actor, target_level, all_rooms)
        for actor_list in bmsld.raw.actors
        for name, actor in actor_list.items()
    }

    def add_node(target_area: str, node_def: NodeDefinition):
        new_actor = get_actor_name_for_node(node_def.data)
        for existing_name, existing_node in world["areas"][target_area]["nodes"].items():
            if existing_name == node_def.name:
                continue
            if get_actor_name_for_node(existing_node) == new_actor:
                raise ValueError(f"New node {node_def.name} with actor {new_actor} conflicts "
                                 f"with existing node {existing_name} in {target_area}")

        world["areas"][target_area]["nodes"][node_def.name] = node_def.data

    for name, details in all_default_details.items():
        if not any([details.is_door, details.is_pickup, details.is_usable]):
            continue

        plt.annotate(name, [details.position.x, details.position.y], fontsize='xx-small', ha='center')
        plt.plot(details.position.x, details.position.y, "o", color=rand_color(details.actor_type))

        if details.is_door:
            if len(details.rooms) == 2:
                doors = create_door_nodes_for_actor(details, node_data_for_area, world)
                for i, room_name in enumerate(details.rooms):
                    add_node(room_name, doors[i])

            else:
                print("multiple rooms for door!", name, details.position, details.rooms)

        elif details.is_pickup:
            for room_name in details.rooms:
                definition = details.create_node_template("pickup", f"Pickup ({name})",
                                                          node_data_for_area.get(room_name))
                definition.data.update({
                    "pickup_index": pickup_index,
                    "major_location": "tank" not in details.actor_type,
                })
                add_node(room_name, definition)

            if len(details.rooms) != 1:
                print("pickup in multiple rooms!", details.name, details.rooms)
            pickup_index += 1

        elif details.is_usable:
            if len(details.rooms) != 1:
                print("usable multiple rooms?", details.name, details.rooms)
                continue

            room_name = details.rooms[0]
            definition = details.create_node_template(
                "generic",
                f"Usable ({name})",
                node_data_for_area.get(room_name),
            )
            add_node(room_name, definition)

        else:
            raise ValueError("What kind of actor is this?!")

    handles_by_label = {}
    handles_by_label = {
        key: value
        for key, value in sorted(handles_by_label.items(), key=lambda it: it[0])
    }
    plt.legend(handles_by_label.values(), handles_by_label.keys())

    plt.plot()
    plt.savefig(f"{target_level}.png", dpi=200, bbox_inches='tight')
    # plt.show()
    plt.close()

    print(f"Writing updated {current_world_file_name()}")
    with out_path.joinpath(current_world_file_name()).open("w") as f:
        json.dump(world, f, indent=4)


def decode_all_worlds(root: Path, out_path: Path):
    header_path = out_path.joinpath("header.json")
    with header_path.open() as f:
        header = json.load(f)

    events.clear()
    events.update(header["resource_database"]["events"])

    for area_path in world_names.keys():
        level_name = os.path.splitext(os.path.split(area_path)[1])[0]
        decode_world(root, level_name, out_path)
        if current_world_file_name() not in header["worlds"]:
            header["worlds"].append(current_world_file_name())

    header["resource_database"]["events"] = events

    with header_path.open("w") as f:
        json.dump(header, f, indent=4)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("game_root", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--only", help="Only process the given world")
    args = parser.parse_args()

    if args.only is not None:
        decode_world(args.game_root, args.only, args.output)
    else:
        decode_all_worlds(args.game_root, args.output)


if __name__ == '__main__':
    main()
