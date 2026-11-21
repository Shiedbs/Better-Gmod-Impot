# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 dy是鹅的不是
#
# 本程序是自由软件：你可以依据 GNU 通用公共许可证第 3 版（或你选择的
# 任何更新版本）重新分发和/或修改它。本程序按"现状"分发，不附带任何
# 明示或暗示的担保。完整条款见项目根目录的 LICENSE 文件。
"""资源库核心：扫描 / 索引 / 校验 / 安全解压。

约定（与导入器一致）：
  * 资源包压缩包名、包内 FBX 名、贴图名必须是英文：A-Za-z0-9_-
  * 不允许空格、中文、全角字符
  * 只支持 .zip
  * 外层 zip 里套 zip => 形成分类文件夹（分类名即外层压缩包名）

设计要点：
  * 扫描只读 zip 中央目录，不解压，因此几百个包也能秒开；
  * 嵌套 zip 的索引结果按外层 zip 的 (mtime, size) 缓存，包没变就不重复解压；
  * 解压做路径穿越 / 体积 / 文件数防护。
"""

import io
import json
import os
import re
import shutil
import zipfile

IMG_EXTS = {".png", ".jpg", ".jpeg", ".tga", ".bmp", ".gif", ".tif", ".tiff", ".webp"}
ZIP_EXT = ".zip"

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
MAX_DEPTH = 3            # 嵌套压缩包最大层数
MAX_FILES = 20000        # 单包最大文件数
MAX_BYTES = 4 * 1024 ** 3  # 单包最大解压体积 4GB
SKIP_NAMES = {".ds_store", "thumbs.db", "desktop.ini"}


# --------------------------------------------------------------------------
# 名称校验
# --------------------------------------------------------------------------
def name_problem(stem, kind="名称"):
    """返回不合规原因，合规返回空字符串。"""
    if not stem:
        return "%s为空" % kind
    if len(stem) > 40:
        return "%s过长（最多 40 字符）" % kind
    if re.search(r"\s", stem):
        return "%s含空格（请用下划线 _）" % kind
    if not NAME_RE.match(stem):
        if any(ord(c) > 127 for c in stem):
            return "%s含中文或全角字符" % kind
        bad = sorted({c for c in stem if not re.match(r"[A-Za-z0-9_-]", c)})
        return "%s含非法字符：%s" % (kind, " ".join(bad[:6]))
    return ""


def file_name_problem(rel_path, kind="文件"):
    base = os.path.basename(rel_path.replace("\\", "/"))
    stem = os.path.splitext(base)[0]
    prob = name_problem(stem, kind + "名")
    if prob:
        return "%s「%s」：%s" % (kind, base, prob)
    return ""


