#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""weapon_replace.py — FBX+PNG 编译成 Gmod 武器/道具模型并打包 addon。"""
import argparse, json, os, struct, shutil, subprocess, sys

BLENDER   = r"D:\Steam\steamapps\common\Blender\blender.exe"
STUDIOMDL = r"D:\Steam\steamapps\common\GarrysMod\bin\studiomdl.exe"
CSS_VPK   = r"D:\Steam\steamapps\common\GarrysMod\sourceengine\content_cstrike_dir.vpk"
GMOD_DIR  = r"D:\Steam\steamapps\common\GarrysMod\garrysmod"
HERE      = os.path.dirname(os.path.realpath(__file__))

HAND_KEYWORDS = ['hand', 'thumb', 'finger', 'pinky', 'ring', 'middle',
                 'index', 'wrist', 'arm', 'root', 'eff']


# ---------- VPK ----------
def _cstr(d, pos):
    e = d.index(b"\x00", pos)
    return d[pos:e].decode("latin1"), e + 1


def vpk_extract(vpk_dir, wanted, outdir):
    d = open(vpk_dir, "rb").read()
    sig, ver, tree = struct.unpack_from("<III", d, 0)
    assert sig == 0x55AA1234
    pos, end = 28, 28 + tree
    entries = []
    while pos < end:
        ext, pos = _cstr(d, pos)
        if ext == "":
            break
        while True:
            path, pos = _cstr(d, pos)
            if path == "":
                break
            while True:
                fn, pos = _cstr(d, pos)
                if fn == "":
                    break
                crc, pre, arch, off, ln, term = struct.unpack_from("<IHHIIH", d, pos)
                pos += 18
                full = (path + "/" + fn + "." + ext) if path else (fn + "." + ext)
                entries.append((full, arch, off, ln, pre))
    results = [(f, a, o, l, p) for (f, a, o, l, p) in entries
               if any(f.rsplit(".", 1)[0] == w or f.rsplit(".", 1)[0].startswith(w + ".")
                      for w in wanted)]
    os.makedirs(outdir, exist_ok=True)
    cache, out = {}, {}
    for full, arch, off, ln, pre in results:
        p = vpk_dir.replace("_dir.vpk", "_%03d.vpk" % arch)
        if arch not in cache:
            cache[arch] = open(p, "rb").read()
        blob = cache[arch][off: off + (ln - pre)]
        dst = os.path.join(outdir, full.replace("/", os.sep))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        open(dst, "wb").write(blob)
        out[full.rsplit(".", 1)[0]] = dst
    return out


# ---------- MDL / VVD ----------
def mdl_bones(mdl_path):
    d = open(mdl_path, "rb").read()
    i32 = lambda o: struct.unpack_from("<i", d, o)[0]
    nb, bi = i32(156), i32(160)
    def s(o):
        e = d.index(b"\x00", o)
        return d[o:e].decode("latin1")
    return [(s(bi + i * 216 + i32(bi + i * 216)), i32(bi + i * 216 + 4)) for i in range(nb)]


def vvd_bbox(vvd_path, weapon_idx=None):
    d = open(vvd_path, "rb").read()
    n = struct.unpack_from("<i", d, 20)[0]
    vstart = struct.unpack_from("<i", d, 56)[0]
    xs, ys, zs = [], [], []
    for i in range(n):
        o = vstart + i * 48
        if weapon_idx is not None and d[o + 12] not in weapon_idx:
            continue
        x, y, z = struct.unpack_from("<3f", d, o + 16)
        xs.append(x); ys.append(y); zs.append(z)
    if not xs:
        xs = ys = zs = [0.0]
    mn = (min(xs), min(ys), min(zs)); mx = (max(xs), max(ys), max(zs))
    return mn, mx, tuple((a + b) / 2 for a, b in zip(mn, mx)), tuple(b - a for a, b in zip(mn, mx))


def weapon_bone_set(bones):
    s = set()
    for i, (name, _) in enumerate(bones):
        nl = name.lower()
        if not any(k in nl for k in HAND_KEYWORDS):
            s.add(i)
    return s


# ---------- Blender ----------
CREATE_NO_WINDOW = 0x08000000  # 隐藏子进程控制台窗口（Windows）


def make_smd(fbx, target_len, center, ref_out, anim_out, mat="skin", rot_z=90.0,
             len_axis=0, forced_scale=None, bone_mode="view", flip_v=False,
             auto_len_axis=False, materials_out=None):
    cfg = dict(fbx=fbx, ref_out=ref_out, anim_out=anim_out, target_len=target_len,
               center=list(center), rot_z_deg=rot_z, len_axis=len_axis, material=mat,
               bone_mode=bone_mode, flip_v=flip_v, auto_len_axis=auto_len_axis)
    if forced_scale:
        cfg["forced_scale"] = forced_scale
    if materials_out:
        cfg["materials_out"] = materials_out
    json.dump(cfg, open(os.path.join(HERE, "config.json"), "w", encoding="utf-8"), ensure_ascii=False)
    r = subprocess.run([BLENDER, "--background", "--factory-startup",
                        "--python", os.path.join(HERE, "fbx_to_smd.py")],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       creationflags=CREATE_NO_WINDOW)
    out = (r.stdout or "") + (r.stderr or "")
    return ("OK " in out and os.path.exists(ref_out)), out


