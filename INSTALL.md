# EJU Codex Beta 1.0.0b11 安装

需要本机 Python 3.12、3.13 或 3.14（包含 venv/pip）。Mac 使用 `python3`，Windows 建议安装 Python 官方 Launcher 并使用 `py -3`。若默认命令选择了其他版本，请用正确版本的解释器运行安装程序。Codex 本身需要另行安装。Beta 不包含完整历年题库。公开包不分发官方题面图片；默认安装不下载题目。未导入本人合法持有的指定官方 PDF 时，官方题目仍待补齐。请按随包的本地官方 PDF 导入说明运行 `python3 /path/to/app/launch.py import-official /path/to/your-official.pdf`（Windows 用 `py -3`）；导入先校验指定 PDF 哈希，题库状态以实际 doctor/题目资源核验为准，安装成功不等于真题已可用。

先把发行包完整解压到本机目录。安装脚本只用 Python 标准库；依赖进入独立 `.venv`，不改全局 Python、Codex 配置、信任或权限。双击“安装”进入本机交互向导：选择新安装、重试或复制升级，确认三个目录，再明确选择联网依赖或本地轮子。向导默认离线，最后输入 INSTALL 才开始。命令行也可在发行包目录明确选择一种方式：

```sh
python3 install.py --allow-network
# Windows：py -3 install.py --allow-network
```

`--allow-network` 只授权 pip 从 PyPI 下载 requirements-lock.txt 中带 SHA256 的固定依赖及其轮子，采用 `--isolated --require-hashes --only-binary=:all:`；不上传私人数据，不安装 OCR、模型或云服务。不支持当前平台的轮子会明确失败。离线安装先在受控环境准备与目标平台/Python 匹配的全部锁定轮子，再运行：

```sh
python3 install.py --wheelhouse "/absolute/path/to/wheels"
```

离线模式使用 `--no-index`。如果发行包缺少完整带哈希的锁文件，安装前即失败。Mac Docling 仅在另行安装完毕、模型齐全且明确通过 configure_codex.py 的 `--docling` 开启时可用；本安装不配置它。Windows 核心无需 OCR。

默认分别创建用户目录下 `EJU-Codex-Beta/app-1.0.0b11`、`EJU-Codex-Beta/private-vault`、`EJU-Codex-Beta/Codex 学习项目`。也可分别传入 `--app`、`--vault`、`--workspace`；三个目标必须全新、互不嵌套，位于发行源目录之外，路径不能经过符号链接或 junction。可以包含空格、中文及一般 shell 特殊字符；换行/NUL 路径不支持。安装程序绝不接管已有目标。Windows 权限继承当前用户目录的 ACL；不会修改系统 ACL，请勿选择共享目录。

安装成功后，在 Codex 将输出的学习项目目录作为项目打开，按 Codex 正常流程加载项目 MCP 配置并开始新聊天，说“开始学习”。工作台可由 app 或学习项目中的“打开工作台.command”/“打开工作台.cmd”启动。Windows 学习项目启动器需要 `py`；app 启动器直接调用其私有 `.venv/Scripts/python.exe`。保持终端运行并打开显示的本机链接。配置生成成功不等于 Codex 桌面会话已连接。

检查安装与资料库：

```sh
python3 "/path/to/app-1.0.0b11/launch.py" doctor
python3 "/path/to/app-1.0.0b11/launch.py" status
python3 "/path/to/app-1.0.0b11/launch.py" backup "/path/to/new-backup"
# Windows 可将 python3 换成 py -3。
```

`doctor` 验证实际资料库与附件；不代表 OCR、Codex 模型调用或桌面连接验收。安装会运行私有运行时的 doctor；工作进程信息以实际运行结果为准。私有运行时版本可执行 `app/.venv/bin/python --version`（Windows 为 `app\.venv\Scripts\python.exe --version`）。

安装失败时不自动删除任何目录。app 下 `installation.json` 记录版本、源清单、安装身份、失败阶段以及已创建/尝试创建的目标。使用**同一份原始发行包**可以重试复制或依赖阶段的中断：

