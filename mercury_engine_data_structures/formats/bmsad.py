import re

import construct
from construct.core import (
    Array, Byte, Bytes, Computed, Const, Construct, ExprAdapter,
    Flag, Float32l, FocusedSeq, GreedyRange, Hex, If, IfThenElse,
    Int16ul, Int32ul, Optional, Peek,
    PrefixedArray, StopIf, Struct, Switch,
)
from construct.debug import Probe

from mercury_engine_data_structures import common_types, dread_data, type_lib
from mercury_engine_data_structures.common_types import Float, StrId, make_dict, make_vector
from mercury_engine_data_structures.construct_extensions.alignment import PrefixedAllowZeroLen
from mercury_engine_data_structures.construct_extensions.misc import ErrorWithMessage
from mercury_engine_data_structures.formats import BaseResource, dread_types
from mercury_engine_data_structures.formats.property_enum import PropertyEnum, PropertyEnumUnsafe
from mercury_engine_data_structures.game_check import Game

component_keys = [
    "ABILITY",
    "ACTIVATABLE_BY_PROJECTILE",
    "ACTIVATABLE",
    "AI",
    "AIM",
    "AIMCAMERAVISIBLEONLY",
    "AINAVIGATION",
    "AISMARTOBJECT",
    "ALTERNATIVE_ACTION_PLAYER",
    "ANIMATION",
    "AREAFX",
    "ATTACK",
    "AUDIO",
    "BILLBOARD",
    "BONETOCONSTANT",
    "BREAKABLESCENARIO",
    "BTOBSERVER",
    "CAMERARAIL",
    "CENTRALUNIT",
    "CHAIN_REACTION_ACTION_SWITCHER",
    "CHANGE_STAGE_NAVMESH_ITEM",
    "CHOZOMBIEFXCOMPONENT",
    "COLLISION",
    "COLLISION MATERIAL CACHE",
    "CONTROLLER",
    "COOLDOWNFAN",
    "CUBEMAP",
    "CUTSCENE",
    "DAMAGE",
    "DOOREMMYFX",
    "DROP",
    "ELECTRICREACTION",
    "ELECTRIFYING",
    "EMMYVALVE",
    "ENHANCEWEAKSPOT",
    "ESCAPE_SEQUENCE_EXPLOSION",
    "EVENTPROP",
    "EVENTSCENARIO",
    "FACTION",
    "FIRE",
    "FLOATING_PROP_ACTING",
    "FOOTSTEP",
    "FORCED_MOVEMENT_AREA",
    "FROZEN",
    "FUSIBLEBOX",
    "FX",
    "GRAB",
    "GRAPPLEPOINT",
    "GUN",
    "HEATABLESHIELD",
    "HECATHONPLANKTONFX",
    "INPUT",
    "INVENTORY",
    "LA_TRIGGER",
    "LANDMARK",
    "LIFE",
    "LIGHTING",
    "LISTENER",
    "LOGICCAMERA",
    "LOGICPATH",
    "LOGICSHAPE",
    "LOOKATPLAYER",
    "MAGMA_KRAID_PISTON_PLATFORM",
    "MAGMA_KRAID_SCENARIO_CONTROLLER",
    "MAGMA_KRAID_SPIKE",
    "MAGNET_SLIDING_BLOCK_RAIL",
    "MAGNET_SLIDING_BLOCK",
    "MAGNET_SURFACE",
    "MAGNETHUSK",
    "MATERIALFX",
    "MELEE",
    "MODELINSTANCE",
    "MODELUPDATER",
    "MORPH_BALL_LAUNCHER_EXIT",
    "MORPH_BALL_LAUNCHER",
    "MOVEMENT",
    "MULTI_LOCK_ON_BLOCK",
    "NAVMESHITEM",
    "NOZZLE",
    "OMNILIGHT",
    "PICKABLE",
    "POSITIONALSOUND",
    "RINKAUNIT",
    "ROTATIONAL",
    "RUMBLE",
    "SCORPIUSFX",
    "SCRIPT",
    "SEGMENTLIGHT",
    "SENSORDOOR",
    "SHOCKWAVE",
    "SHOCKWAVEPOOL",
    "SHOT",
    "SIMULATION",
    "SLIDLEOUTSPAWNPOINT",
    "SMARTOBJECT",
    "SONAR_TARGET",
    "SPAWNGROUP",
    "SPAWNPOINT",
    "SPECIALENERGY",
    "SPOTLIGHT",
    "STARTPOINT",
    "THERMALREACTION",
    "TILEGROUP",
    "TIMELINECOMPONENT",
    "TRIGGER",
    "TUNNEL_TRAP",
    "USABLE",
    "VIDEOMANAGER",
    "WEIGHT_ACTIVABLE_PROP",
    "WORLDGRAPH",
    "WTCHANGE",
    "XPARASITEDROP",
    "ZIPLINE_RAIL",
]
component_keys.extend([s + "COMPONENT" for s in component_keys])

Char = construct.PaddedString(1, 'ascii')

