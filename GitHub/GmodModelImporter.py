# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 dy是鹅的不是
#
# 本程序是自由软件：你可以依据 GNU 通用公共许可证第 3 版（或你选择的
# 任何更新版本）重新分发和/或修改它。本程序按"现状"分发，不附带任何
# 明示或暗示的担保。完整条款见项目根目录的 LICENSE 文件。
"""
Gmod 模型导入器（便携整合包）
  选 FBX + PNG -> 自动编译成 Gmod 道具 -> 装进 addons -> 生成菜单可搜索

资源库模式：
  资源库/*.zip 或 资源库/<分类>/*.zip -> 选中资源包 -> 自动填入 FBX/贴图/名字
  外层 zip 里套 zip = 分类文件夹（分类名即外层压缩包名）
"""
import os, sys, re, json, threading, shutil, subprocess, traceback, hashlib
import tkinter as tk
from tkinter import filedialog, messagebox, ttk, simpledialog

HERE = os.path.dirname(os.path.abspath(__file__))
# 两种布局：
#   整合包：<根>/_runtime/GmodModelImporter.py
#   源码库：<根>/GmodModelImporter.py + <根>/tools/
if os.path.basename(HERE).lower() in ("_runtime", "runtime"):
    ROOT = os.path.dirname(HERE)
    RT = HERE
else:
    ROOT = HERE
    RT = HERE
TOOL = os.path.join(RT, "tools")
sys.path.insert(0, TOOL)
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)
import weapon_replace as W
import resource_lib as RL

STEAM_GUESS = [
    r"D:\Steam\steamapps\common\GarrysMod\garrysmod\addons",
    r"C:\Program Files (x86)\Steam\steamapps\common\GarrysMod\garrysmod\addons",
    r"C:\Program Files\Steam\steamapps\common\GarrysMod\garrysmod\addons",
    r"E:\Steam\steamapps\common\GarrysMod\garrysmod\addons",
]

ICON = os.path.join(RT, "icon.png")

# 运行设置（注意：不能写 _runtime/tools/config.json，那个文件每次调用 Blender 都会被覆盖）
SETTINGS_PATH = os.path.join(RT, "settings.json")
LIB_DIR = os.path.join(ROOT, "资源库")
CACHE_DIR = os.path.join(RT, "_library_cache")
LIB_INDEX = os.path.join(CACHE_DIR, "index.json")
PACK_CACHE = os.path.join(CACHE_DIR, "packs")
WORK_TMP = os.path.join(RT, "_work_tmp")
PREVIEW_SCRIPT = os.path.join(TOOL, "preview_render.py")
PREVIEW_CFG = os.path.join(TOOL, "preview_config.json")
PREVIEW_DIR = os.path.join(CACHE_DIR, "preview")
GROUPS_PATH = os.path.join(RT, "model_groups.json")

BLENDER_LOCK = threading.Lock()   # 串行化 Blender 调用（config.json 是共享文件）

DISCLAIMER = (
    "免责声明：整合包由deepseek辅助开发，辅助Gomd内容的产出，"
    "禁止利用整合包进行暴力、淫秽作品开发，导入模型产生产权问题由个人承担，"
    "与整合包作者无关，产生费用仅为支持整合包作者继续开发或感谢，"
    "完全由个人自愿赠与。开始使用该整合包视为同意上述声明。"
    "感谢您的使用。抖音：是鹅的不是 id：85394887560 。"
)


# --------------------------------------------------------------------------
# 基础工具
# --------------------------------------------------------------------------
def load_settings():
    try:
        with open(SETTINGS_PATH, encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, dict):
            return d
    except Exception:
        pass
    return {}


def save_settings(d):
    try:
        os.makedirs(os.path.dirname(SETTINGS_PATH), exist_ok=True)
        tmp = SETTINGS_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
        os.replace(tmp, SETTINGS_PATH)
    except Exception:
        pass


def load_groups():
    try:
        with open(GROUPS_PATH, encoding="utf-8") as f:
            d = json.load(f)
        out = []
        for g in d.get("groups") or []:
            if isinstance(g, dict) and g.get("id") and g.get("name"):
                out.append({
                    "id": str(g["id"]),
                    "name": str(g["name"]),
                    "models": [str(m) for m in (g.get("models") or [])],
                })
        return out
    except Exception:
        return []