def sanitize_id(name):
    """中文/非法字符 -> 下划线（正常英文名下几乎不变）"""
    s = (name or "").strip().lower()
    s = re.sub(r"[^a-z0-9_]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s


def natural_key(s):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


# --------------------------------------------------------------------------
# zip 内部名称处理（Windows 压缩软件常用 GBK 且不打 UTF-8 标志位）
# --------------------------------------------------------------------------
def _decode_name(info):
    name = info.filename
    if info.flag_bits & 0x800:
        return name
    try:
        raw = name.encode("cp437")
    except Exception:
        return name
    for enc in ("utf-8", "gbk", "big5"):
        try:
            return raw.decode(enc)
        except Exception:
            pass
    return name


def _norm(name):
    n = name.replace("\\", "/")
    while n.startswith("./"):
        n = n[2:]
    return n


def _is_junk(rel):
    if rel.startswith("__MACOSX/") or rel == "__MACOSX":
        return True
    base = rel.rsplit("/", 1)[-1].lower()
    return base in SKIP_NAMES


def strip_common_root(names):
    """如果所有文件都在唯一一层目录下，返回该目录名（用于拍平嵌套外壳）"""
    if not names:
        return ""
    tops = {n.split("/")[0] for n in names}
    if len(tops) != 1:
        return ""
    top = tops.pop()
    if not all(n.startswith(top + "/") for n in names):
        return ""
    return top


def read_member(zf, fixed_name):
    for info in zf.infolist():
        if _norm(_decode_name(info)) == fixed_name:
            return zf.read(info)
    raise KeyError("压缩包内找不到：%s" % fixed_name)


# --------------------------------------------------------------------------
# 索引
# --------------------------------------------------------------------------
def _inspect(zf):
    items = []
    for info in zf.infolist():
        fixed = _norm(_decode_name(info))
        if not fixed or fixed.endswith("/"):
            continue
        items.append((fixed, info))
    names = [n for n, _ in items]
    root = strip_common_root(names)
    prefix = (root + "/") if root else ""
    rel_items = []
    for n, info in items:
        if not n.startswith(prefix):
            continue
        rel = n[len(prefix):]
        if not rel or _is_junk(rel):
            continue
        rel_items.append((rel, info))
    fbx = [(r, i.file_size) for r, i in rel_items if r.lower().endswith(".fbx")]
    tex = [(r, i.file_size) for r, i in rel_items
           if os.path.splitext(r)[1].lower() in IMG_EXTS]
    zips = [(r, i.file_size) for r, i in rel_items if r.lower().endswith(ZIP_EXT)]
    total = sum(i.file_size for _, i in rel_items)
    return {"root": root, "fbx": fbx, "tex": tex, "zips": zips, "total": total}


def _pack_node(name, outer, members, fbx, tex, size, bad=None, warn=None):
    return {"type": "pack", "name": name, "outer": outer, "members": list(members),
            "fbx": list(fbx), "tex": list(tex), "size": size,
            "bad": list(bad or []), "warn": list(warn or []), "children": []}


def _index_zip_obj(zf, depth, outer, members, name):
    try:
        info = _inspect(zf)
    except Exception as e:
        return _pack_node(name, outer, members, [], [], 0,
                          bad=["压缩包读取失败：%s" % e])

    if info["fbx"]:
        node = _pack_node(name, outer, members,
                          [r for r, _ in info["fbx"]], [r for r, _ in info["tex"]],
                          info["total"])
        if info["zips"]:
            node["warn"].append("包内另有 %d 个压缩包，已忽略" % len(info["zips"]))
        return node

    if info["zips"]:
        if depth >= MAX_DEPTH:
            return _pack_node(name, outer, members, [], [], info["total"],
                              bad=["压缩包嵌套超过 %d 层" % MAX_DEPTH])
        cat = {"type": "category", "name": name, "outer": outer, "members": list(members),
               "size": info["total"], "bad": [], "warn": [], "children": []}
        prefix = (info["root"] + "/") if info["root"] else ""
        for rel, _sz in info["zips"]:
            full = prefix + rel
            child_name = os.path.splitext(os.path.basename(rel))[0]
            try:
                data = read_member(zf, full)
                with zipfile.ZipFile(io.BytesIO(data)) as inner:
                    child = _index_zip_obj(inner, depth + 1, outer, members + [full], child_name)
            except Exception as e:
                child = _pack_node(child_name, outer, members + [full], [], [], 0,
                                   bad=["内部压缩包读取失败：%s" % e])
            cat["children"].append(child)
        return cat

    return _pack_node(name, outer, members, [], [], info["total"],
                      bad=["压缩包内没有 FBX，也没有内层压缩包"])


def _index_path(path):
    name = os.path.splitext(os.path.basename(path))[0]
    with zipfile.ZipFile(path) as zf:
        return _index_zip_obj(zf, 0, os.path.abspath(path), [], name)


# --------------------------------------------------------------------------
# 缓存
# --------------------------------------------------------------------------
def load_cache(cache_path):
    try:
        with open(cache_path, encoding="utf-8") as f:
            data = json.load(f)
        if data.get("version") == 1 and isinstance(data.get("zips"), dict):
            return data
    except Exception:
        pass
    return {"version": 1, "zips": {}}


def save_cache(cache_path, cache):
    try:
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        tmp = cache_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False)
        os.replace(tmp, cache_path)
    except Exception:
        pass


