# Gmod 自定义模型导入器

把 FBX + 贴图自动编译成 Garry's Mod 道具（`.mdl`/`.vvd`/`.vtx` + 材质 + 生成菜单条目），
并提供一个**资源库**：把模型打包成 zip 拖进窗口，自动识别、分类、填表、批量导入。

> 本项目以 **GPL-3.0-or-later** 开源，见 [LICENSE](LICENSE)。

## 功能

- **单文件导入**：选 FBX + 多张贴图 → 自动解析材质 → 编译 → 安装进 `addons/` → 生成可搜索的生成菜单条目
- **资源库**：`资源库/分类/*.zip` 形式的模型包；外层 zip 套 zip 会自动变成分类
- **拖放**：直接把 zip 拖进窗口左侧即可入库并自动选中
- **材质配对**：按 FBX 材质槽生成配对表，贴图可拖动绑定，未绑定自动使用纯黑贴图
- **批量导入**：整个分类一键导入，跳过不合规包，结束给汇总
- **模型预览**：资源栏下方无光照左右视图预览
- **模型文件夹**：右侧可新建文件夹，把已导入模型拖进去；游戏生成菜单按文件夹分类
- **重名保护**：资源包重名、模型 ID 重名都会红字提示并阻止覆盖
- **命名规则**：资源包 / FBX / 贴图文件名必须是英文（`A-Za-z0-9_-`），中文和空格会被标红拒绝

## 目录结构

```
仓库根目录/
├─ GmodModelImporter.py    # 主程序（Tkinter 界面）
├─ tools/
│   ├─ resource_lib.py     # 资源库：扫描/索引/校验/解压
│   ├─ weapon_replace.py   # SMD/VTF/VMT 编译封装
│   ├─ fbx_to_smd.py       # Blender 端 FBX → SMD
│   └─ preview_render.py   # 无光照预览渲染
├─ 启动导入器.bat           # 启动（优先用 runtime\python，其次系统 Python）
├─ 安装依赖.bat             # 一键 pip install
├─ requirements.txt
├─ README.md / LICENSE / AUTHORS / .gitignore
└─ icon.png / icon.ico
```

> 本仓库**不包含** Python、Blender，也不包含 Garry's Mod 的 `bin`（studiomdl、vtex、engine.dll 等）。
> 请自行安装 Python 3.12+、Blender，并确保已拥有 Garry's Mod。

## 使用

1. 安装 **Python 3.12+**（勾选 Add Python to PATH）

2. 双击 `安装依赖.bat`，或运行：

   ```bat
   py -3 -m pip install -r requirements.txt
   ```

3. 安装 **Blender**（4.2 LTS 或 5.2 LTS），并确保 **Garry's Mod** 已安装

4. 双击 `启动导入器.bat`

5. 界面里确认 `garrysmod\addons` 目录（不对就点「浏览」）

6. 把模型 zip 拖进左侧「资源库」，或点「导入压缩包」

7. 点资源包 → 自动解压、读材质、填好 FBX/名字/贴图 → 点「开始导入」

资源库里的包也可以是纯文件夹（`资源库/人物/猫娘.zip`），文件夹名就是分类名。

> 没有 Python 的用户：可以使用作者发布的“便携版”（自带 Python 与依赖），不用执行上面的 pip 步骤。

## 许可

- 本项目代码：**GPL-3.0-or-later**
- 你可以自由使用、修改、再分发；再分发时必须保留许可与版权声明，并提供源码
- 本项目不提供任何担保

## 第三方组件

| 组件                          | 位置       | 许可                 | 说明                                                                                             |
| --------------------------- | -------- | ------------------ | ---------------------------------------------------------------------------------------------- |
| Blender                     | **不随仓库** | GPL-2.0-or-later   | 用户自行安装；如另外发布含 Blender 的便携版，需附 `license/` 与源码地址：<https://www.blender.org/download/source-code/> |
| Python                      | **不随仓库** | PSF License        | 用户自行安装 Python 3.12+                                                                            |
| Pillow / numpy              | pip 安装   | MIT / BSD          | `py -3 -m pip install -r requirements.txt`                                                     |
| Garry's Mod / Source SDK 工具 | **不随仓库** | Valve / Steam 订阅协议 | 导入器从用户本机 `GarrysMod\bin` 自动定位，不再二次分发                                                           |
| 图标                          | `icon.*` | 作者自绘               |                                                                                                |

> 说明：GPL 只约束**本项目的代码**。把 Blender 作为独立程序调用，不会让 GPL "传染" 到本项目之外的软件；
> 但再分发 Blender 本身时，仍需履行 Blender 的 GPL 义务。
> 本项目不附带任何 Valve/Steam 游戏文件，使用者需自行拥有 Garry's Mod。

## 相关项目

- **SoulCam**：配套的 Garry's Mod 单人插件，独立实现，同样以 GPL-3.0-or-later 发布。

## 仓库

本仓库即源码。`git clone` 后按上面的「使用」安装依赖并运行即可。

## 作者

**dy是鹅的不是**（抖音同名）—— 项目发起、模型导入流程、资源库。

详见 [AUTHORS](AUTHORS)。