def dump_materials(fbx, out_json):
    """只导入 FBX，返回材质名列表（mat0, mat1, ...）"""
    cfg = dict(fbx=fbx,
               ref_out=os.path.join(HERE, "_dummy_ref.smd"),
               anim_out=os.path.join(HERE, "_dummy_anim.smd"),
               target_len=1.0, center=[0, 0, 0], materials_out=out_json,
               dump_materials=True)
    json.dump(cfg, open(os.path.join(HERE, "config.json"), "w", encoding="utf-8"), ensure_ascii=False)
    r = subprocess.run([BLENDER, "--background", "--factory-startup",
                        "--python", os.path.join(HERE, "fbx_to_smd.py")],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       creationflags=CREATE_NO_WINDOW)
    out = (r.stdout or "") + (r.stderr or "")
    if os.path.exists(out_json):
        try:
            return json.load(open(out_json, encoding="utf-8")), out
        except Exception:
            pass
    return None, out


# ---------- QC / compile ----------
def write_qc(path, modelname, cdmaterials, smd, sequences, collision=None):
    lines = ['$modelname "%s"' % modelname,
             '$cdmaterials "%s"' % cdmaterials,
             '$model "body" "%s"' % smd,
             '$surfaceprop "metal"', '$mostlyopaque']
    lines += sequences
    if collision:
        lines.append('$collisionmodel "%s" { $mass 10 }' % collision)
    open(path, "w", encoding="utf-8").write("\n".join(lines) + "\n")


def compile_qc(qc, workdir):
    game = os.path.join(workdir, "game")
    os.makedirs(os.path.join(game, "models", "weapons"), exist_ok=True)
    gi = os.path.join(game, "gameinfo.txt")
    if not os.path.exists(gi):
        open(gi, "w").write('"GameInfo"\n{\n game "tmp"\n FileSystem\n {\n  SteamAppId 4000\n'
                            '  SearchPaths\n  {\n   Game |gameinfo_path|.\n  }\n }\n}\n')
    r = subprocess.run([STUDIOMDL, "-game", game, "-nop4", qc],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       creationflags=CREATE_NO_WINDOW)
    return ("Completed" in (r.stdout or "")) or r.returncode == 0


