import bpy, math, json, os, re

CFG = os.path.join(os.path.dirname(os.path.realpath(__file__)), "config.json")
cfg = json.load(open(CFG, encoding="utf-8"))

FBX = cfg["fbx"]
REF_OUT = cfg["ref_out"]
ANIM_OUT = cfg["anim_out"]
TARGET_LEN = float(cfg["target_len"])
CENTER = cfg.get("center", [0.0, 0.0, 0.0])
ROT_Z = math.radians(float(cfg.get("rot_z_deg", 90.0)))
LEN_AXIS = int(cfg.get("len_axis", 0))
MAT = cfg.get("material", "skin")
FLIP_V = bool(cfg.get("flip_v", True))
BONE_MODE = cfg.get("bone_mode", "view")   # view | world

bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.fbx(filepath=FBX)

meshes = [o for o in bpy.context.scene.objects if o.type == 'MESH']
assert meshes, "FBX 里没有网格"
bpy.context.view_layer.objects.active = meshes[0]
bpy.ops.object.select_all(action='DESELECT')
for o in meshes:
    o.select_set(True)
bpy.ops.object.join()
obj = bpy.context.active_object

# ===== 多材质：用材质槽的真实名字（清理成合法文件名）=====
def _mat_name(slot, used):
    raw = ""
    try:
        raw = slot.name or (slot.material.name if slot.material else "")
    except Exception:
        raw = ""
    if not raw:
        raw = "material"
    s = re.sub(r"[^A-Za-z0-9_]+", "_", raw).strip("_").lower()
    if not s:
        s = "material"
    if s[0].isdigit():
        s = "m_" + s
    base, k = s, 2
    while s in used:
        s = "%s_%d" % (base, k)
        k += 1
    used.add(s)
    return s

slots = list(obj.material_slots)
used = set()
mat_names = [_mat_name(ms, used) for ms in slots]
if not mat_names:
    mat_names = ["material"]
mat_count = len(mat_names)
mats_out = cfg.get("materials_out")
if mats_out:
    json.dump(mat_names, open(mats_out, "w", encoding="utf-8"))
if cfg.get("dump_materials"):
    print("OK dump_materials " + ",".join(mat_names))
    raise SystemExit(0)

dims = obj.dimensions
if cfg.get("auto_len_axis", False):
    LEN_AXIS = list(dims).index(max(dims))
fbx_len = dims[LEN_AXIS]
forced = cfg.get("forced_scale")
if forced:
    scale = float(forced)
else:
    scale = TARGET_LEN / fbx_len if fbx_len > 0 else 1.0

obj.rotation_euler = (0, 0, ROT_Z)
obj.scale = (scale, scale, scale)
bpy.ops.object.transform_apply(location=False, rotation=True, scale=True)

import mathutils
bb = [obj.matrix_world @ mathutils.Vector(c) for c in obj.bound_box]
cx = sum(v.x for v in bb) / 8.0
cy = sum(v.y for v in bb) / 8.0
cz = sum(v.z for v in bb) / 8.0
obj.location = (CENTER[0] - cx, CENTER[1] - cy, CENTER[2] - cz)
bpy.ops.object.transform_apply(location=True, rotation=False, scale=False)

bpy.ops.object.mode_set(mode='EDIT')
bpy.ops.mesh.select_all(action='SELECT')
bpy.ops.mesh.quads_convert_to_tris(quad_method='BEAUTY', ngon_method='BEAUTY')
bpy.ops.object.mode_set(mode='OBJECT')

mesh = obj.data
uv_layer = mesh.uv_layers[0].data if mesh.uv_layers else None
verts = mesh.vertices

if BONE_MODE == "world":
    # 世界模型：复刻原版 ValveBiped 骨骼，第三人称靠它把手和枪对齐
    nodes = ['version 1', 'nodes',
             '0 "ValveBiped" -1',
             '1 "ValveBiped.weapon_bone" 0',
             '2 "ValveBiped.weapon_bone_Clip" 1',
             '3 "ValveBiped.flash" 1',
             'end',
             'skeleton', 'time 0',
             '0 0.000000 0.000000 0.000000 1.570796 0.000000 0.000000',
             '1 0.940000 5.230000 2.920000 0.000000 0.000000 0.000000',
             '2 0.030000 -4.570000 3.150000 0.000000 0.000000 0.000000',
             '3 -0.950000 -2.720000 26.050000 0.000000 0.000000 0.000000',
             'end']
    SKIN_BONE = 1
else:
    # 视角模型
    nodes = ['version 1', 'nodes', '0 "v_weapon" -1', 'end', 'skeleton', 'time 0',
             '0 0.000000 0.000000 0.000000 0.000000 0.000000 0.000000', 'end']
    SKIN_BONE = 0

ref = list(nodes)
ref.append('triangles')
for poly in mesh.polygons:
    loops = poly.loop_indices
    if len(loops) != 3:
        continue
    mi = poly.material_index if poly.material_index < mat_count else 0
    ref.append(mat_names[mi])
    for li in loops:
        loop = mesh.loops[li]
        v = verts[loop.vertex_index]
        n = loop.normal
        uv = uv_layer[li].uv if uv_layer else (0.0, 0.0)
        vv = 1.0 - uv.y if FLIP_V else uv.y
        ref.append("%d %.6f %.6f %.6f %.6f %.6f %.6f %.6f %.6f" %
                   (SKIN_BONE, v.co.x, v.co.y, v.co.z, n.x, n.y, n.z, uv.x, vv))
ref.append('end')

open(REF_OUT, 'w', encoding='utf-8').write('\n'.join(ref) + '\n')
open(ANIM_OUT, 'w', encoding='utf-8').write('\n'.join(nodes) + '\n')

xs = [v.co.x for v in verts]; ys = [v.co.y for v in verts]; zs = [v.co.z for v in verts]
print("OK fbx_len=%.4f scale=%.4f bone=%s size=(%.3f,%.3f,%.3f)" %
      (fbx_len, scale, BONE_MODE, max(xs)-min(xs), max(ys)-min(ys), max(zs)-min(zs)))