FunctionArgument = Struct(
    type=Char,
    value=Switch(
        construct.this.type,
        {
            's': StrId,
            'f': Float,
            'b': Flag,
            'i': Int32ul,
        },
        ErrorWithMessage(lambda ctx: f"Unknown argument type: {ctx.type}", construct.SwitchError)
    )
)
Functions = make_vector(Struct(
    name=StrId,
    unk=Int16ul,
    params=common_types.DictAdapter(common_types.make_vector(
        common_types.DictElement(FunctionArgument, key=PropertyEnum)
    )),
))

fieldtypes = {k: v for k, v in vars(dread_types).items() if isinstance(v, construct.Construct)}


def find_charclass_for_type(type_name: str):
    if type_name == "CActorComponent":
        return "CActorComponentDef"

    as_char = "CCharClass" + type_name[1:]
    if as_char in fieldtypes:
        return as_char

    return find_charclass_for_type(
        type_lib.get_parent_for(type_name),
    )


def Dependencies():
    component_dependencies = {
        "CFXComponent": make_vector(Struct(
            "file" / StrId,
            "unk1" / Int32ul,
            "unk2" / Int32ul,
            "unk3" / Byte
        )),
        "CCollisionComponent": Struct(
            "file" / StrId,
            "unk" / Int16ul
        ),
        "CGrabComponent": make_vector(Struct(
            "unk1" / StrId,
            "unk2" / StrId,
            "unk3" / StrId,
            "unk4" / Float32l,
            "unk5" / Byte,
            "unk6" / Byte,
            "unk7" / Int16ul,
            "unk8" / Array(2, Struct(
                "unk2" / Int16ul,
                "unk1" / Array(8, Float32l),
            )),
        )),
        "CBillboardComponent": Struct(
            "id1" / StrId,
            "unk1" / make_vector(Struct(
                "id" / StrId,
                "unk1" / Array(3, Int32ul),
                "unk2" / Byte,
                "unk3" / Array(2, Int32ul),
                "unk4" / Float32l
            )),
            "id2" / StrId,
            "unk2" / make_vector(Struct(
                "id" / StrId,
                "unk1" / Byte, 
                "unk2" / Array(4, Int32ul)
            )),
        ),
        "CSwarmControllerComponent": Struct(
            "unk1" / make_vector(StrId),
            "unk2" / make_vector(StrId),
            "unk3" / make_vector(StrId)
        )
    }
    component_dependencies["CStandaloneFXComponent"] = component_dependencies["CFXComponent"]

    def component_type(this):
        for component_type in component_dependencies.keys():
            if type_lib.is_child_of(this.type, component_type):
                return component_type
        return None
    
    return Switch(component_type, component_dependencies)

Component = Struct(
    type=StrId,
    unk_1=Array(2, Hex(Int32ul)),
    fields=PrefixedAllowZeroLen(
        Int32ul,
        Struct(
            empty_string=PropertyEnum,
            root=PropertyEnum,
            fields=Switch(
                lambda ctx: find_charclass_for_type(ctx._._.type),
                fieldtypes,
                ErrorWithMessage(lambda ctx: f"Unknown component type: {ctx._._.type}", construct.SwitchError)
            )
        )
    ),
    extra_fields=construct.If(
        lambda this: type_lib.is_child_of(this.type, "CComponent"),
        common_types.DictAdapter(common_types.make_vector(
            common_types.DictElement(Struct(
                "type" / StrId,
                "value" / Switch(
                    construct.this.type,
                    {
                        "bool": Flag,
                        "string": StrId
                    },
                    ErrorWithMessage(lambda ctx: f"Unknown argument type: {ctx.type}", construct.SwitchError)
                )
            ))
        ))
    ),
    functions=Functions,
    dependencies=Dependencies()
)

CCharClass = Struct(
    model_name=StrId,
    unk_1=Int16ul,
    unk_2=Int32ul,
    unk_3=Int16ul,
    sub_actors=PrefixedArray(Int32ul, StrId),
    unk_4=Array(9, Float32l),
    magic=Const(0xFFFFFFFF, Hex(Int32ul)),
    unk_5=Byte,
    unk_6=StrId,
    unk_7=Byte,

    components=make_dict(Component),
    
    binaries=make_vector(StrId),
    sources=make_vector(StrId >> Byte),
)

CActorDef = Struct(
    unk_1=Int16ul,
    unk_2=Int32ul,
    unk_3=Int16ul,
    sub_actors=PrefixedArray(Int32ul, StrId),
    unk_4=StrId,

    components=make_dict(Component),
    
    binaries=make_vector(StrId),
    sources=make_vector(StrId >> Byte),
)

property_types = {
    "CCharClass": CCharClass,
    "CActorDef": CActorDef
}
#
BMSAD = Struct(
    magic=Const(b"MSAD"),
    version=Const(0x0200000F, Hex(Int32ul)),

    # # gameeditor::CGameModelRoot
    # root_type=construct.Const('Root', PropertyEnum),
    # Root=gameeditor_CGameModelRoot,

    name=StrId,
    type=StrId,

    property=Switch(
        construct.this.type,
        property_types,
        ErrorWithMessage(lambda ctx: f"Unknown property type: {ctx.type}"),
    ),
    # rest=Peek(construct.GreedyBytes),

    # z=Probe(),
    _end=construct.Terminated,
)


# BMSAD = game_model_root.create('CActorDef', 0x02000031)


class Bmsad(BaseResource):
    @classmethod
    def construct_class(cls, target_game: Game) -> Construct:
        return BMSAD