# ---------- VTF (7.1, 64 字节头, DXT1) ----------
def _dxt1_encode(img):
    import numpy as np
    w, h = img.size
    ph = (h + 3) // 4 * 4
    pw = (w + 3) // 4 * 4
    arr = np.asarray(img.convert("RGB"), dtype=np.uint8)
    canvas = np.zeros((ph, pw, 3), dtype=np.uint8)
    canvas[:h, :w] = arr
    b = canvas.reshape(ph // 4, 4, pw // 4, 4, 3).transpose(0, 2, 1, 3, 4).reshape(-1, 16, 3).astype(np.int32)
    n = b.shape[0]
    lum = b[:, :, 0] * 0.299 + b[:, :, 1] * 0.587 + b[:, :, 2] * 0.114
    c_min = b[np.arange(n), lum.argmin(1)]
    c_max = b[np.arange(n), lum.argmax(1)]

    def to565(c):
        r = (c[:, 0] >> 3) & 0x1f
        g = (c[:, 1] >> 2) & 0x3f
        bl = (c[:, 2] >> 3) & 0x1f
        return ((r << 11) | (g << 5) | bl).astype(np.uint16)

    c0 = to565(c_max); c1 = to565(c_min)
    swap = c0 <= c1
    c0, c1 = np.where(swap, c1, c0).astype(np.uint16), np.where(swap, c0, c1).astype(np.uint16)

    def from565(v):
        r = (v >> 11) & 0x1f; g = (v >> 5) & 0x3f; bl = v & 0x1f
        return np.stack([(r << 3) | (r >> 2), (g << 2) | (g >> 4), (bl << 3) | (bl >> 2)], axis=-1).astype(np.int32)

    p0 = from565(c0); p1 = from565(c1)
    pal = np.stack([p0, p1, (2 * p0 + p1) // 3, (p0 + 2 * p1) // 3], axis=1)
    d = ((b[:, :, None, :] - pal[:, None, :, :]) ** 2).sum(-1)
    idx = d.argmin(2).astype(np.uint32)
    shifts = (np.arange(16) * 2).astype(np.uint32)
    bits = (idx << shifts).sum(1).astype(np.uint32)
    out = np.zeros((n, 8), dtype=np.uint8)
    out[:, 0:2] = c0.astype('<u2').view(np.uint8).reshape(n, 2)
    out[:, 2:4] = c1.astype('<u2').view(np.uint8).reshape(n, 2)
    out[:, 4:8] = bits.astype('<u4').view(np.uint8).reshape(n, 4)
    return out.tobytes()


def png_to_vtf(png, vtf):
    from PIL import Image
    img = Image.open(png).convert("RGBA").transpose(Image.FLIP_TOP_BOTTOM)
    w, h = img.size
    data = _dxt1_encode(img)
    hdr = b'VTF\x00' + struct.pack('<II', 7, 1) + struct.pack('<I', 64)
    hdr += struct.pack('<HH', w, h) + struct.pack('<I', 0x00000100)
    hdr += struct.pack('<HH', 1, 0) + struct.pack('<I', 0)
    hdr += struct.pack('<fff', 1.0, 1.0, 1.0) + struct.pack('<I', 0)
    hdr += struct.pack('<f', 1.0) + struct.pack('<I', 13)
    hdr += struct.pack('<B', 1) + struct.pack('<I', 13)
    hdr += struct.pack('<BB', 0, 0) + b'\x00'
    assert len(hdr) == 64, len(hdr)
    with open(vtf, 'wb') as f:
        f.write(hdr)
        f.write(data)
    return 1


def write_vmt(path, basetexture):
    open(path, "w", encoding="utf-8").write(
        'VertexLitGeneric\n{\n\t"$basetexture" "%s"\n}\n' % basetexture)


# ---------- main ----------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fbx", required=True)
    ap.add_argument("--png", required=True)
    ap.add_argument("--view", required=True)
    ap.add_argument("--world", required=True)
    ap.add_argument("--scale", type=float, default=None)
    ap.add_argument("--name", default=None)
    ap.add_argument("--out", default=os.path.join(HERE, "addon_out"))
    args = ap.parse_args()

    work = os.path.join(HERE, "_work")
    shutil.rmtree(work, ignore_errors=True)
    os.makedirs(work, exist_ok=True)

    vpk_extract(CSS_VPK, ["models/weapons/" + args.view, "models/weapons/" + args.world], work)
    view_mdl = os.path.join(work, "models", "weapons", args.view + ".mdl")
    view_vvd = os.path.join(work, "models", "weapons", args.view + ".vvd")
    world_vvd = os.path.join(work, "models", "weapons", args.world + ".vvd")

    bones = mdl_bones(view_mdl)
    wset = weapon_bone_set(bones)
    _, _, vctr, vsize = vvd_bbox(view_vvd, wset)
    _, _, wctr, wsize = vvd_bbox(world_vvd, None)

    for tag, tlen, ctr, mode in [("view", vsize[1], vctr, "view"), ("world", wsize[1], wctr, "world")]:
        ref = os.path.join(work, tag + "_ref.smd")
        anim = os.path.join(work, tag + "_idle.smd")
        ok, out = make_smd(args.fbx, tlen, ctr, ref, anim, mat="skin",
                           bone_mode=mode, flip_v=False, forced_scale=args.scale)
        if not ok:
            print(out[-800:]); sys.exit(1)

    seqs = ['$sequence "idle" "view_idle.smd" fps 30 activity "ACT_VM_IDLE" 1',
            '$sequence "fire1" "view_idle.smd" fps 20 activity "ACT_VM_PRIMARYATTACK" 1',
            '$sequence "fire2" "view_idle.smd" fps 20',
            '$sequence "fire3" "view_idle.smd" fps 20',
            '$sequence "draw" "view_idle.smd" fps 30 activity "ACT_VM_DRAW" 1',
            '$sequence "reload" "view_idle.smd" fps 37 activity "ACT_VM_RELOAD" 1']
    qc = os.path.join(work, "view.qc")
    write_qc(qc, "weapons/%s.mdl" % args.view, "models/weapons/v_models/replace/",
             "view_ref.smd", seqs)
    if not compile_qc(qc, work):
        sys.exit(1)
    qc = os.path.join(work, "world.qc")
    write_qc(qc, "weapons/%s.mdl" % args.world, "models/weapons/v_models/replace/",
             "world_ref.smd", ['$sequence "idle" "world_idle.smd" fps 1'])
    if not compile_qc(qc, work):
        sys.exit(1)

    addon = os.path.join(args.out, args.name or ("replace_" + args.view))
    shutil.rmtree(addon, ignore_errors=True)
    os.makedirs(os.path.join(addon, "models", "weapons"), exist_ok=True)
    os.makedirs(os.path.join(addon, "materials", "models", "weapons", "v_models", "replace"), exist_ok=True)
    png_to_vtf(args.png, os.path.join(addon, "materials", "models", "weapons", "v_models", "replace", "skin.vtf"))
    gm = os.path.join(work, "game", "models", "weapons")
    for f in os.listdir(gm):
        if f.startswith(args.view + ".") or f.startswith(args.world + "."):
            shutil.copy2(os.path.join(gm, f), os.path.join(addon, "models", "weapons", f))
    write_vmt(os.path.join(addon, "materials", "models", "weapons", "v_models", "replace", "skin.vmt"),
              "models/weapons/v_models/replace/skin")
    print("DONE:", addon)


if __name__ == "__main__":
    main()