def save_groups(groups):
    try:
        with open(GROUPS_PATH, "w", encoding="utf-8") as f:
            json.dump({"version": 1, "groups": groups}, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def lua_str(s):
    return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"') + '"'


def find_addons():
    for p in STEAM_GUESS:
        if os.path.isdir(p):
            return p
    return STEAM_GUESS[0]


def _steam_libraries():
    libs = []
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as k:
            p = winreg.QueryValueEx(k, "SteamPath")[0].replace("/", "\\")
        libs.append(p)
        vdf = os.path.join(p, "steamapps", "libraryfolders.vdf")
        if os.path.isfile(vdf):
            text = open(vdf, encoding="utf-8", errors="replace").read()
            for m in re.finditer(r'"path"\s+"([^"]+)"', text):
                libs.append(m.group(1).replace("\\\\", "\\"))
    except Exception:
        pass
    return libs


def find_gmod_bin():
    """优先用环境变量，其次从 addons 路径反推，再查 Steam 库。"""
    env = os.environ.get("GMOD_BIN")
    if env and os.path.isfile(os.path.join(env, "studiomdl.exe")):
        return env

    cands = []
    for a in [find_addons()] + STEAM_GUESS:
        if a and os.path.isdir(a):
            cands.append(os.path.join(os.path.dirname(os.path.dirname(a)), "bin"))
    for lib in _steam_libraries():
        cands.append(os.path.join(lib, "steamapps", "common", "GarrysMod", "bin"))

    for c in cands:
        if os.path.isfile(os.path.join(c, "studiomdl.exe")):
            return c
    return cands[0] if cands else ""


def find_studiomdl():
    # 兼容仍然带 bin 的旧整合包；新包用本机 GMod
    bundled = os.path.join(RT, "bin", "studiomdl.exe")
    if os.path.isfile(bundled):
        return bundled
    gbin = find_gmod_bin()
    if gbin:
        return os.path.join(gbin, "studiomdl.exe")
    return bundled


def find_blender():
    bundled = os.path.join(RT, "blender", "blender.exe")
    if os.path.isfile(bundled):
        return bundled

    env = os.environ.get("BLENDER_EXE")
    if env and os.path.isfile(env):
        return env

    for lib in _steam_libraries():
        p = os.path.join(lib, "steamapps", "common", "Blender", "blender.exe")
        if os.path.isfile(p):
            return p

    for base in (r"C:\Program Files\Blender Foundation",
                 r"D:\Program Files\Blender Foundation",
                 r"E:\Program Files\Blender Foundation"):
        if os.path.isdir(base):
            for d in sorted(os.listdir(base), reverse=True):
                p = os.path.join(base, d, "blender.exe")
                if os.path.isfile(p):
                    return p
    return bundled


W.BLENDER = find_blender()
W.STUDIOMDL = find_studiomdl()


def sanitize(name):
    s = name.strip().lower()
    s = re.sub(r"[^a-z0-9_]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s or "model"


def _prep_texture(png, work, suffix=""):
    """把贴图统一到最大边 1024（保持比例），避免超大贴图出问题"""
    from PIL import Image
    img = Image.open(png).convert("RGBA")
    w, h = img.size
    if max(w, h) > 1024:
        s = 1024.0 / max(w, h)
        img = img.resize((max(1, int(w * s)), max(1, int(h * s))), Image.LANCZOS)
    out = os.path.join(work, "tex_prep%s.png" % suffix)
    img.save(out)
    return out


def _black_texture(work):
    """材质没有贴图时使用纯黑贴图"""
    from PIL import Image
    p = os.path.join(work, "_black.png")
    if not os.path.exists(p):
        Image.new("RGBA", (64, 64), (0, 0, 0, 255)).save(p)
    return p


# --------------------------------------------------------------------------
# 导入主流程
# --------------------------------------------------------------------------
def do_import(fbx, slots, display_name, target_len, rot_z, addons_dir, log,
              surface="metal", collision=True, pool=None):
    """slots: [(材质名, 贴图路径或 None)]；slot 为空时按 pool 顺序自动分配。"""
    model_id = sanitize(display_name)
    if not model_id:
        raise RuntimeError("模型名字无法生成合法 ID")
    log("模型 ID：%s" % model_id)
    log("显示名：%s" % display_name)

    dst_prop = os.path.join(addons_dir, "%s_prop" % model_id)
    if os.path.isdir(dst_prop):
        raise RuntimeError(
            "已存在同名模型「%s_prop」，禁止重复导入。\n"
            "请先在右侧列表删除旧模型，或修改模型名字。" % model_id)

    work = WORK_TMP
    shutil.rmtree(work, ignore_errors=True)
    os.makedirs(work, exist_ok=True)
    game = os.path.join(work, "game")
    os.makedirs(os.path.join(game, "models", "props"), exist_ok=True)
    gi = os.path.join(game, "gameinfo.txt")
    open(gi, "w").write('"GameInfo"\n{\n game "tmp"\n FileSystem\n {\n  SteamAppId 4000\n'
                        '  SearchPaths\n  {\n   Game |gameinfo_path|.\n  }\n }\n}\n')

    log("[1/5] 正在解析模型数据（多材质）...")
    ref = os.path.join(work, "prop_ref.smd")
    anim = os.path.join(work, "prop_idle.smd")
    mats_json = os.path.join(work, "materials.json")
    with BLENDER_LOCK:
        ok, out = W.make_smd(fbx, target_len, (0, 0, 0), ref, anim, mat="skin",
                             bone_mode="view", flip_v=True, auto_len_axis=True,
                             rot_z=rot_z, materials_out=mats_json)
    if not ok:
        raise RuntimeError("模型解析失败：\n" + out[-1200:])
    mat_names = ["mat0"]
    if os.path.exists(mats_json):
        try:
            mat_names = json.load(open(mats_json, encoding="utf-8")) or ["mat0"]
        except Exception:
            pass
    log("      检测到 %d 个材质：%s" % (len(mat_names), ", ".join(mat_names)))

    log("[2/5] 准备模型描述 ...")
    qc = os.path.join(work, "prop.qc")
    qc_text = (
        '$modelname "props/%s.mdl"\n'
        '$cdmaterials "models/props/%s/"\n'
        '$model "body" "prop_ref.smd"\n'
        '$surfaceprop "%s"\n'
        '$sequence "idle" "prop_idle.smd" fps 1\n' % (model_id, model_id, surface))
    if collision:
        qc_text += '$collisionmodel "prop_ref.smd" { $mass 10 }\n'
    open(qc, "w").write(qc_text)

    log("[3/5] 正在编译模型 ...")
    r = subprocess.run([W.STUDIOMDL, "-game", game, "-nop4", qc],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       creationflags=getattr(W, "CREATE_NO_WINDOW", 0x08000000))
    if "Completed" not in (r.stdout or ""):
        raise RuntimeError("编译失败：\n" + (r.stdout or "")[-1200:])
    log("      OK")

    log("[4/5] 打包 addon ...")
    addon = os.path.join(work, "%s_prop" % model_id)
    os.makedirs(os.path.join(addon, "models", "props"), exist_ok=True)
    matdir = os.path.join(addon, "materials", "models", "props", model_id)
    os.makedirs(matdir, exist_ok=True)
    os.makedirs(os.path.join(addon, "lua", "autorun"), exist_ok=True)

    gm = os.path.join(game, "models", "props")
    for f in os.listdir(gm):
        if f.startswith(model_id + "."):
            shutil.copy2(os.path.join(gm, f), os.path.join(addon, "models", "props", f))

    # 材质 -> 贴图（显式映射；未绑定用纯黑）
    if not slots and pool:
        slots = [(m, pool[i] if i < len(pool) else None) for i, m in enumerate(mat_names)]
    slot_map = {}
    for m, t in (slots or []):
        if m:
            slot_map[m] = t

    prepared = {}

    def prep(src):
        key = os.path.abspath(src)
        if key not in prepared:
            prepared[key] = _prep_texture(src, work, suffix="_%d" % len(prepared))
        return prepared[key]

    for i, mname in enumerate(mat_names):
        src = slot_map.get(mname)
        is_black = not (src and os.path.isfile(src))
        if is_black:
            src = _black_texture(work)
        W.png_to_vtf(prep(src), os.path.join(matdir, mname + ".vtf"))
        W.write_vmt(os.path.join(matdir, mname + ".vmt"),
                    "models/props/%s/%s" % (model_id, mname))
        log("      材质 %s -> %s" % (mname, "纯黑（未绑定贴图）" if is_black else os.path.basename(src)))

    safe = display_name.replace('"', "'")
    open(os.path.join(addon, "lua", "autorun", "%s_spawnlist.lua" % model_id),
         "w", encoding="utf-8").write(
        'local NAME = "%s"\n'
        'local MODEL = "models/props/%s.mdl"\n'
        'hook.Add("PopulatePropMenu", "%s_add", function()\n'
        '    if PropGroups and PropGroups["%s"] then return end\n'
        '    local c = {}\n'
        '    table.insert(c, { type = "model", model = MODEL })\n'
        '    spawnmenu.AddPropCategory("%s", NAME, c, "icon16/box.png")\n'
        'end)\n'
        'if list then\n'
        '    list.Set("SpawnableEntities", "%s", {\n'
        '        PrintName = NAME, ClassName = "prop_physics",\n'
        '        Spawnable = true, Model = MODEL })\n'
        'end\n' % (safe, model_id, model_id, model_id, model_id, model_id))

    log("[5/5] 安装到 Gmod ...")
    os.makedirs(addons_dir, exist_ok=True)
    dst = dst_prop
    if os.path.exists(dst):
        raise RuntimeError("已存在同名模型「%s_prop」，禁止重复导入。" % model_id)
    shutil.copytree(addon, dst)
    log("      已安装：%s" % dst)
    shutil.rmtree(work, ignore_errors=True)
    return model_id


# --------------------------------------------------------------------------
# 主题
# --------------------------------------------------------------------------
# --------------------------------------------------------------------------
# Windows 文件拖放（资源管理器 -> 窗口，WM_DROPFILES）
# 纯 ctypes 子类化窗口，不依赖 tkdnd，整个整合包依旧零额外依赖
# --------------------------------------------------------------------------
class FileDropTarget:
    WM_DROPFILES = 0x0233
    GWLP_WNDPROC = -4

    def __init__(self, root, on_files):
        self.root = root
        self.on_files = on_files
        self.ok = False
        self.hwnd = None
        self.old_proc = None
        self._proc_ref = None
        try:
            import ctypes
            from ctypes import wintypes
            self.ct = ctypes
            self.user32 = ctypes.WinDLL("user32", use_last_error=True)
            self.shell32 = ctypes.WinDLL("shell32", use_last_error=True)

            self.user32.GetParent.restype = wintypes.HWND
            self.user32.GetParent.argtypes = [wintypes.HWND]
            child = wintypes.HWND(root.winfo_id())
            hwnd = self.user32.GetParent(child) or child
            self.hwnd = hwnd

            LRESULT = ctypes.c_ssize_t
            proto = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT,
                                       wintypes.WPARAM, wintypes.LPARAM)
            self._proto = proto
            self._proc_ref = proto(self._proc)

            setter = getattr(self.user32, "SetWindowLongPtrW", None) or self.user32.SetWindowLongW
            setter.restype = ctypes.c_void_p
            setter.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
            old = setter(wintypes.HWND(hwnd), self.GWLP_WNDPROC, self._proc_ref)
            if not old:
                raise ctypes.WinError(ctypes.get_last_error())
            self.old_proc = old

            self.user32.CallWindowProcW.restype = LRESULT
            self.user32.CallWindowProcW.argtypes = [ctypes.c_void_p, wintypes.HWND,
                                                    wintypes.UINT, wintypes.WPARAM,
                                                    wintypes.LPARAM]
            self.shell32.DragAcceptFiles.argtypes = [wintypes.HWND, wintypes.BOOL]
            self.shell32.DragAcceptFiles(wintypes.HWND(hwnd), True)
            self.shell32.DragQueryFileW.restype = wintypes.UINT
            self.shell32.DragQueryFileW.argtypes = [ctypes.c_void_p, wintypes.UINT,
                                                    ctypes.c_wchar_p, wintypes.UINT]
            self.shell32.DragFinish.argtypes = [ctypes.c_void_p]
            self.ok = True
        except Exception:
            self.ok = False

    def _query(self, hdrop):
        n = self.shell32.DragQueryFileW(hdrop, 0xFFFFFFFF, None, 0)
        out = []
        for i in range(n):
            ln = self.shell32.DragQueryFileW(hdrop, i, None, 0)
            if ln <= 0:
                continue
            buf = self.ct.create_unicode_buffer(ln + 1)
            self.shell32.DragQueryFileW(hdrop, i, buf, ln + 1)
            out.append(buf.value)
        return out

    def _proc(self, hwnd, msg, wparam, lparam):
        if msg == self.WM_DROPFILES:
            paths = []
            try:
                paths = self._query(wparam)
            except Exception:
                paths = []
            try:
                self.shell32.DragFinish(wparam)
            except Exception:
                pass
            if paths:
                try:
                    self.root.after(0, lambda p=list(paths): self.on_files(p))
                except Exception:
                    pass
            return 0
        return self.user32.CallWindowProcW(self.old_proc, hwnd, msg, wparam, lparam)


THEMES = {
    "dark": {
        "bg": "#1e1e1e", "fg": "#a0a0a0", "title_fg": "#c8c8c8", "tip": "#7a7a7a",
        "entry_bg": "#262626", "entry_fg": "#c8c8c8",
        "btn_bg": "#2b2b2b", "btn_fg": "#a0a0a0",
        "btn_act_bg": "#383838", "btn_act_fg": "#cccccc",
        "list_bg": "#242424", "list_fg": "#a8a8a8",
        "list_sel_bg": "#3a3a3a", "list_sel_fg": "#e0e0e0",
        "log_bg": "#191919", "log_fg": "#8a9a9a",
        "head_bg": "#2b2b2b", "head_fg": "#8a8a8a",
        "accent_bg": "#2f5a35", "accent_fg": "#cfe0d2",
        "accent_act_bg": "#37663d", "accent_act_fg": "#e0f0e4",
        "danger_bg": "#4a2626", "danger_fg": "#c9a8a8",
        "danger_act_bg": "#5a2e2e", "danger_act_fg": "#e0c0c0",
        "border": "#333333",
        "warn_fg": "#e06c6c", "warn_bg": "#3a2424", "ok_fg": "#7fbf7f",
        "chip_bg": "#2f2f2f", "chip_fg": "#cccccc", "slot_alt": "#232323",
        "cat_fg": "#c8b98a",
    },
    "light": {
        "bg": "#f4f4f4", "fg": "#4a4a4a", "title_fg": "#222222", "tip": "#777777",
        "entry_bg": "#ffffff", "entry_fg": "#333333",
        "btn_bg": "#e6e6e6", "btn_fg": "#3f3f3f",
        "btn_act_bg": "#d6d6d6", "btn_act_fg": "#111111",
        "list_bg": "#ffffff", "list_fg": "#333333",
        "list_sel_bg": "#c8d8ea", "list_sel_fg": "#111111",
        "log_bg": "#fafafa", "log_fg": "#555555",
        "head_bg": "#e6e6e6", "head_fg": "#555555",
        "accent_bg": "#3f8a4a", "accent_fg": "#ffffff",
        "accent_act_bg": "#4f9a5a", "accent_act_fg": "#ffffff",
        "danger_bg": "#c25555", "danger_fg": "#ffffff",
        "danger_act_bg": "#b04a4a", "danger_act_fg": "#ffffff",
        "border": "#cccccc",
        "warn_fg": "#c0392b", "warn_bg": "#fbeaea", "ok_fg": "#2e7d32",
        "chip_bg": "#ffffff", "chip_fg": "#333333", "slot_alt": "#efefef",
        "cat_fg": "#8a6d1f",
    },
}

# 碰撞/表面类型：显示名 -> Source $surfaceprop（"none" 表示不生成碰撞模型）
SURFACES = [
    ("金属", "metal"),
    ("金属桶", "metal_barrel"),
    ("木材", "wood"),
    ("混凝土", "concrete"),
    ("玻璃", "glass"),
    ("塑料", "plastic"),
    ("布料", "cloth"),
    ("地毯", "carpet"),
    ("瓦片", "tile"),
    ("肉体", "flesh"),
    ("沙土", "sand"),
    ("雪", "snow"),
    ("泥土", "dirt"),
    ("草地", "grass"),
    ("水", "water"),
    ("无碰撞", "none"),
]
SURFACE_MAP = dict(SURFACES)


class App:
    def __init__(self, root):
        self.root = root
        root.title("Gmod 模型导入器")
        self.theme = "dark"

        st = load_settings()
        self.library_root = st.get("library") or LIB_DIR
        try:
            os.makedirs(self.library_root, exist_ok=True)
        except Exception:
            self.library_root = LIB_DIR
            os.makedirs(self.library_root, exist_ok=True)

        # 跨主题切换保留的状态
        self.fbx = tk.StringVar()
        self.name = tk.StringVar()
        self.length = tk.StringVar(value=str(st.get("length", "34")))
        self.rot = tk.StringVar(value=str(st.get("rot", "90")))
        self.surface = tk.StringVar(value=st.get("surface", "金属"))
        self.addons = tk.StringVar(value=st.get("addons") or find_addons())
        self.warn = tk.StringVar(value="")

        self.slots = []          # [{"mat": 材质名, "tex": 贴图路径或 None}]
        self.pool = []           # 贴图池（绝对路径，文件夹顺序）
        self.cur_pack = None
        self.lib_nodes = []
        self.node_by_iid = {}
        self.name_conflict = False
        self.busy = False
        self.preparing = False
        self.batch_running = False
        self.batch_stop = False
        self.scanning = False
        self._scan_again = False
        self._drag = None
        self._pending_select_name = None
        self._preparing_node = None
        self._preview_imgs = {}
        self._lib_drag = None
        self._deps_missing = False
        self.groups = load_groups()
        self.model_rows = {}
        self._right_drag = None

        self.build_ui()
        self.name.trace_add("write", lambda *a: self.check_name())
        self.addons.trace_add("write", lambda *a: self.check_name())
        self.check_name()
        self.refresh_library()
        self.scan_models()
        self._sync_studiomdl()
        self.root.after(150, self._install_drop)

        if not os.path.isfile(W.BLENDER):
            self.log("[警告] 未找到 Blender：请安装 Blender，或设置环境变量 BLENDER_EXE")
        if not os.path.isfile(W.STUDIOMDL):
            self.log("[警告] 未找到 studiomdl：请确认 Garry's Mod 安装位置")
        self.root.after(300, self._check_deps)

        root.geometry(st.get("geometry") or "1300x900")
        root.minsize(1140, 800)
        root.protocol("WM_DELETE_WINDOW", self.on_close)

    # ---------- 主题 ----------
    def _check_deps(self):
        """启动时检查 Pillow / numpy，缺了就明确提示，避免导入到一半才报错"""
        import importlib.util
        missing = [m for m in ("PIL", "numpy") if importlib.util.find_spec(m) is None]
        if not missing:
            self._deps_missing = False
            return

        names = "、".join("Pillow" if m == "PIL" else "numpy" for m in missing)
        self._deps_missing = True
        self.log("[缺少依赖] " + names + "（导入功能已禁用）")
        self.refresh_start_state()
        messagebox.showwarning(
            "缺少 Python 依赖",
            "缺少：%s\n\n"
            "请在项目目录打开命令行，运行：\n\n"
            "    py -3 -m pip install -r requirements.txt\n\n"
            "或者使用自带运行环境的便携版（_runtime\\python）。\n"
            "安装完成后重新启动导入器。" % names)

    def apply_ttk_style(self, T):
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("Treeview", background=T["list_bg"], fieldbackground=T["list_bg"],
                        foreground=T["list_fg"], borderwidth=0, rowheight=26,
                        bordercolor=T["bg"], lightcolor=T["bg"], darkcolor=T["bg"],
                        relief="flat")
        style.configure("Treeview.Heading", background=T["head_bg"],
                        foreground=T["head_fg"], relief="flat", borderwidth=0)
        style.map("Treeview", background=[("selected", T["list_sel_bg"])],
                  foreground=[("selected", T["list_sel_fg"])])
        style.configure("TCombobox", fieldbackground=T["entry_bg"], background=T["btn_bg"],
                        foreground=T["entry_fg"], arrowcolor=T["fg"],
                        bordercolor=T["border"], lightcolor=T["border"],
                        darkcolor=T["border"])
        self.root.option_add("*Listbox*background", T["entry_bg"])
        self.root.option_add("*Listbox*foreground", T["entry_fg"])
        self.root.option_add("*Listbox*selectBackground", T["list_sel_bg"])
        self.root.option_add("*Listbox*selectForeground", T["list_sel_fg"])

    def toggle_theme(self):
        old = ""
        try:
            old = self.logbox.get("1.0", "end-1c")
        except Exception:
            pass
        self.theme = "light" if self.theme == "dark" else "dark"
        self.build_ui()
        if old:
            try:
                self.logbox.delete("1.0", "end")
                self.logbox.insert("end", old)
            except Exception:
                pass
        self.scan_models()
        self.check_name()

    # ---------- UI ----------
    def build_ui(self):
        for w in getattr(self, "_built", []):
            try:
                w.destroy()
            except Exception:
                pass
        T = THEMES[self.theme]
        self.apply_ttk_style(T)
        self.root.configure(bg=T["bg"])

        content = tk.Frame(self.root, bg=T["bg"])
        content.pack(fill="both", expand=True)
        self._built = [content]

        # 顶栏
        top = tk.Frame(content, bg=T["bg"])
        top.pack(fill="x", padx=12, pady=(10, 0))
        tk.Label(top, text="Gmod 模型导入器", fg=T["title_fg"], bg=T["bg"],
                 font=("微软雅黑", 12, "bold")).pack(side="left")
        tk.Button(top, text="切换主题", command=self.toggle_theme,
                  bg=T["btn_bg"], fg=T["btn_fg"], relief="flat", bd=0,
                  activebackground=T["btn_act_bg"], activeforeground=T["btn_act_fg"],
                  padx=12).pack(side="right")

        main = tk.Frame(content, bg=T["bg"])
        main.pack(fill="both", expand=True, padx=12, pady=10)

        # ============ 左：资源库 ============
        left = tk.Frame(main, bg=T["bg"], width=300)
        left.pack(side="left", fill="y", padx=(0, 10), pady=(0, 6))
        left.pack_propagate(False)

        lh = tk.Frame(left, bg=T["bg"])
        lh.pack(fill="x")
        tk.Label(lh, text="资源库", fg=T["title_fg"], bg=T["bg"],
                 font=("微软雅黑", 10, "bold")).pack(side="left")
        for txt, cmd in (("新建", self.new_library_folder), ("目录", self.change_library),
                         ("打开", self.open_library), ("刷新", self.refresh_library)):
            tk.Button(lh, text=txt, command=cmd, bg=T["btn_bg"], fg=T["btn_fg"],
                      relief="flat", bd=0, activebackground=T["btn_act_bg"],
                      activeforeground=T["btn_act_fg"], padx=6).pack(side="right", padx=1)

        drop = tk.Frame(left, bg=T["entry_bg"], highlightthickness=1,
                        highlightbackground=T["border"], highlightcolor=T["border"])
        drop.pack(fill="x", pady=(4, 6))
        dz = tk.Label(drop, text="＋ 把 .zip 拖到这里，或点我选择\n可拖文件夹（作为分类）· 仅支持 zip · 名称需英文",
                      fg=T["tip"], bg=T["entry_bg"], font=("微软雅黑", 8),
                      justify="left", anchor="w", padx=8, pady=8, cursor="hand2",
                      wraplength=270)
        dz.pack(fill="x")
        for w in (drop, dz):
            w.bind("<Button-1>", lambda e: self.import_zips())
        dz.bind("<Enter>", lambda e: drop.configure(highlightbackground=T["accent_bg"]))
        dz.bind("<Leave>", lambda e: drop.configure(highlightbackground=T["border"]))

        lf = tk.Frame(left, bg=T["bg"])
        lf.pack(fill="both", expand=True)
        self.lib_tree = ttk.Treeview(lf, columns=("x",), show="tree", selectmode="browse")
        self.lib_tree.column("#0", width=270, stretch=True)
        self.lib_tree.tag_configure("bad", foreground=T["warn_fg"])
        self.lib_tree.tag_configure("cat", foreground=T["cat_fg"])
        self.lib_tree.tag_configure("warn", foreground=T["tip"])
        sb = tk.Scrollbar(lf, command=self.lib_tree.yview, bg=T["btn_bg"],
                          troughcolor=T["bg"], bd=0, relief="flat")
        self.lib_tree.configure(yscrollcommand=sb.set)
        self.lib_tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.lib_tree.bind("<<TreeviewSelect>>", self.on_lib_select)
        self.lib_tree.bind("<Double-1>", self.on_lib_activate)
        self.lib_tree.bind("<ButtonPress-1>", self._lib_press, add="+")
        self.lib_tree.bind("<B1-Motion>", self._lib_motion, add="+")
        self.lib_tree.bind("<ButtonRelease-1>", self._lib_release, add="+")

        self.lib_status = tk.Label(left, text="正在扫描资源库…", fg=T["tip"], bg=T["bg"],
                                   font=("微软雅黑", 8), anchor="nw", justify="left",
                                   wraplength=288, height=3)
        self.lib_status.pack(fill="x", pady=(4, 2))

        # 模型预览（无光照，左右视图）
        pv = tk.Frame(left, bg=T["bg"])
        pv.pack(fill="x", pady=(2, 4))
        ph = tk.Frame(pv, bg=T["bg"])
        ph.pack(fill="x")
        tk.Label(ph, text="模型预览", fg=T["fg"], bg=T["bg"],
                 font=("微软雅黑", 9, "bold")).pack(side="left")
        self.preview_status = tk.Label(ph, text="无光照 · 左/右", fg=T["tip"], bg=T["bg"],
                                       font=("微软雅黑", 8))
        self.preview_status.pack(side="left", padx=6)
        self.preview_btn = tk.Button(ph, text="预览", command=self.preview_model,
                                     bg=T["btn_bg"], fg=T["btn_fg"], relief="flat", bd=0,
                                     activebackground=T["btn_act_bg"],
                                     activeforeground=T["btn_act_fg"], padx=10)
        self.preview_btn.pack(side="right")

        pbx = tk.Frame(pv, bg=T["bg"])
        pbx.pack(fill="x", pady=(2, 0))

        def _preview_box(text):
            box = tk.Frame(pbx, bg=T["log_bg"], width=134, height=132,
                           highlightthickness=1, highlightbackground=T["border"])
            box.pack_propagate(False)
            box.pack(side="left", expand=True, padx=1)
            lbl = tk.Label(box, bg=T["log_bg"], fg=T["tip"], text=text,
                           font=("微软雅黑", 8))
            lbl.pack(fill="both", expand=True)
            return lbl

        self.preview_left = _preview_box("左侧")
        self.preview_right = _preview_box("右侧")

        lb = tk.Frame(left, bg=T["bg"])
        lb.pack(fill="x", pady=(2, 0))
        tk.Button(lb, text="导入压缩包", command=self.import_zips,
                  bg=T["btn_bg"], fg=T["btn_fg"], relief="flat", bd=0,
                  highlightthickness=0,
                  activebackground=T["btn_act_bg"], activeforeground=T["btn_act_fg"],
                  padx=8).pack(side="left", fill="x", expand=True, padx=(0, 2))
        tk.Button(lb, text="删除资源包", command=self.delete_pack,
                  bg=T["danger_bg"], fg=T["danger_fg"], relief="flat", bd=0,
                  highlightthickness=0,
                  activebackground=T["danger_act_bg"], activeforeground=T["danger_act_fg"],
                  padx=8).pack(side="left", fill="x", expand=True)

        self.batch_btn = tk.Button(left, text="批量导入此分类", command=self.batch_import,
                                   bg=T["accent_bg"], fg=T["accent_fg"], relief="flat", bd=0,
                                   highlightthickness=0,
                                   activebackground=T["accent_act_bg"],
                                   activeforeground=T["accent_act_fg"], pady=3)
        self.batch_btn.pack(fill="x", pady=(4, 0))

        # ============ 中：导入表单 ============
        mid = tk.Frame(main, bg=T["bg"])
        mid.pack(side="left", fill="both", expand=True)

        self.pack_label = tk.Label(mid, text="当前资源包：未选择", fg=T["tip"], bg=T["bg"],
                                   font=("微软雅黑", 9), anchor="w")
        self.pack_label.pack(fill="x")

        frm = tk.Frame(mid, bg=T["bg"])
        frm.pack(fill="x")

        def row(label, var, browse=None, width=11):
            r = tk.Frame(frm, bg=T["bg"])
            r.pack(fill="x", pady=3)
            tk.Label(r, text=label, fg=T["fg"], bg=T["bg"], width=width, anchor="w").pack(side="left")
            e = tk.Entry(r, textvariable=var, bg=T["entry_bg"], fg=T["entry_fg"],
                         insertbackground=T["fg"], relief="flat", highlightthickness=1,
                         highlightbackground=T["border"], highlightcolor=T["border"])
            e.pack(side="left", fill="x", expand=True, padx=6, ipady=4)
            if browse:
                tk.Button(r, text="浏览", command=browse, bg=T["btn_bg"], fg=T["btn_fg"],
                          relief="flat", activebackground=T["btn_act_bg"],
                          activeforeground=T["btn_act_fg"], bd=0, padx=10).pack(side="left")
            return e

        self.fbx_entry = row("FBX 文件", self.fbx, self.pick_fbx)

        # 材质 / 贴图配对
        head = tk.Frame(frm, bg=T["bg"])
        head.pack(fill="x", pady=(6, 2))
        tk.Label(head, text="材质 / 贴图配对", fg=T["fg"], bg=T["bg"],
                 font=("微软雅黑", 9, "bold")).pack(side="left")
        tk.Label(head, text="把下方贴图拖到材质行 · 双击解绑 · 未绑定=纯黑",
                 fg=T["tip"], bg=T["bg"], font=("微软雅黑", 8)).pack(side="left", padx=8)

        sw = tk.Frame(frm, bg=T["bg"])
        sw.pack(fill="x")
        self.slot_canvas = tk.Canvas(sw, bg=T["bg"], height=104, highlightthickness=0, bd=0)
        self.slot_canvas.pack(side="left", fill="x", expand=True)
        self.slot_frame = tk.Frame(self.slot_canvas, bg=T["bg"])
        self._slot_win = self.slot_canvas.create_window((0, 0), window=self.slot_frame, anchor="nw")
        slot_sb = tk.Scrollbar(sw, command=self.slot_canvas.yview, bg=T["btn_bg"],
                               troughcolor=T["bg"], bd=0, relief="flat")
        slot_sb.pack(side="right", fill="y")
        self.slot_canvas.configure(yscrollcommand=slot_sb.set)

        def _slot_conf(e):
            self.slot_canvas.itemconfig(self._slot_win, width=e.width)
            self.slot_frame.update_idletasks()
            self.slot_canvas.configure(scrollregion=self.slot_canvas.bbox("all"))

        self.slot_canvas.bind("<Configure>", _slot_conf)
        self.slot_frame.bind("<Configure>", lambda e: self.slot_canvas.configure(
            scrollregion=self.slot_canvas.bbox("all")))
        self.slot_canvas.bind("<MouseWheel>", self._slot_wheel)
        self.slot_frame.bind("<MouseWheel>", self._slot_wheel)

        tk.Label(frm, text="贴图池（文件夹顺序，可拖动 / 双击填入空槽）", fg=T["tip"], bg=T["bg"],
                 font=("微软雅黑", 8), anchor="w").pack(fill="x", pady=(4, 1))
        pf = tk.Frame(frm, bg=T["bg"])
        pf.pack(fill="x")
        self.pool_list = tk.Listbox(pf, height=4, bg=T["entry_bg"], fg=T["entry_fg"],
                                    selectbackground=T["list_sel_bg"],
                                    selectforeground=T["list_sel_fg"],
                                    relief="flat", highlightthickness=1,
                                    highlightbackground=T["border"],
                                    highlightcolor=T["border"], bd=0, activestyle="none",
                                    font=("Consolas", 9))
        self.pool_list.pack(side="left", fill="x", expand=True)
        self.pool_list.bind("<ButtonPress-1>", self._pool_press)
        self.pool_list.bind("<B1-Motion>", self._drag_move)
        self.pool_list.bind("<ButtonRelease-1>", self._drag_end)
        self.pool_list.bind("<Double-1>", self._pool_double)

        pb = tk.Frame(pf, bg=T["bg"])
        pb.pack(side="right", fill="y", padx=(4, 0))
        for txt, cmd in (("添加贴图", self.add_textures),
                         ("按名称配对", self.pair_by_name),
                         ("按顺序填充", self.fill_in_order),
                         ("读取材质", self.detect_materials),
                         ("全部解绑", self.clear_slots)):
            tk.Button(pb, text=txt, command=cmd, bg=T["btn_bg"], fg=T["btn_fg"],
                      relief="flat", activebackground=T["btn_act_bg"],
                      activeforeground=T["btn_act_fg"], bd=0, padx=8, width=9,
                      font=("微软雅黑", 8)).pack(fill="x", pady=1)

        self.name_entry = row("模型名字", self.name)
        self.dup_label = tk.Label(frm, text="", fg=T["warn_fg"], bg=T["bg"],
                                  font=("微软雅黑", 8), anchor="w")
        self.dup_label.pack(fill="x", padx=(88, 0))

        rr = tk.Frame(frm, bg=T["bg"])
        rr.pack(fill="x", pady=3)
        tk.Label(rr, text="目标长度", fg=T["fg"], bg=T["bg"], width=11, anchor="w").pack(side="left")
        tk.Entry(rr, textvariable=self.length, bg=T["entry_bg"], fg=T["entry_fg"],
                 insertbackground=T["fg"], relief="flat", highlightthickness=1,
                 highlightbackground=T["border"], highlightcolor=T["border"],
                 width=8).pack(side="left", padx=(6, 16), ipady=4)
        tk.Label(rr, text="旋转角度", fg=T["fg"], bg=T["bg"], width=9, anchor="w").pack(side="left")
        tk.Entry(rr, textvariable=self.rot, bg=T["entry_bg"], fg=T["entry_fg"],
                 insertbackground=T["fg"], relief="flat", highlightthickness=1,
                 highlightbackground=T["border"], highlightcolor=T["border"],
                 width=8).pack(side="left", padx=(6, 0), ipady=4)

        # 碰撞类型
        tk.Label(frm, text="碰撞类型", fg=T["fg"], bg=T["bg"], anchor="w").pack(fill="x", pady=(4, 2))
        sf = tk.Frame(frm, bg=T["bg"])
        sf.pack(fill="x", pady=2)
        self._surf_btns = {}
        for i, (name, _val) in enumerate(SURFACES):
            b = tk.Button(sf, text=name, command=lambda n=name: self.pick_surface(n),
                          bg=T["btn_bg"], fg=T["btn_fg"], relief="flat", bd=0,
                          activebackground=T["btn_act_bg"],
                          activeforeground=T["btn_act_fg"],
                          font=("微软雅黑", 9), height=1)
            b.grid(row=i // 8, column=i % 8, padx=2, pady=2, sticky="ew")
            self._surf_btns[name] = b
        for c in range(8):
            sf.grid_columnconfigure(c, weight=1)
        self.refresh_surface_buttons()

        row("Gmod 目录", self.addons, self.pick_addons)

        self.start_btn = tk.Button(mid, text="开始导入", command=self.start,
                                   bg=T["accent_bg"], fg=T["accent_fg"], relief="flat", bd=0,
                                   activebackground=T["accent_act_bg"],
                                   activeforeground=T["accent_act_fg"],
                                   font=("微软雅黑", 11, "bold"), height=2)
        self.start_btn.pack(fill="x", pady=(8, 6))

        # 日志
        tk.Label(mid, text="运行日志", fg=T["tip"], bg=T["bg"],
                 font=("微软雅黑", 9), anchor="w").pack(fill="x")
        self.logbox = tk.Text(mid, bg=T["log_bg"], fg=T["log_fg"], relief="flat",
                              font=("Consolas", 9), height=8, bd=0,
                              insertbackground=T["fg"])
        self.logbox.pack(fill="both", expand=True, pady=(2, 0))
        self.logbox.insert("end", DISCLAIMER + "\n" + "-" * 44 + "\n\n")

        # ============ 右：已导入模型 ============
        right = tk.Frame(main, bg=T["bg"], width=240)
        right.pack(side="right", fill="y", padx=(10, 0))
        right.pack_propagate(False)

        tk.Label(right, text="已导入模型", fg=T["tip"], bg=T["bg"],
                 font=("微软雅黑", 9), anchor="w").pack(fill="x", pady=(0, 4))

        tf = tk.Frame(right, bg=T["bg"])
        tf.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(tf, columns=("name",), show="tree", height=18)
        self.tree.column("#0", width=222, anchor="w")
        self.tree.tag_configure("cat", foreground=T["cat_fg"])
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<Double-1>", self.on_model_activate)
        self.tree.bind("<ButtonPress-1>", self._right_press, add="+")
        self.tree.bind("<B1-Motion>", self._right_motion, add="+")
        self.tree.bind("<ButtonRelease-1>", self._right_release, add="+")

        gb = tk.Frame(right, bg=T["bg"])
        gb.pack(fill="x", pady=(6, 0))
        tk.Button(gb, text="新建文件夹", command=self.new_model_group,
                  bg=T["btn_bg"], fg=T["btn_fg"], relief="flat", bd=0,
                  activebackground=T["btn_act_bg"], activeforeground=T["btn_act_fg"],
                  padx=10).pack(fill="x")

        btns = tk.Frame(right, bg=T["bg"])
        btns.pack(fill="x", pady=(4, 0))
        tk.Button(btns, text="删除选中", command=self.delete_selected,
                  bg=T["danger_bg"], fg=T["danger_fg"], relief="flat", bd=0,
                  activebackground=T["danger_act_bg"], activeforeground=T["danger_act_fg"],
                  padx=10).pack(side="left", fill="x", expand=True, padx=(0, 4))
        tk.Button(btns, text="刷新", command=self.scan_models,
                  bg=T["btn_bg"], fg=T["btn_fg"], relief="flat", bd=0,
                  activebackground=T["btn_act_bg"], activeforeground=T["btn_act_fg"],
                  padx=10).pack(side="left", fill="x", expand=True)

        # 重建后恢复动态内容
        self.render_library()
        self.render_slots()
        self.refresh_pool()
        self.refresh_start_state()
        if getattr(self, "_preview_imgs", None):
            for name, lbl in (("left", self.preview_left), ("right", self.preview_right)):
                ph = self._preview_imgs.get(name)
                if ph:
                    lbl.config(image=ph, text="")
        if self.cur_pack is not None:
            self.pack_label.configure(text="当前资源包：%s" % self.cur_pack["name"])

    # ---------- 通用 ----------
    def ui(self, fn, *a, **kw):
        try:
            self.root.after(0, lambda: fn(*a, **kw))
        except Exception:
            pass

    def log(self, msg):
        if threading.current_thread() is threading.main_thread():
            self._log_write(msg)
        else:
            try:
                self.root.after(0, lambda m=msg: self._log_write(m))
            except Exception:
                pass

    def _log_write(self, msg):
        try:
            if not self.logbox.winfo_exists():
                return
            self.logbox.insert("end", str(msg) + "\n")
            self.logbox.see("end")
        except Exception:
            pass

    def pick_surface(self, name):
        self.surface.set(name)
        self.refresh_surface_buttons()

    def refresh_surface_buttons(self):
        T = THEMES[self.theme]
        cur = self.surface.get()
        for name, b in getattr(self, "_surf_btns", {}).items():
            if name == cur:
                b.configure(bg=T["accent_bg"], fg=T["accent_fg"],
                            activebackground=T["accent_act_bg"],
                            activeforeground=T["accent_act_fg"])
            else:
                b.configure(bg=T["btn_bg"], fg=T["btn_fg"],
                            activebackground=T["btn_act_bg"],
                            activeforeground=T["btn_act_fg"])

    # ---------- 资源库 ----------
    def _install_drop(self):
        self.drop = FileDropTarget(self.root, self._on_files_dropped)
        if self.drop.ok:
            self.log("[拖放] 已启用：把 zip 拖进窗口左侧即可导入资源库")
        else:
            self.log("[拖放] 未能注册系统拖放，请使用「导入压缩包」按钮")

    def _on_files_dropped(self, paths):
        if self.batch_running or self.busy or self.preparing:
            self.log("正在处理，请稍候再拖入文件")
            return
        target = self.library_root
        node = self.current_node()
        if node is not None and node["type"] == "category" and node.get("dir"):
            target = node["dir"]
        added, rejected = [], []
        for p in paths:
            try:
                if os.path.isdir(p):
                    name = os.path.basename(p.rstrip("\\/"))
                    try:
                        zips = [f for f in os.listdir(p) if f.lower().endswith(".zip")]
                    except Exception:
                        zips = []
                    if not zips:
                        rejected.append("%s：文件夹里没有 zip" % name)
                        continue
                    dst = os.path.join(self.library_root, name)
                    if os.path.exists(dst):
                        rejected.append("%s：资源库里已有同名分类" % name)
                        continue
                    shutil.copytree(p, dst)
                    added.append("%s（分类，%d 个包）" % (name, len(zips)))
                    continue

                base = os.path.basename(p)
                ext = os.path.splitext(base)[1].lower()
                if ext != ".zip":
                    rejected.append("%s：只支持 .zip（rar/7z 请先转成 zip）" % base)
                    continue
                stem = os.path.splitext(base)[0]
                prob = RL.name_problem(stem, "资源包名")
                if prob:
                    rejected.append("%s：%s" % (base, prob))
                    continue
                dst = os.path.join(target, base)
                if os.path.exists(dst):
                    rejected.append("%s：资源库里已有同名文件" % base)
                    continue
                shutil.copy2(p, dst)
                added.append(base)
                self._pending_select_name = stem
            except Exception as e:
                rejected.append("%s：%s" % (os.path.basename(p), e))
        if added:
            self.log("拖入资源库：%s" % "、".join(added[:6]))
        if rejected:
            messagebox.showwarning("部分未导入", "\n".join(rejected[:12]))
        if added:
            self.refresh_library()

    def refresh_library(self, force=False):
        if self.scanning:
            self._scan_again = True
            return
        self.scanning = True
        try:
            self.lib_status.configure(text="正在扫描资源库…")
        except Exception:
            pass
        root = self.library_root

        def work():
            try:
                nodes = RL.scan_library(root, LIB_INDEX, log=self.log)
            except Exception as e:
                self.log("[资源库] 扫描失败：%s" % e)
                nodes = []
            self.ui(self._library_loaded, nodes)

        threading.Thread(target=work, daemon=True).start()

    def _library_loaded(self, nodes):
        self.lib_nodes = nodes
        self.scanning = False
        self.render_library()
        packs = RL.flatten_packs(nodes)
        bad = sum(1 for p in packs if p["bad"])
        pending = getattr(self, "_pending_select_name", None)
        if pending:
            self._pending_select_name = None
            for p in packs:
                if p["name"] == pending:
                    if p["bad"]:
                        self.log("[拖放] 资源包「%s」不合规：%s" % (pending, "；".join(p["bad"])))
                        self.lib_status.configure(text="[!] 拖入的包不合规：\n" + "；".join(p["bad"][:3]))
                    else:
                        self.select_pack(p)
                        self._highlight_pack(p)
                    break
        other = 0
        try:
            for name in os.listdir(self.library_root):
                if os.path.splitext(name)[1].lower() in (".rar", ".7z", ".tar", ".gz", ".bz2"):
                    other += 1
        except Exception:
            pass
        text = "共 %d 个资源包%s" % (len(packs), ("，%d 个不合规（红字）" % bad) if bad else "")
        if other:
            text += "\n另有 %d 个非 zip 压缩包未识别（请转成 .zip）" % other
        self.lib_status.configure(text=text)

        if getattr(self, "_scan_again", False):
            self._scan_again = False
            self.refresh_library()

    def render_library(self):
        try:
            self.lib_tree.delete(*self.lib_tree.get_children())
        except Exception:
            return
        self.node_by_iid = {}
        T = THEMES[self.theme]
        self.lib_tree.tag_configure("bad", foreground=T["warn_fg"])
        self.lib_tree.tag_configure("cat", foreground=T["cat_fg"])
        self.lib_tree.tag_configure("warn", foreground=T["tip"])

        def add(parent, node):
            is_cat = node["type"] == "category"
            if is_cat:
                n = len(RL.flatten_packs(node.get("children", [])))
                label = "%s  (%d)" % (node["name"], n)
            else:
                label = node["name"]
                if node["bad"]:
                    label += "  [!]"
            tags = []
            if node["bad"]:
                tags.append("bad")
            elif is_cat:
                tags.append("cat")
            elif node.get("warn"):
                tags.append("warn")
            iid = self.lib_tree.insert(parent, "end", text=label, tags=tuple(tags),
                                       open=is_cat)
            self.node_by_iid[iid] = node
            for ch in node.get("children", []):
                add(iid, ch)

        for nd in self.lib_nodes:
            add("", nd)

        if self.cur_pack is not None:
            for iid, nd in self.node_by_iid.items():
                if nd is self.cur_pack:
                    try:
                        self.lib_tree.selection_set(iid)
                        self.lib_tree.see(iid)
                    except Exception:
                        pass
                    break

    def on_lib_select(self, _e=None):
        sel = self.lib_tree.selection()
        if not sel:
            return
        node = self.node_by_iid.get(sel[0])
        if not node:
            return
        if node["type"] == "category":
            packs = RL.flatten_packs(node.get("children", []))
            ok = sum(1 for p in packs if not p["bad"])
            self.lib_status.configure(
                text="分类「%s」：%d 个资源包，可导入 %d 个" % (node["name"], len(packs), ok))
            return
        self.show_pack_info(node)
        if node is self._preparing_node:
            return
        self.select_pack(node)

    def on_lib_activate(self, _e=None):
        sel = self.lib_tree.selection()
        if not sel:
            return
        node = self.node_by_iid.get(sel[0])
        if node and node["type"] == "category":
            iid = sel[0]
            self.lib_tree.item(iid, open=not self.lib_tree.item(iid, "open"))

    def show_pack_info(self, node):
        info = "FBX %d · 贴图 %d" % (len(node.get("fbx", [])), len(node.get("tex", [])))
        if node["bad"]:
            self.lib_status.configure(text="[!] %s\n%s" % (info, "；".join(node["bad"][:3])))
        elif node.get("warn"):
            self.lib_status.configure(text="%s\n%s" % (info, "；".join(node["warn"][:2])))
        else:
            self.lib_status.configure(text=info)

    def _highlight_pack(self, node):
        for iid, nd in self.node_by_iid.items():
            if nd is node:
                try:
                    self.lib_tree.selection_set(iid)
                    self.lib_tree.see(iid)
                except Exception:
                    pass
                return

    # ---------- 无光照预览 ----------
    def preview_model(self):
        fbx = self.fbx.get().strip()
        if not fbx or not os.path.isfile(fbx):
            messagebox.showerror("错误", "请先选择 FBX 文件")
            return
        if self.busy or self.preparing or self.batch_running:
            messagebox.showinfo("提示", "正在处理，请稍候")
            return

        texs = [s.get("tex") for s in self.slots] if self.slots else list(self.pool)
        if not texs:
            if not messagebox.askyesno("没有贴图", "当前没有贴图，预览会是纯黑。继续？"):
                return

        self.preview_btn.config(state="disabled")
        self.preview_status.config(text="渲染中…")
        self.log("正在渲染无光照预览（Blender，约几秒）…")

        def work():
            try:
                sig_parts = [fbx, str(os.path.getmtime(fbx))]
                for t in texs:
                    if t and os.path.isfile(t):
                        sig_parts.append(t + str(os.path.getmtime(t)))
                    else:
                        sig_parts.append("none")
                sig = "|".join(sig_parts)
                key = hashlib.md5(sig.encode("utf-8")).hexdigest()[:12]
                out = os.path.join(PREVIEW_DIR, key)
                sigfile = os.path.join(out, ".sig")
                reuse = (os.path.isfile(os.path.join(out, "left.png"))
                         and os.path.isfile(os.path.join(out, "right.png"))
                         and os.path.isfile(sigfile))
                if reuse:
                    try:
                        reuse = open(sigfile, encoding="utf-8").read() == sig
                    except Exception:
                        reuse = False

                if not reuse:
                    os.makedirs(out, exist_ok=True)
                    cfg = {"fbx": fbx, "textures": texs, "out": out, "size": 256}
                    with open(PREVIEW_CFG, "w", encoding="utf-8") as f:
                        json.dump(cfg, f, ensure_ascii=False)
                    with BLENDER_LOCK:
                        r = subprocess.run(
                            [W.BLENDER, "--background", "--factory-startup",
                             "--python", PREVIEW_SCRIPT],
                            capture_output=True, text=True, encoding="utf-8",
                            errors="replace",
                            creationflags=getattr(W, "CREATE_NO_WINDOW", 0x08000000))
                    if not (os.path.isfile(os.path.join(out, "left.png"))
                            and os.path.isfile(os.path.join(out, "right.png"))):
                        tail = ((r.stdout or "") + (r.stderr or ""))[-900:]
                        raise RuntimeError("渲染失败：\n" + tail)
                    with open(sigfile, "w", encoding="utf-8") as f:
                        f.write(sig)

                self.ui(self._show_preview, out)
            except Exception as e:
                self.ui(self._preview_failed, str(e))

        threading.Thread(target=work, daemon=True).start()

    def _show_preview(self, out):
        try:
            from PIL import Image, ImageTk
        except Exception as e:
            self._preview_failed("缺少 Pillow：%s" % e)
            return
        imgs = {}
        for name, lbl in (("left", self.preview_left), ("right", self.preview_right)):
            p = os.path.join(out, name + ".png")
            im = Image.open(p).convert("RGB")
            im.thumbnail((128, 128))
            ph = ImageTk.PhotoImage(im)
            imgs[name] = ph
            lbl.config(image=ph, text="")
        self._preview_imgs = imgs
        self.preview_status.config(text="无光照 · 左 / 右")
        self.preview_btn.config(state="normal")
        self.log("预览已更新")

    def _preview_failed(self, err):
        self.preview_status.config(text="渲染失败")
        self.preview_btn.config(state="normal")
        self.log("[预览] " + err)

    # ---------- 资源管理：新建文件夹 / 拖动归类 ----------
    def new_library_folder(self):
        node = self.current_node()
        base = self.library_root
        if node and node["type"] == "category" and node.get("dir"):
            base = node["dir"]

        name = simpledialog.askstring("新建文件夹", "文件夹名称（建议英文）：", parent=self.root)
        if not name:
            return
        name = name.strip()
        if not name or re.search(r'[\\/:*?"<>|]', name):
            messagebox.showerror("错误", "名称不能为空，且不能包含 \\ / : * ? \" < > |")
            return
        p = os.path.join(base, name)
        if os.path.exists(p):
            messagebox.showerror("错误", "已存在同名文件或文件夹")
            return
        try:
            os.makedirs(p)
            self.log("已新建分类文件夹：%s" % p)
            self.refresh_library()
        except Exception as e:
            messagebox.showerror("创建失败", str(e))

    def _lib_press(self, e):
        self._lib_drag = None
        iid = self.lib_tree.identify_row(e.y)
        if not iid:
            return
        node = self.node_by_iid.get(iid)
        if not node or node["type"] != "pack" or node.get("members"):
            return
        outer = node.get("outer")
        if not outer or not os.path.isfile(outer):
            return
        self._lib_drag = {"node": node, "x": e.x_root, "y": e.y_root, "ghost": None}

    def _lib_motion(self, e):
        d = self._lib_drag
        if not d:
            return
        if d["ghost"] is None:
            if abs(e.x_root - d["x"]) < 6 and abs(e.y_root - d["y"]) < 6:
                return
            T = THEMES[self.theme]
            g = tk.Toplevel(self.root)
            g.overrideredirect(True)
            g.attributes("-topmost", True)
            tk.Label(g, text="移动到分类：" + d["node"]["name"], bg=T["accent_bg"],
                     fg=T["accent_fg"], padx=6, pady=2,
                     font=("微软雅黑", 8)).pack()
            d["ghost"] = g
        d["ghost"].geometry("+%d+%d" % (e.x_root + 12, e.y_root + 8))

    def _lib_release(self, e):
        d = self._lib_drag
        self._lib_drag = None
        if not d:
            return
        if d["ghost"] is not None:
            try:
                d["ghost"].destroy()
            except Exception:
                pass
        if d["ghost"] is None:
            return

        iid = self.lib_tree.identify_row(e.y)
        node = self.node_by_iid.get(iid)
        if node and node["type"] == "category" and node.get("dir"):
            target_dir = node["dir"]
        else:
            target_dir = self.library_root

        src = d["node"].get("outer")
        if not src or not os.path.isfile(src):
            return
        dst = os.path.join(target_dir, os.path.basename(src))
        if os.path.abspath(src) == os.path.abspath(dst):
            return
        if os.path.exists(dst):
            messagebox.showerror("移动失败", "目标位置已存在同名文件")
            return
        try:
            shutil.move(src, dst)
            self.log("已移动资源包到：%s" % target_dir)
            self.cur_pack = None
            self.refresh_library()
        except Exception as ex:
            messagebox.showerror("移动失败", str(ex))

    def _cache_key(self, node):
        raw = "|".join([node.get("outer") or ""] + list(node.get("members") or []))
        h = hashlib.md5(raw.encode("utf-8")).hexdigest()[:8]
        return "%s_%s" % (RL.sanitize_id(node["name"]) or "pack", h)

    def _prepare_pack(self, node):
        """解压 + 定位 FBX + 读取材质，返回 (fbx, mats, texs)"""
        dest = os.path.join(PACK_CACHE, self._cache_key(node))
        marker = os.path.join(dest, ".packinfo.json")
        sig = {"outer": node.get("outer"), "members": node.get("members"),
               "size": node.get("size"), "name": node["name"]}
        fresh = False
        if os.path.isdir(dest) and os.path.isfile(marker):
            try:
                with open(marker, encoding="utf-8") as f:
                    fresh = json.load(f).get("sig") == sig
            except Exception:
                fresh = False
        if not fresh:
            self.log("解压资源包：%s" % node["name"])
            shutil.rmtree(dest, ignore_errors=True)
            os.makedirs(dest, exist_ok=True)
            RL.extract_pack(node, dest, log=self.log)
            with open(marker, "w", encoding="utf-8") as f:
                json.dump({"sig": sig}, f, ensure_ascii=False)
            self._prune_pack_cache(os.path.basename(dest))

        fbx_rel = sorted(node.get("fbx") or [], key=RL.natural_key)
        if not fbx_rel:
            raise RuntimeError("资源包内没有 FBX")
        if len(fbx_rel) > 1:
            self.log("  包内有 %d 个 FBX，使用：%s" % (len(fbx_rel), fbx_rel[0]))
        fbx = os.path.join(dest, *fbx_rel[0].split("/"))
        if not os.path.isfile(fbx):
            raise RuntimeError("解压后找不到 FBX：%s" % fbx_rel[0])

        texs = []
        for rel in sorted(node.get("tex") or [], key=RL.natural_key):
            p = os.path.join(dest, *rel.split("/"))
            if os.path.isfile(p):
                texs.append(p)

        mats = self._mats_for(dest, fbx)
        return fbx, mats, texs

    def _prune_pack_cache(self, keep_name, keep=5):
        """解压缓存只保留最近使用的几个包，避免长期使用撑爆磁盘"""
        try:
            entries = []
            for d in os.listdir(PACK_CACHE):
                p = os.path.join(PACK_CACHE, d)
                if not os.path.isdir(p) or d == keep_name:
                    continue
                try:
                    mt = os.path.getmtime(p)
                except OSError:
                    continue
                entries.append((mt, p))
            entries.sort()
            for _mt, p in entries[:max(0, len(entries) - (keep - 1))]:
                shutil.rmtree(p, ignore_errors=True)
        except Exception:
            pass

    def _mats_for(self, dest, fbx):
        cache = os.path.join(dest, ".mats.json")
        try:
            st = os.stat(fbx)
            if os.path.isfile(cache):
                with open(cache, encoding="utf-8") as f:
                    d = json.load(f)
                if d.get("size") == st.st_size and d.get("mtime") == int(st.st_mtime):
                    return d.get("mats") or ["material"]
        except Exception:
            pass
        self.log("读取材质（Blender，约几秒）…")
        with BLENDER_LOCK:
            mats, out = W.dump_materials(fbx, os.path.join(dest, ".mats_raw.json"))
        if not mats:
            self.log("  材质读取失败，按单材质处理")
            mats = ["material"]
        try:
            st = os.stat(fbx)
            with open(cache, "w", encoding="utf-8") as f:
                json.dump({"size": st.st_size, "mtime": int(st.st_mtime), "mats": mats},
                          f, ensure_ascii=False)
        except Exception:
            pass
        return mats

    def select_pack(self, node):
        if node is self.cur_pack:
            return
        if node["bad"]:
            self.lib_status.configure(text="[!] 该资源包不可用：\n" + "；".join(node["bad"][:3]))
            self.log("[资源库] 跳过不合规资源包 %s：%s" % (node["name"], "；".join(node["bad"])))
            return
        if self.busy or self.preparing or self.batch_running:
            self.log("正在处理，请稍候再选择资源包")
            return

        self._preparing_node = node
        self.preparing = True
        self.refresh_start_state()
        self.pack_label.configure(text="当前资源包：%s（解压/读材质中…）" % node["name"])

        def work():
            try:
                fbx, mats, texs = self._prepare_pack(node)
            except Exception as e:
                self.ui(self._select_failed, node, str(e))
                return
            self.ui(self._select_done, node, fbx, mats, texs)

        threading.Thread(target=work, daemon=True).start()

    def _select_failed(self, node, err):
        self.preparing = False
        self._preparing_node = None
        self.refresh_start_state()
        self.pack_label.configure(text="当前资源包：%s（载入失败）" % node["name"])
        self.log("[资源库] 载入失败：%s" % err)
        self.lib_status.configure(text="[!] 载入失败：%s" % err[:120])

    def _select_done(self, node, fbx, mats, texs):
        self.preparing = False
        self._preparing_node = None
        self.cur_pack = node
        self.fbx.set(fbx)
        self.name.set(node["name"])
        self.pool = list(texs)
        self.slots = [{"mat": m, "tex": None} for m in mats]
        for s, (m, t) in zip(self.slots, RL.auto_pairs(mats, texs)):
            s["tex"] = t
        self.render_slots()
        self.refresh_pool()
        self.refresh_start_state()
        self.check_name()
        self.pack_label.configure(text="当前资源包：%s" % node["name"])
        self._highlight_pack(node)
        self.log("已载入资源包 %s：材质 %d，贴图 %d" % (node["name"], len(mats), len(texs)))
        if not texs:
            self.log("  该包没有贴图，所有材质将使用纯黑贴图")

    def open_library(self):
        try:
            os.makedirs(self.library_root, exist_ok=True)
            os.startfile(self.library_root)
        except Exception as e:
            messagebox.showerror("打开失败", str(e))

    def change_library(self):
        p = filedialog.askdirectory(title="选择资源库文件夹", initialdir=self.library_root)
        if not p:
            return
        self.library_root = p
        self.save_settings()
        self.cur_pack = None
        self.refresh_library()

    def import_zips(self):
        ps = filedialog.askopenfilenames(title="选择要放入资源库的压缩包",
                                         filetypes=[("ZIP 压缩包", "*.zip")])
        if not ps:
            return
        target = self.library_root
        node = self.current_node()
        if node is not None and node["type"] == "category" and node.get("dir"):
            target = node["dir"]
        try:
            os.makedirs(target, exist_ok=True)
        except Exception as e:
            messagebox.showerror("错误", "无法创建目录：\n%s" % e)
            return
        added, rejected = [], []
        for p in ps:
            base = os.path.basename(p)
            stem = os.path.splitext(base)[0]
            prob = RL.name_problem(stem, "资源包名")
            if prob:
                rejected.append("%s：%s" % (base, prob))
                continue
            dst = os.path.join(target, base)
            if os.path.exists(dst):
                rejected.append("%s：同名文件已存在" % base)
                continue
            try:
                shutil.copy2(p, dst)
                added.append(base)
            except Exception as e:
                rejected.append("%s：复制失败 %s" % (base, e))
        self.log("已导入 %d 个压缩包到资源库" % len(added))
        if rejected:
            messagebox.showwarning("部分文件未导入", "\n".join(rejected[:12]))
        self.refresh_library()

    def current_node(self):
        sel = self.lib_tree.selection()
        if not sel:
            return None
        return self.node_by_iid.get(sel[0])

    def delete_pack(self):
        node = self.current_node()
        if node is None or node["type"] != "pack":
            messagebox.showinfo("提示", "请先在资源库列表里选中一个资源包")
            return
        if node.get("members"):
            messagebox.showinfo("提示", "该资源包位于外层压缩包内，\n请直接编辑外层 zip 后刷新。")
            return
        if not node.get("outer") or not os.path.isfile(node["outer"]):
            messagebox.showinfo("提示", "该资源包不是独立文件，无法单独删除")
            return
        if not messagebox.askyesno("确认删除", "从资源库删除：\n%s ?" % node["outer"]):
            return
        try:
            os.remove(node["outer"])
            shutil.rmtree(os.path.join(PACK_CACHE, self._cache_key(node)), ignore_errors=True)
            self.log("已删除资源包：%s" % os.path.basename(node["outer"]))
        except Exception as e:
            messagebox.showerror("删除失败", str(e))
        if node is self.cur_pack:
            self.cur_pack = None
        self.refresh_library()

    # ---------- 配对表 ----------
    def _slot_wheel(self, e):
        try:
            self.slot_canvas.yview_scroll(int(-e.delta / 120), "units")
        except Exception:
            pass

    def render_slots(self):
        T = THEMES[self.theme]
        try:
            for w in self.slot_frame.winfo_children():
                w.destroy()
        except Exception:
            return
        if not self.slots:
            tk.Label(self.slot_frame, text="（选中资源包，或点「读取材质」）",
                     fg=T["tip"], bg=T["bg"], font=("微软雅黑", 8),
                     anchor="w").pack(fill="x", padx=6, pady=8)
            self.slot_canvas.configure(scrollregion=(0, 0, 0, 0))
            return
        for i, s in enumerate(self.slots):
            bg = T["slot_alt"] if i % 2 else T["bg"]
            row = tk.Frame(self.slot_frame, bg=bg)
            row.pack(fill="x", pady=1)
            row._slot_index = i
            mlab = tk.Label(row, text=s["mat"], width=16, anchor="w", bg=bg, fg=T["entry_fg"],
                            font=("Consolas", 9))
            mlab.pack(side="left", padx=(6, 2))
            tex = s.get("tex")
            txt = os.path.basename(tex) if tex else "纯黑（未绑定）"
            chip = tk.Label(row, text=txt, anchor="w", bg=T["chip_bg"], fg=T["chip_fg"],
                            padx=8, pady=3)
            if not tex:
                chip.configure(fg=T["tip"])
            chip.pack(side="left", fill="x", expand=True, padx=3)
            chip._slot_index = i
            if tex:
                chip.bind("<ButtonPress-1>", lambda e, p=tex: self._drag_begin(e, p))
                chip.bind("<B1-Motion>", self._drag_move)
                chip.bind("<ButtonRelease-1>", self._drag_end)
            chip.bind("<Double-1>", lambda e, i=i: self.unbind_slot(i))
            xbtn = tk.Button(row, text="×", width=2, command=lambda i=i: self.unbind_slot(i),
                             bg=T["btn_bg"], fg=T["btn_fg"], relief="flat", bd=0,
                             activebackground=T["btn_act_bg"],
                             activeforeground=T["btn_act_fg"])
            xbtn.pack(side="right", padx=3)
            for w in (row, mlab, chip, xbtn):
                w.bind("<MouseWheel>", self._slot_wheel)
        self.slot_frame.update_idletasks()
        self.slot_canvas.configure(scrollregion=self.slot_canvas.bbox("all"))
        self.slot_canvas.yview_moveto(0)

    def _pool_name(self, path):
        base = os.path.basename(path)
        same = [p for p in self.pool if os.path.basename(p) == base]
        if len(same) > 1:
            base = "%s/%s" % (os.path.basename(os.path.dirname(path)), base)
        if RL.is_preview_tex(path):
            base += "  [预览]"
        return base

    def refresh_pool(self):
        T = THEMES[self.theme]
        self.pool_list.delete(0, "end")
        used = {s["tex"] for s in self.slots if s.get("tex")}
        for i, p in enumerate(self.pool):
            mark = "● " if p in used else "○ "
            self.pool_list.insert("end", mark + self._pool_name(p))
            if p in used:
                self.pool_list.itemconfig(i, foreground=T["tip"])

    def _pool_press(self, e):
        idx = self.pool_list.nearest(e.y)
        if idx < 0 or idx >= len(self.pool):
            return
        self.pool_list.selection_clear(0, "end")
        self.pool_list.selection_set(idx)
        self._drag_begin(e, self.pool[idx])

    def _pool_double(self, e):
        idx = self.pool_list.nearest(e.y)
        if idx < 0 or idx >= len(self.pool):
            return
        path = self.pool[idx]
        for s in self.slots:
            if not s.get("tex"):
                s["tex"] = path
                self.render_slots()
                self.refresh_pool()
                return
        self.log("所有材质都已绑定贴图（可先双击材质行解绑）")

    def _drag_begin(self, e, path):
        if not path or self.batch_running or self.busy:
            return
        self._drag = {"path": path, "sx": e.x_root, "sy": e.y_root, "ghost": None}

    def _drag_move(self, e):
        d = self._drag
        if not d:
            return
        if d["ghost"] is None:
            if abs(e.x_root - d["sx"]) < 4 and abs(e.y_root - d["sy"]) < 4:
                return
            T = THEMES[self.theme]
            g = tk.Toplevel(self.root)
            g.overrideredirect(True)
            g.attributes("-topmost", True)
            tk.Label(g, text=os.path.basename(d["path"]), bg=T["accent_bg"],
                     fg=T["accent_fg"], padx=6, pady=2, font=("微软雅黑", 8)).pack()
            d["ghost"] = g
        d["ghost"].geometry("+%d+%d" % (e.x_root + 14, e.y_root + 10))

    def _drag_end(self, e):
        d = self._drag
        self._drag = None
        if not d or d.get("ghost") is None:
            return
        try:
            d["ghost"].destroy()
        except Exception:
            pass
        idx = self._slot_at(e.x_root, e.y_root)
        if idx is None:
            return
        self.assign(idx, d["path"])

    def _slot_at(self, x, y):
        try:
            w = self.root.winfo_containing(x, y)
        except Exception:
            return None
        while w is not None:
            i = getattr(w, "_slot_index", None)
            if i is not None:
                return i
            w = getattr(w, "master", None)
        return None

    def assign(self, idx, path):
        if idx is None or not (0 <= idx < len(self.slots)):
            return
        self.slots[idx]["tex"] = path
        self.root.after(0, self._refresh_slots_ui)

    def unbind_slot(self, idx):
        if 0 <= idx < len(self.slots):
            self.slots[idx]["tex"] = None
            self.root.after(0, self._refresh_slots_ui)

    def _refresh_slots_ui(self):
        self.render_slots()
        self.refresh_pool()

    def clear_slots(self):
        for s in self.slots:
            s["tex"] = None
        self.render_slots()
        self.refresh_pool()

    def pair_by_name(self):
        if not self.slots:
            messagebox.showinfo("提示", "请先选中资源包或点「读取材质」")
            return
        mats = [s["mat"] for s in self.slots]
        for s, (m, t) in zip(self.slots, RL.auto_pairs(mats, self.pool)):
            s["tex"] = t
        self.render_slots()
        self.refresh_pool()
        self.log("已按名称自动配对")

    def fill_in_order(self):
        if not self.slots:
            messagebox.showinfo("提示", "请先选中资源包或点「读取材质」")
            return
        used = {s["tex"] for s in self.slots if s.get("tex")}
        rest = [p for p in self.pool if p not in used]
        for s in self.slots:
            if s["tex"]:
                continue
            if not rest:
                break
            s["tex"] = rest.pop(0)
        self.render_slots()
        self.refresh_pool()
        self.log("已按文件夹顺序填充空槽")

    def pick_fbx(self):
        p = filedialog.askopenfilename(filetypes=[("FBX", "*.fbx")])
        if not p:
            return
        stem = os.path.splitext(os.path.basename(p))[0]
        prob = RL.name_problem(stem, "FBX 名")
        if prob:
            messagebox.showwarning("文件名不合规",
                                   "%s\n\n请改成英文名（A-Z a-z 0-9 _ -）后再导入。" % prob)
            return
        self.fbx.set(p)
        if not self.name.get():
            self.name.set(stem)
        self.check_name()

    def add_textures(self):
        ps = filedialog.askopenfilenames(filetypes=[("图片", "*.png *.jpg *.jpeg *.tga *.bmp")])
        if not ps:
            return
        bad = []
        for p in ps:
            stem = os.path.splitext(os.path.basename(p))[0]
            prob = RL.name_problem(stem, "贴图名")
            if prob:
                bad.append("%s：%s" % (os.path.basename(p), prob))
                continue
            if p not in self.pool:
                self.pool.append(p)
        self.pool.sort(key=lambda s: RL.natural_key(os.path.basename(s)))
        self.refresh_pool()
        if bad:
            messagebox.showwarning("部分贴图未加入", "\n".join(bad[:10]))
        if self.slots:
            used = {s["tex"] for s in self.slots if s.get("tex")}
            rest = [p for p in self.pool if p not in used]
            for s in self.slots:
                if s["tex"]:
                    continue
                if not rest:
                    break
                s["tex"] = rest.pop(0)
            self.render_slots()
            self.refresh_pool()

    def detect_materials(self):
        fbx = self.fbx.get().strip()
        if not fbx or not os.path.isfile(fbx):
            messagebox.showerror("错误", "请先选择 FBX 文件")
            return
        if self.busy or self.preparing or self.batch_running:
            messagebox.showinfo("提示", "正在处理，请稍候")
            return
        self.preparing = True
        self.refresh_start_state()
        self.log("正在读取材质（需要几秒）…")

        def work():
            try:
                os.makedirs(CACHE_DIR, exist_ok=True)
                with BLENDER_LOCK:
                    mats, out = W.dump_materials(fbx, os.path.join(CACHE_DIR, "_mats.json"))
            except Exception as e:
                mats, out = None, str(e)
            self.ui(self._mats_done, mats, out)

        threading.Thread(target=work, daemon=True).start()

    def _mats_done(self, mats, out):
        self.preparing = False
        self.refresh_start_state()
        if not mats:
            self.log("读取材质失败：\n" + (out or "")[-600:])
            return
        old = {s["mat"]: s.get("tex") for s in self.slots}
        self.slots = [{"mat": m, "tex": old.get(m)} for m in mats]
        for s, (m, t) in zip(self.slots, RL.auto_pairs(mats, self.pool)):
            if not s["tex"]:
                s["tex"] = t
        self.render_slots()
        self.refresh_pool()
        self.check_name()
        self.log("该模型有 %d 个材质：%s" % (len(mats), ", ".join(mats)))
        self.log("可拖动贴图调整对应关系；未绑定的材质将使用纯黑贴图。")

    # ---------- 名字/状态 ----------
    def check_name(self, *_):
        if not hasattr(self, "name_entry"):
            return
        T = THEMES[self.theme]
        name = self.name.get().strip()
        mid = sanitize(name) if name else ""
        addons = self.addons.get().strip()
        conflict = bool(mid) and bool(addons) and os.path.isdir(
            os.path.join(addons, mid + "_prop"))
        no_id = bool(name) and not re.search(r"[A-Za-z0-9]", name)
        self.name_conflict = conflict
        self.name_no_id = no_id
        try:
            if conflict:
                self.name_entry.configure(highlightbackground=T["warn_fg"],
                                          highlightcolor=T["warn_fg"],
                                          highlightthickness=2)
                self.dup_label.configure(
                    text="✖ 已存在同名模型「%s_prop」：请改名或先在右侧删除" % mid,
                    fg=T["warn_fg"])
            elif no_id:
                self.name_entry.configure(highlightbackground=T["warn_fg"],
                                          highlightcolor=T["warn_fg"],
                                          highlightthickness=2)
                self.dup_label.configure(
                    text="✖ 名字里至少要有英文字母或数字（用于生成模型 ID）",
                    fg=T["warn_fg"])
            else:
                self.name_entry.configure(highlightbackground=T["border"],
                                          highlightcolor=T["border"],
                                          highlightthickness=1)
                self.dup_label.configure(text="")
        except Exception:
            pass
        self.refresh_start_state()

    def refresh_start_state(self):
        if not hasattr(self, "start_btn"):
            return
        busy = self.busy or self.preparing or self.batch_running
        bad = (self.name_conflict or getattr(self, "name_no_id", False)
               or getattr(self, "_deps_missing", False))
        try:
            self.start_btn.configure(state="disabled" if (busy or bad) else "normal")
            if self.batch_running:
                self.batch_btn.configure(text="停止批量", bg=THEMES[self.theme]["danger_bg"])
            else:
                self.batch_btn.configure(text="批量导入此分类",
                                         bg=THEMES[self.theme]["accent_bg"])
        except Exception:
            pass

    def _sync_studiomdl(self):
        a = self.addons.get().strip()
        if a and os.path.isdir(a):
            p = os.path.join(os.path.dirname(os.path.dirname(a)), "bin", "studiomdl.exe")
            if os.path.isfile(p):
                W.STUDIOMDL = p

    def pick_addons(self):
        p = filedialog.askdirectory(title="选择 GarrysMod/garrysmod/addons 文件夹")
        if p:
            self.addons.set(p)
            self._sync_studiomdl()
            self.scan_models()
            self.check_name()

    def scan_models(self):
        """扫描 addons 目录，按文件夹（组）列出导入器生成的 <id>_prop 模型"""
        addons = self.addons.get().strip()
        try:
            self.tree.delete(*self.tree.get_children())
        except Exception:
            pass
        self.model_rows = {}
        self.model_paths = {}
        if not os.path.isdir(addons):
            return

        models = {}
        for d in sorted(os.listdir(addons)):
            if not d.endswith("_prop"):
                continue
            p = os.path.join(addons, d)
            if not os.path.isdir(p):
                continue
            mid = d[:-5]
            disp = mid
            lua = os.path.join(p, "lua", "autorun", mid + "_spawnlist.lua")
            if os.path.exists(lua):
                try:
                    for line in open(lua, encoding="utf-8", errors="replace"):
                        s = line.strip()
                        if s.startswith("local NAME"):
                            disp = s.split("=", 1)[1].strip().strip('"').strip("'")
                            break
                except Exception:
                    pass
                self._ensure_group_guard(mid, lua)
            models[mid] = (disp, p)

        # 清理已不存在的模型
        present = set(models)
        changed = False
        for g in self.groups:
            before = len(g["models"])
            g["models"] = [m for m in g["models"] if m in present]
            if len(g["models"]) != before:
                changed = True
        if changed:
            save_groups(self.groups)

        assigned = set()
        for g in self.groups:
            assigned.update(g["models"])
        ungrouped = [m for m in models if m not in assigned]

        uid = self.tree.insert("", "end", text="未分组 (%d)" % len(ungrouped),
                               open=True, tags=("cat",))
        self.model_rows[uid] = {"type": "ungrouped"}
        for mid in sorted(ungrouped):
            disp, p = models[mid]
            iid = self.tree.insert(uid, "end", text="%s  (%s)" % (disp, mid))
            self.model_rows[iid] = {"type": "model", "id": mid, "path": p, "name": disp}
            self.model_paths[iid] = p

        for g in self.groups:
            gid = self.tree.insert("", "end", text="%s (%d)" % (g["name"], len(g["models"])),
                                   open=True, tags=("cat",))
            self.model_rows[gid] = {"type": "group", "id": g["id"]}
            for mid in g["models"]:
                if mid not in models:
                    continue
                disp, p = models[mid]
                iid = self.tree.insert(gid, "end", text="%s  (%s)" % (disp, mid))
                self.model_rows[iid] = {"type": "model", "id": mid, "path": p,
                                        "name": disp, "group": g["id"]}
                self.model_paths[iid] = p

        self.sync_group_addon()

    def _ensure_group_guard(self, mid, lua_path):
        try:
            text = open(lua_path, encoding="utf-8").read()
        except Exception:
            return
        if "PropGroups" in text:
            return
        lines = text.splitlines()
        for i, line in enumerate(lines):
            if "PopulatePropMenu" in line and "function" in line:
                lines.insert(i + 1, '    if PropGroups and PropGroups["%s"] then return end' % mid)
                break
        else:
            return
        try:
            open(lua_path, "w", encoding="utf-8").write("\n".join(lines) + "\n")
        except Exception:
            pass

    def sync_group_addon(self):
        """把文件夹结构写成 Gmod 插件：每个文件夹一个分类，里面放拖入的模型"""
        addons = self.addons.get().strip()
        if not addons:
            return
        gdir = os.path.join(addons, "model_importer_groups")
        groups = [g for g in self.groups if g["models"]]
        if not groups:
            shutil.rmtree(gdir, ignore_errors=True)
            return
        try:
            os.makedirs(os.path.join(gdir, "lua", "autorun"), exist_ok=True)
            with open(os.path.join(gdir, "addon.json"), "w", encoding="utf-8") as f:
                json.dump({"title": "模型文件夹", "type": "tool", "tags": ["fun"]},
                          f, ensure_ascii=False)
            lines = [
                "-- 由 Gmod 模型导入器生成：把已导入模型按文件夹显示在生成菜单",
                "PropGroups = PropGroups or {}",
            ]
            for g in groups:
                for mid in g["models"]:
                    lines.append("PropGroups[%s] = %s" % (lua_str(mid), lua_str(g["id"])))
            lines.append('hook.Add("PopulatePropMenu", "model_groups_add", function()')
            lines.append("    local groups = {")
            for g in groups:
                models = ", ".join(lua_str("models/props/%s.mdl" % m) for m in g["models"])
                lines.append('        { id = %s, name = %s, icon = "icon16/folder.png", models = { %s } },'
                             % (lua_str(g["id"]), lua_str(g["name"]), models))
            lines.append("    }")
            lines.append("    for _, g in ipairs(groups) do")
            lines.append("        local c = {}")
            lines.append("        for _, m in ipairs(g.models) do")
            lines.append('            table.insert(c, { type = "model", model = m })')
            lines.append("        end")
            lines.append("        spawnmenu.AddPropCategory(g.id, g.name, c, g.icon)")
            lines.append("    end")
            lines.append("end)")
            with open(os.path.join(gdir, "lua", "autorun", "model_groups.lua"), "w",
                      encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
        except Exception as e:
            self.log("[文件夹] 写入 Gmod 分类插件失败：%s" % e)

    def new_model_group(self):
        name = simpledialog.askstring("新建文件夹", "文件夹名称（Gmod 生成菜单里显示）：",
                                      parent=self.root)
        if not name:
            return
        name = name.strip()
        if not name:
            return
        if any(g["name"] == name for g in self.groups):
            messagebox.showerror("错误", "已存在同名文件夹")
            return
        used = {g["id"] for g in self.groups}
        n = 1
        while "grp_%d" % n in used:
            n += 1
        self.groups.append({"id": "grp_%d" % n, "name": name, "models": []})
        save_groups(self.groups)
        self.scan_models()
        self.log("已新建文件夹：%s" % name)

    def delete_group(self, gid):
        g = next((x for x in self.groups if x["id"] == gid), None)
        if not g:
            return
        if not messagebox.askyesno("删除文件夹",
                                   "删除文件夹「%s」？\n里面的模型会回到“未分组”。" % g["name"]):
            return
        self.groups = [x for x in self.groups if x["id"] != gid]
        save_groups(self.groups)
        self.sync_group_addon()
        self.scan_models()
        self.log("已删除文件夹：%s" % g["name"])

    def assign_model_to_group(self, mid, gid):
        for g in self.groups:
            if mid in g["models"]:
                g["models"].remove(mid)
        if gid:
            for g in self.groups:
                if g["id"] == gid:
                    g["models"].append(mid)
                    break
        save_groups(self.groups)
        self.sync_group_addon()
        self.scan_models()

    def on_model_activate(self, _e=None):
        sel = self.tree.selection()
        if not sel:
            return
        row = self.model_rows.get(sel[0])
        if row and row["type"] in ("group", "ungrouped"):
            self.tree.item(sel[0], open=not self.tree.item(sel[0], "open"))

    def _right_press(self, e):
        self._right_drag = None
        iid = self.tree.identify_row(e.y)
        if not iid:
            return
        row = self.model_rows.get(iid)
        if not row or row["type"] != "model":
            return
        self._right_drag = {"row": row, "x": e.x_root, "y": e.y_root, "ghost": None}

    def _right_motion(self, e):
        d = self._right_drag
        if not d:
            return
        if d["ghost"] is None:
            if abs(e.x_root - d["x"]) < 6 and abs(e.y_root - d["y"]) < 6:
                return
            T = THEMES[self.theme]
            g = tk.Toplevel(self.root)
            g.overrideredirect(True)
            g.attributes("-topmost", True)
            tk.Label(g, text="移动模型：" + d["row"]["name"], bg=T["accent_bg"],
                     fg=T["accent_fg"], padx=6, pady=2, font=("微软雅黑", 8)).pack()
            d["ghost"] = g
        d["ghost"].geometry("+%d+%d" % (e.x_root + 12, e.y_root + 8))

    def _right_release(self, e):
        d = self._right_drag
        self._right_drag = None
        if not d:
            return
        if d["ghost"] is not None:
            try:
                d["ghost"].destroy()
            except Exception:
                pass
        if d["ghost"] is None:
            return
        iid = self.tree.identify_row(e.y)
        row = self.model_rows.get(iid)
        mid = d["row"]["id"]
        if row and row["type"] == "group":
            self.assign_model_to_group(mid, row["id"])
            self.log("已把 %s 移到文件夹：%s" % (mid, row["id"]))
        elif row and row["type"] == "ungrouped":
            self.assign_model_to_group(mid, None)
            self.log("已把 %s 移回未分组" % mid)

    def delete_selected(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("提示", "请先在右侧列表选中模型或文件夹")
            return
        iid = sel[0]
        row = self.model_rows.get(iid)
        if not row:
            return
        if row["type"] == "group":
            self.delete_group(row["id"])
            return
        if row["type"] != "model":
            return
        path = row.get("path", "")
        disp = row.get("name", os.path.basename(path))
        if not path:
            return
        if not messagebox.askyesno("确认删除",
                                   "确定删除模型「%s」？\n\n将删除整个文件夹：\n%s" % (disp, path)):
            return
        try:
            shutil.rmtree(path)
            self.log("已删除模型：%s" % path)
            self.scan_models()
            self.check_name()
        except Exception as e:
            messagebox.showerror("删除失败", str(e))

    # ---------- 导入 ----------
    def start(self):
        fbx = self.fbx.get().strip()
        name = self.name.get().strip()
        addons = self.addons.get().strip()
        if not fbx or not os.path.isfile(fbx):
            messagebox.showerror("错误", "请选择 FBX 文件")
            return
        if not name:
            messagebox.showerror("错误", "请填写模型名字")
            return
        if not addons:
            messagebox.showerror("错误", "请选择 Gmod addons 目录")
            return
        if self.name_conflict:
            messagebox.showerror("重名", "已存在同名模型「%s_prop」。\n\n"
                                          "请修改模型名字，或先在右侧列表删除旧模型。"
                                 % sanitize(name))
            return
        if getattr(self, "name_no_id", False):
            messagebox.showerror("模型名字", "名字里至少要有英文字母或数字（用于生成模型 ID）。")
            return
        if self.batch_running:
            messagebox.showinfo("提示", "批量导入进行中，请先停止批量任务。")
            return
        if not self.slots and not self.pool:
            messagebox.showerror("错误", "请先选中资源包，或添加贴图")
            return
        try:
            tl = float(self.length.get())
            rz = float(self.rot.get())
        except ValueError:
            messagebox.showerror("错误", "长度/角度必须是数字")
            return

        surf_name = self.surface.get()
        surf = SURFACE_MAP.get(surf_name, "metal")
        collision = (surf != "none")
        if surf == "none":
            surf = "metal"

        slots = [(s["mat"], s.get("tex")) for s in self.slots]
        pool = list(self.pool)
        self.busy = True
        self.refresh_start_state()
        self.logbox.delete("1.0", "end")

        def run():
            try:
                do_import(fbx, slots, name, tl, rz, addons, self.log,
                          surface=surf, collision=collision, pool=pool)
                self.log("\n完成！重启 Gmod 后，在生成菜单搜索：%s" % name)
                self.ui(self.scan_models)
            except Exception as e:
                self.log("\n[失败] " + str(e))
                traceback.print_exc()
            finally:
                self.ui(self._import_finished)

        threading.Thread(target=run, daemon=True).start()

    def _import_finished(self):
        self.busy = False
        self.refresh_start_state()
        self.check_name()

    def batch_import(self):
        if self.batch_running:
            self.batch_stop = True
            self.log("正在停止批量导入（当前包完成后停止）…")
            return
        if self.busy or self.preparing:
            messagebox.showinfo("提示", "正在导入或读取材质，请稍候再试。")
            return
        node = self.current_node()
        if node is None:
            messagebox.showinfo("提示", "请先在资源库选中一个分类或资源包")
            return
        if node["type"] == "category":
            packs = RL.flatten_packs([node])
            label = "分类「%s」" % node["name"]
        else:
            packs = [node]
            label = "资源包「%s」" % node["name"]
        ok_packs = [p for p in packs if not p["bad"]]
        skipped = len(packs) - len(ok_packs)
        if not ok_packs:
            messagebox.showerror("无法批量导入", "该范围内没有合规的资源包。")
            return
        msg = "%s 下共 %d 个资源包，将导入其中 %d 个" % (label, len(packs), len(ok_packs))
        if skipped:
            msg += "\n另有 %d 个不合规资源包会被跳过" % skipped
        msg += "\n\n提示：重名模型会被自动跳过，不会覆盖。\n继续？"
        if not messagebox.askyesno("批量导入", msg):
            return
        if not self.addons.get().strip():
            messagebox.showerror("错误", "请选择 Gmod addons 目录")
            return
        try:
            tl = float(self.length.get())
            rz = float(self.rot.get())
        except ValueError:
            messagebox.showerror("错误", "长度/角度必须是数字")
            return
        surf = SURFACE_MAP.get(self.surface.get(), "metal")
        collision = (surf != "none")
        if surf == "none":
            surf = "metal"
        addons = self.addons.get().strip()

        self.batch_running = True
        self.batch_stop = False
        self.user_abort = False
        self.refresh_start_state()
        self.logbox.delete("1.0", "end")

        def work():
            results = {"ok": [], "skip": []}
            seen = set()
            total = len(ok_packs)
            for i, p in enumerate(ok_packs, 1):
                if self.batch_stop:
                    self.user_abort = True
                    break
                self.log("\n[%d/%d] %s" % (i, total, p["name"]))
                mid = sanitize(p["name"])
                if mid in seen:
                    results["skip"].append((p["name"], "本次批量中重名"))
                    continue
                if os.path.isdir(os.path.join(addons, mid + "_prop")):
                    results["skip"].append((p["name"], "已存在同名模型"))
                    continue
                try:
                    fbx, mats, texs = self._prepare_pack(p)
                    slots = RL.auto_pairs(mats, texs)
                    do_import(fbx, slots, p["name"], tl, rz, addons, self.log,
                              surface=surf, collision=collision, pool=texs)
                    results["ok"].append(p["name"])
                    seen.add(mid)
                except Exception as e:
                    short = str(e).strip().splitlines()[0][:140] if str(e).strip() else "未知错误"
                    results["skip"].append((p["name"], short))
                    self.log("  跳过：%s" % short)
            self.ui(self._batch_done, results, self.user_abort)

        threading.Thread(target=work, daemon=True).start()

    def _batch_done(self, results, aborted):
        self.batch_running = False
        self.batch_stop = False
        self.refresh_start_state()
        self.scan_models()
        lines = ["成功 %d 个" % len(results["ok"]),
                 "跳过 %d 个" % len(results["skip"])]
        if aborted:
            lines.append("（已手动停止）")
        detail = ""
        if results["skip"]:
            detail = "\n\n跳过明细：\n" + "\n".join(
                "%s：%s" % (n, r) for n, r in results["skip"][:12])
            if len(results["skip"]) > 12:
                detail += "\n…还有 %d 个" % (len(results["skip"]) - 12)
        self.log("\n批量导入结束：" + "，".join(lines))
        messagebox.showinfo("批量导入结束", "，".join(lines) + detail)

    # ---------- 退出 ----------
    def save_settings(self):
        try:
            geo = self.root.geometry()
        except Exception:
            geo = ""
        save_settings({
            "library": self.library_root,
            "addons": self.addons.get(),
            "length": self.length.get(),
            "rot": self.rot.get(),
            "surface": self.surface.get(),
            "geometry": geo,
        })

    def on_close(self):
        self.batch_stop = True
        self.save_settings()
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    try:
        _icon = tk.PhotoImage(file=ICON)
        root.iconphoto(True, _icon)
        root._icon_ref = _icon
    except Exception:
        pass
    App(root)
    root.mainloop()