```sh
python3 install.py --resume --app "/上次的程序目录" --allow-network
# 离线重试改用 --wheelhouse "/本地轮子目录"。
```

重试读取记录中的 vault/workspace，核对源清单及已复制文件；不会采用新传入的库路径、覆盖被编辑的代码、跟随运行时链接或接管已有库/项目。为保护记录，建库或生成学习项目开始后不自动重跑：保留现场，先对已建立的库运行私有运行时 `core/run.py --vault 路径 doctor`。验证成功后可按下面恢复/配置步骤建立**新的**项目路径；验证失败保留原件并恢复已核对备份。不要把已有学习库当作缓存删除，也不要删除整个 `EJU-Codex-Beta` 父目录。

## 从 Beta 1–10 复制升级

先停止旧版工作台，结束正在写入的学习会话。运行新发行包的安装器，显式指定旧程序目录和三个新目标：

```sh
python3 install.py --upgrade-from "/旧版程序目录" \
  --app "/新程序目录" --vault "/新学习库目录" \
  --workspace "/新Codex学习项目" --allow-network
```

安装器验证旧版安装状态，用新运行时对旧库建立一致备份，再校验并恢复到新库。旧程序、旧入口和旧库保留。新 app 下的 `migration-backup` 是含附件的**普通未加密备份**，同样需要妥善保管。新项目指向新库，历史事件、草稿和版本信息保留，活动会话凭证清除。缺失的旧题仅保留历史证据，不会伪造原题。

切换后只继续使用新项目：旧、新两份库不做实时同步或自动合并。若需要回退，在产生新的学习记录前重新打开旧项目；已经产生新记录时先另存新库备份，避免丢掉这段进度。官方原页属于程序资源，可在“准备与连接”重新导入 README 列出的九份指定 PDF。

迁移前用上述 `backup` 命令创建新备份目录，另存到你选择的安全介质。普通备份未加密，包含私人资料。恢复到全新目录：

```sh
python3 "/path/to/app/launch.py" restore "/path/to/backup" "/path/to/new-vault"
```

恢复后，用私有 Python 运行 `core/configure_codex.py --python 私有Python路径 --vault 新库路径 --workspace 全新项目路径`，生成新的项目配置。不要移动已安装 app 后继续使用旧配置，其路径是绝对路径。

卸载前退出工作台及 Codex 项目会话，核对备份，再手动删除 installation.json 标明的 app 与学习项目目录。默认保留 private-vault 和所有备份；删除私人库需要你另行决定。请勿把学习项目的 `.codex/config.toml`、installation.json 或私人库加入公开发行包。

当前 Mac 的运行结果与 Windows 静态兼容检查分别记录。未在真实 Windows 上运行的行为（Python Launcher、CMD、ACL、实际轮子安装及浏览器）仍需 Windows 实机验收。

Windows 实机步骤与回执模板见 [Windows 验收](WINDOWS_VALIDATION.md)。当前二进制依赖可用性核验针对 Windows x64 / Python 3.12；其他架构没有本次证据。

## 可选口令备份

```sh
python3 "/path/to/app/launch.py" backup-encrypted "/path/to/new-backup.ejubak"
python3 "/path/to/app/launch.py" restore-encrypted "/path/to/backup.ejubak" "/path/to/new-vault"
```

命令会在本机终端隐去口令输入；不支持把口令作为参数、环境变量或管道。生成时二次确认，恢复时错误口令/损坏文件不会建立恢复库。口令至少 12 个字符；遗失无法恢复。加密快照上限 128 MiB，超限时可使用普通备份并自行选择受保护介质。恢复后仍需用上面的 configure_codex.py 创建新入口，恢复操作不会修改当前 installation.json。

向导默认目录带 `b11` 后缀以便并存；纯命令行不传路径时仍使用顶部列出的默认私人库与学习项目，若已存在会拒绝并要求新路径。Windows 仍需实际设备验证 CMD、Launcher、ACL 和 PDF 渲染一致性。
