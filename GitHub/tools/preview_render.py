# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 dy是鹅的不是
"""Blender 无光照预览渲染：FBX + 贴图 -> 左右两张 PNG。

由 GmodModelImporter.py 调用，读取同目录 preview_config.json：
{
  "fbx": "...",
  "textures": ["a.png", null, ...],   # 按材质槽顺序，null/空 = 纯黑
  "out": "输出目录",
  "size": 256
}
"""
import bpy
import json
import os

HERE = os.path.dirname(os.path.realpath(__file__))
CFG = os.path.join(HERE, "preview_config.json")

with open(CFG, encoding="utf-8-sig") as f:
    cfg = json.load(f)

FBX = cfg["fbx"]
TEXS = cfg.get("textures") or []
OUT = cfg["out"]
SIZE = int(cfg.get("size", 256))

os.makedirs(OUT, exist_ok=True)

bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.fbx(filepath=FBX)

meshes = [o for o in bpy.context.scene.objects if o.type == 'MESH']
if not meshes:
    raise SystemExit("PREVIEW_FAIL no mesh")

bpy.context.view_layer.objects.active = meshes[0]
bpy.ops.object.select_all(action='DESELECT')
for o in meshes:
    o.select_set(True)
bpy.ops.object.join()
obj = bpy.context.active_object

bpy.ops.object.transform_apply(location=False, rotation=True, scale=True)

# 统一缩放、居中
dims = obj.dimensions
biggest = max(dims.x, dims.y, dims.z) or 1.0
s = 2.0 / biggest
obj.scale = (s, s, s)
bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)

from mathutils import Vector
bb = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
center = sum(bb, Vector()) / 8.0
obj.location = obj.location - center
bpy.ops.object.transform_apply(location=True, rotation=False, scale=False)

# 每个材质槽 -> 自发光贴图
for i, slot in enumerate(obj.material_slots):
    tex = TEXS[i] if i < len(TEXS) else None
    mat = bpy.data.materials.new("prev_%d" % i)
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()

    out = nt.nodes.new("ShaderNodeOutputMaterial")
    em = nt.nodes.new("ShaderNodeEmission")
    em.inputs["Strength"].default_value = 1.0

    if tex and os.path.isfile(tex):
        try:
            img = bpy.data.images.load(tex, check_existing=False)
            tn = nt.nodes.new("ShaderNodeTexImage")
            tn.image = img
            nt.links.new(tn.outputs["Color"], em.inputs["Color"])
        except Exception:
            em.inputs["Color"].default_value = (0, 0, 0, 1)
    else:
        em.inputs["Color"].default_value = (0, 0, 0, 1)

    nt.links.new(em.outputs["Emission"], out.inputs["Surface"])
    slot.material = mat

scene = bpy.context.scene

# 深色背景，贴近导入器界面
world = bpy.data.worlds.new("PrevWorld")
scene.world = world
world.use_nodes = True
bg = world.node_tree.nodes.get("Background")
if bg:
    bg.inputs[0].default_value = (0.055, 0.055, 0.055, 1.0)
    bg.inputs[1].default_value = 1.0

for eng in ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE", "CYCLES"):
    try:
        scene.render.engine = eng
        break
    except Exception:
        continue

try:
    scene.eevee.taa_render_samples = 16
except Exception:
    pass
try:
    scene.view_settings.view_transform = 'Standard'
except Exception:
    pass

scene.render.resolution_x = SIZE
scene.render.resolution_y = SIZE
scene.render.resolution_percentage = 100
scene.render.film_transparent = False
scene.render.image_settings.file_format = 'PNG'
scene.render.image_settings.color_mode = 'RGB'

cam_data = bpy.data.cameras.new("PrevCam")
cam = bpy.data.objects.new("PrevCam", cam_data)
scene.collection.objects.link(cam)
scene.camera = cam
cam_data.type = 'ORTHO'
cam_data.ortho_scale = 2.45

views = {
    "left": (-1.0, -1.0, 0.55),
    "right": (1.0, -1.0, 0.55),
}

for name, (x, y, z) in views.items():
    loc = Vector((x, y, z)).normalized() * 5.0
    cam.location = loc
    cam.rotation_euler = (-loc).to_track_quat('-Z', 'Y').to_euler()
    scene.render.filepath = os.path.join(OUT, name + ".png")
    bpy.ops.render.render(write_still=True)

print("PREVIEW_OK")