def _index_cached(path, cache, log=None):
    try:
        st = os.stat(path)
    except OSError:
        return _pack_node(os.path.splitext(os.path.basename(path))[0], path, [], [], [], 0,
                          bad=["文件不存在"]), False
    key = os.path.abspath(path)
    hit = cache["zips"].get(key)
    if hit and hit.get("mtime") == st.st_mtime and hit.get("size") == st.st_size:
        return hit["node"], False
    if log:
        log("  索引资源包：%s" % os.path.basename(path))
    node = _index_path(path)
    cache["zips"][key] = {"mtime": st.st_mtime, "size": st.st_size, "node": node}
    return node, True


# --------------------------------------------------------------------------
# 扫描 + 校验
# --------------------------------------------------------------------------
def _dedupe(items):
    out, seen = [], set()
    for s in items:
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def finalize(nodes):
    """统一改名/重名/内容校验，结果写入 bad（阻断）与 warn（提示）。

    会就地修改节点，且必须可重复调用（缓存节点会被反复 finalize）。
    """
    DUP_MSG = "资源包重名（同名包出现多次）"
    packs, cats = [], []

    def walk(ns):
        for n in ns:
            if n["type"] == "category":
                cats.append(n)
                walk(n.get("children", []))
            else:
                packs.append(n)

    walk(nodes)

    for c in cats:
        if c.get("outer"):
            prob = name_problem(c["name"], "分类压缩包名")
            if prob:
                c["bad"].append(prob)
        c["bad"] = _dedupe(c.get("bad") or [])
        c["warn"] = _dedupe(c.get("warn") or [])

    for p in packs:
        bad = [x for x in (p.get("bad") or []) if x != DUP_MSG]
        warn = list(p.get("warn") or [])
        prob = name_problem(p["name"], "资源包名")
        if prob:
            bad.append(prob)
        if not p.get("fbx"):
            bad.append("包内没有 FBX")
        for rel in p.get("fbx", []):
            prob = file_name_problem(rel, "FBX")
            if prob:
                bad.append(prob)
        for rel in p.get("tex", []):
            prob = file_name_problem(rel, "贴图")
            if prob:
                bad.append(prob)
        p["bad"] = _dedupe(bad)
        p["warn"] = _dedupe(warn)

    counts = {}
    for p in packs:
        k = p["name"].lower()
        counts[k] = counts.get(k, 0) + 1
    for p in packs:
        if counts[p["name"].lower()] > 1:
            p["bad"].append(DUP_MSG)
        p["bad"] = _dedupe(p["bad"])

    return nodes


def scan_library(root, cache_path=None, log=None):
    """扫描资源库根目录，返回分类/资源包树。"""
    cache = load_cache(cache_path) if cache_path else {"version": 1, "zips": {}}
    dirty = False
    nodes = []
    if not os.path.isdir(root):
        return nodes
    for name in sorted(os.listdir(root), key=natural_key):
        if name.startswith(".") or name.startswith("_"):
            continue
        p = os.path.join(root, name)
        if os.path.isdir(p):
            cat = {"type": "category", "name": name, "dir": p,
                   "bad": [], "warn": [], "size": 0, "children": []}
            for fn in sorted(os.listdir(p), key=natural_key):
                fp = os.path.join(p, fn)
                if fn.lower().endswith(ZIP_EXT) and os.path.isfile(fp):
                    child, changed = _index_cached(fp, cache, log)
                    cat["children"].append(child)
                    dirty = dirty or changed
            nodes.append(cat)
        elif name.lower().endswith(ZIP_EXT) and os.path.isfile(p):
            node, changed = _index_cached(p, cache, log)
            nodes.append(node)
            dirty = dirty or changed

    finalize(nodes)
    if dirty and cache_path:
        save_cache(cache_path, cache)
    return nodes


def flatten_packs(nodes):
    out = []

    def walk(ns):
        for n in ns:
            if n["type"] == "category":
                walk(n.get("children", []))
            else:
                out.append(n)

    walk(nodes)
    return out


# --------------------------------------------------------------------------
# 解压
# --------------------------------------------------------------------------
def open_pack_zip(node):
    """打开资源包（可能是嵌套 zip 的最后一层），返回 ZipFile。"""
    outer = node.get("outer")
    if not outer:
        raise RuntimeError("该节点不是压缩包")
    members = node.get("members") or []
    if not members:
        return zipfile.ZipFile(outer)
    zf = zipfile.ZipFile(outer)
    for m in members:
        data = read_member(zf, m)
        zf.close()
        zf = zipfile.ZipFile(io.BytesIO(data))
    return zf


def _safe_parts(rel):
    parts = [p for p in rel.replace("\\", "/").split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise RuntimeError("压缩包内含非法路径（..）：%s" % rel)
    if not parts:
        raise RuntimeError("压缩包内含空路径")
    if re.match(r"^[A-Za-z]:", parts[0]):
        raise RuntimeError("压缩包内含绝对路径：%s" % rel)
    return parts


def extract_zip(zf, dest, max_bytes=MAX_BYTES, max_files=MAX_FILES, log=None):
    """安全解压到 dest（已拍平外壳目录），返回 {相对路径: 绝对路径}。"""
    items = []
    for info in zf.infolist():
        fixed = _norm(_decode_name(info))
        if not fixed or fixed.endswith("/"):
            continue
        items.append((fixed, info))

    names = [n for n, _ in items]
    root = strip_common_root(names)
    prefix = (root + "/") if root else ""

    os.makedirs(dest, exist_ok=True)
    out = {}
    total = 0
    for fixed, info in items:
        if not fixed.startswith(prefix):
            continue
        rel = fixed[len(prefix):]
        if not rel or _is_junk(rel):
            continue
        parts = _safe_parts(rel)
        total += info.file_size
        if total > max_bytes:
            raise RuntimeError("解压体积超过上限（%.1f GB）" % (max_bytes / 1024 ** 3))
        if len(out) >= max_files:
            raise RuntimeError("文件数超过上限（%d）" % max_files)
        dst = os.path.join(dest, *parts)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with zf.open(info) as src, open(dst, "wb") as w:
            shutil.copyfileobj(src, w)
        out[rel] = dst
    return out


def extract_pack(node, dest, log=None):
    zf = open_pack_zip(node)
    try:
        return extract_zip(zf, dest, log=log)
    finally:
        zf.close()


# --------------------------------------------------------------------------
# 自动配对（名称优先，其次顺序）
# --------------------------------------------------------------------------
def _tex_key(path):
    return sanitize_id(os.path.splitext(os.path.basename(path))[0])


PREVIEW_HINTS = ("preview", "thumb", "thumbnail", "icon", "cover", "screenshot", "banner")


def is_preview_tex(path):
    stem = os.path.splitext(os.path.basename(path))[0].lower()
    return any(h in stem for h in PREVIEW_HINTS)


def auto_pairs(mats, texs, avoid_preview=True):
    """返回 [(材质名, 贴图路径或 None)]，不重复使用同一张贴图。

    avoid_preview：不把 preview/thumb/icon 这类预览图自动当成材质贴图
    （仍保留在贴图池里，用户可手动拖上去）。
    """
    cand = list(texs)
    if avoid_preview:
        filtered = [t for t in cand if not is_preview_tex(t)]
        if filtered:
            cand = filtered
    used = set()
    pairs = []
    for m in mats:
        key = sanitize_id(m)
        pick = None
        for t in cand:
            if t in used:
                continue
            if _tex_key(t) == key:
                pick = t
                break
        if pick is None:
            for t in cand:
                if t not in used:
                    pick = t
                    break
        if pick:
            used.add(pick)
        pairs.append((m, pick))
    return pairs
