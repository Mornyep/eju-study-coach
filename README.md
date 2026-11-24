# EJU 学习教练 · 1.0.0 Beta 11

面向 Mac 与 Windows 的本地学习工具，由 Codex 带领学习，浏览器工作台负责完整看题、草稿和作答。默认以审核过的 EJU 原题为主；原创题用于明确的辅助练习。

这是小范围测试候选。首次安装请看 [安装指南](INSTALL.md)，能力与实测边界看 [发布说明](RELEASE_NOTES.md)。软件不是 JASSO 官方产品，没有得分预测或提分保证。

## 安装并开始

1. 安装 Python 3.12、3.13 或 3.14，以及你本人的 Codex。Mac 使用 `python3`；Windows 可使用 `py -3.12`。不需要管理员权限。
2. 将此包解压到普通本地目录，双击“安装”进入本机向导，或按照 [安装指南](INSTALL.md) 使用命令。它会明确要求选择从 PyPI 下载锁定依赖，或使用本地 wheelhouse。没有自动 OCR 模型下载。
3. 安装器创建独立程序、私人资料库和 Codex 学习项目。在 Codex 打开生成的学习项目，按宿主正常提示加载配置，新建聊天并说「开始学习」。登录或项目信任由本人正常操作；文件准备不等于宿主已加载。
4. 双击安装目录中的「打开工作台」，保持终端运行，打开它显示的本机链接。题面和答案草稿与 Codex 共用私人库。
5. 在工作台的“准备与连接”选择本地官方 PDF。未导入时可明确请求原创辅助练习；系统不会自动用原创替代原题。

## 接入官方原页

公开软件包不包含官方 PDF/PNG 或其他人的私人题库。JASSO [使用条款](https://www.jasso.go.jp/en/notice/policy/index.html) 对再分发有明确限制。请通过 [官方过去问样题页](https://www.jasso.go.jp/ryugaku/eju/examinee/pastpaper_sample/index.html) 取得以下已核对版本，供你在本机使用：

| 文件 | 官方来源 | SHA-256 |
| --- | --- | --- |
| 2018 第1回 综合科目 | [2018_1question_jw.pdf](https://www.jasso.go.jp/ryugaku/eju/examinee/pastpaper_sample/__icsFiles/afieldfile/2021/09/10/2018_1question_jw.pdf) | `674cb79402a4559f569fa23bba2b028de46778a94c22362b5a94a3c9a194d175` |
| 2010 第1回 综合科目 | [eju_2010_01question_jw_ja.pdf](https://www.jasso.go.jp/ryugaku/eju/examinee/pastpaper_sample/__icsFiles/afieldfile/2021/02/16/eju_2010_01question_jw_ja.pdf) | `ba00b682a99088a2c1e37e46f516af949ae2eab7f129587d0259f3ca88ce607e` |
| 2010 第1回 日本语 | [eju_2010_01question_jafl_1.pdf](https://www.jasso.go.jp/ryugaku/eju/examinee/pastpaper_sample/__icsFiles/afieldfile/2021/02/16/eju_2010_01question_jafl_1.pdf) | `c91fdc4bcde8861304e172cb6f7a3a71fb4a2f9e04d74c6dfed9f84c35c9c1f7` |
| 2018 第1回 日本语 | [2018_1question_jafl.pdf](https://www.jasso.go.jp/ryugaku/eju/examinee/pastpaper_sample/__icsFiles/afieldfile/2026/02/24/2018_1question_jafl.pdf) | `ed520ff7362a7f9708ab038e4f4019c09f6207702c84cafdcd5c9aa82bbd8956` |
| 2018 第1回 理科 | [2018_1question_science.pdf](https://www.jasso.go.jp/ryugaku/eju/examinee/pastpaper_sample/__icsFiles/afieldfile/2021/09/10/2018_1question_science.pdf) | `e433282a32e30454712bb0baae12270ff6cca6367538a52cf2243e00dd92f261` |
| 2017 第1回 日本语 | [2017_1question_jafl.pdf](https://www.jasso.go.jp/ryugaku/eju/examinee/pastpaper_sample/__icsFiles/afieldfile/2025/04/28/2017_1question_jafl.pdf) | `86a2b9a07707f1ea1f6ca00405d8648f5bad307315bcb68a0d01db77547d8913` |
| 2017 第2回 日本语 | [2017_2question_jafl.pdf](https://www.jasso.go.jp/ryugaku/eju/examinee/pastpaper_sample/__icsFiles/afieldfile/2025/04/24/2017_2question_jafl.pdf) | `871d2271ea93f9bd9f2087d7eb0f2031ea4a1ef697817ee9b15acdb8f540f3c9` |
| 2017 第2回 综合科目 | [2017_2question_jw.pdf](https://www.jasso.go.jp/ryugaku/eju/examinee/pastpaper_sample/__icsFiles/afieldfile/2025/04/24/2017_2question_jw.pdf) | `2876ae4939a49c45f00cebebc031ab35852a99d971eb4ac126286a9586c687cb` |
| 2017 第1回 综合科目 | [2017_1question_jw.pdf](https://www.jasso.go.jp/ryugaku/eju/examinee/pastpaper_sample/__icsFiles/afieldfile/2025/04/24/2017_1question_jw.pdf) | `2daa15cf42d9c208bc000eafce11322d348e6130603669ec64d9db0aed824363` |

也可以在安装目录运行，每个文件一次（Windows 把 `python3` 换为 `py -3.12`）：

```sh
python3 launch.py import-official "/完整路径/2018_1question_jw.pdf"
python3 launch.py import-official "/完整路径/2018_1question_science.pdf"
python3 launch.py import-official "/完整路径/2018_1question_jafl.pdf"
python3 launch.py import-official "/完整路径/eju_2010_01question_jafl_1.pdf"
python3 launch.py import-official "/完整路径/eju_2010_01question_jw_ja.pdf"
python3 launch.py import-official "/完整路径/2017_1question_jafl.pdf"
python3 launch.py import-official "/完整路径/2017_2question_jafl.pdf"
python3 launch.py import-official "/完整路径/2017_1question_jw.pdf"
python3 launch.py import-official "/完整路径/2017_2question_jw.pdf"
python3 launch.py doctor
```

导入器不联网、不修改原 PDF。先核对整个 PDF 哈希，再渲染已审核页，并逐张核对 PNG 哈希和尺寸。九份 PDF 全部匹配后，76 项官方材料全部可用：日语读解 32 道（22 篇独立文章）、综合科目 32 道、物理 10 道，以及 2 套完整日语记述题。每套记述含作答说明和二选一主题，不拆算成四项独立材料；2017 两份日语 PDF 同时用于读解和记述。

每份文件可单独导入，只有其中已核对的题变为可用。从旧版升级后，需要重新导入 2017 两份日语 PDF，以生成新增记述原页。这是 2010、2017、2018 年的局部题库，不覆盖全科或全部知识点，也不代表当前考试的难度校准。若官网文件更新或本机渲染结果不同，导入会停止，不会把未核版本标成可用。原页可随时从原 PDF 重建，通常不必放入学习记录备份。

## 数据、备份与恢复

[隐私说明](PRIVACY.md) 区分本地 Core 和 Codex 的模型处理；[安全说明](SECURITY.md) 列出已落实的边界。日常不用手工管理队列或搬运进度。

```sh
python3 launch.py backup "/新的备份目录"
python3 launch.py restore "/备份目录" "/新的恢复目录"
```

上述普通备份是未加密的本地一致快照，含已登记的附件，不含 inbox 临时文件、可重建解析缓存、运行连接凭证。恢复只写新目录，并不切换当前库。先检查恢复库，再用安装包中的 `core/configure_codex.py --python "安装目录中的Python" --workspace "新的学习项目" --vault "新的恢复目录"` 建立新入口；原库保留。具体命令见安装指南。

停止工作台：在启动终端按 Ctrl+C。卸载只移除确认属于本次安装的程序目录与学习项目；私人库和备份单独保留。不要删除未核对的资料。

可选的口令加密备份（只在本机交互终端输入口令，不放在命令中）：

```sh
python3 launch.py backup-encrypted "/新的备份文件.ejubak"
python3 launch.py restore-encrypted "/备份文件.ejubak" "/新的恢复目录"
```

完整备份先通过 AES-256-GCM 认证后才解包，口令至少 12 个字符。软件不保存口令，遗失后无法恢复。备份内容上限 128 MiB；工作中的学习库和普通备份仍未加密。具体格式和临时文件边界见 [安全说明](SECURITY.md)。

Beta 1–10 的显式复制升级、安装中断重试和恢复步骤见 [安装指南](INSTALL.md)。升级不覆盖旧库；新旧库后续写入不会自动合并。

## 日语与综合的答后练习

日语读解有 32 道完整官方题；文章和选项全部加载后才开放作答。64 道日语与综合题提供经核对的答后指引。新增重点包括指代与推断范围、实用条件、地图、供需图、汇率换算和 GDP 指数。

覆盖按同篇文章或共享完整原页保守合并。全部材料此前未见时，日语指代有 4 组、条件有 6 组、推断范围有 10 组；综合地图有 4 组、图表有 9 组、计算有 7 组。不同重点可能包含同一组，不能相加当作总材料数。你的当前未见候选还取决于已经练过、提示或展示过的材料。

同篇材料或同一完整原页上的另一题按已见处理，候选列表也会避免同时推荐这类题。即使旧目录没有同篇标记，系统也会对照官方 PDF、页码和图片识别共用原页；读取资料、导入 PDF 本身不算学习者曝光。历史事件保持原样，已有共同原页的作答不会被重新算作新的独立证据。

查看解析后可按设问、依据、知识、选项和下一步练习回读。提交且揭答后可手动保存本人复盘：日语侧重指代、句段关系和原文证据；综合侧重知识、图表、时间地点与计算。复盘是自述，不直接改变掌握度。多窗口修改冲突时比较两份内容后明确选择；离开未保存页面会提醒。

记述使用下方的版本保存流程；本版仍没有官方听力音频。

## 连续练习与复盘

工作台支持直接选择本次科目，查找下一道完整未见原题并明确点击开始。先接入官方PDF；没有候选时保留材料缺口，不自动改用原创。完成作答后可查看解析、保存本人复盘，再选择下一题。未保存复盘会阻止切题，需先保存或明确撤销未保存修改。

“学习记录”默认按最近作答整理，可筛选科目，查看本人复盘及单独的理由核对；“查看原始事件”保留草稿、展示等回执。推荐中的“相关”指上题涉及的知识点；仅同科的候选会直说，不等于已经精确诊断薄弱点。

“待复习”按每题最新作答显示需要核对的理由，点“查看作答与讲解”回看 Codex 已保存的解释。需要重新作答时点“重做这道旧题”；它保留已见状态，先完成或继续当前未完任务。新聊天可读取保存的讲解与下一步；是否实际已接入，仍以该聊天的工具回执为准。

Beta 9 的理由反馈引用你这次实际写下的思路，区分有依据、需要修正和尚需澄清。答案对照结果与理由核对分别展示；理由不足时先问一个具体问题。讲解可以附上已审核的练习方向，点击“采用这个方向查找原题”才应用为本次临时筛选，之后仍由你选择是否开始。依据更新或题目材料不可用时，旧建议会要求重新确认。

## 本次练习的训练重点

工作台的“下一道原题”可选训练重点；不选择时沿用按科目查找。重点来自已核对的题目要求，只过滤完整未见原题，不是对本人弱项的自动诊断。没有匹配题时会明确说明，可自行取消筛选。日语和综合的64道原题已分类，物理暂不提供重点筛选。

Beta 8 将“指代与比喻”扩充至 2 题、“单位换算与计算”扩充至 5 题。题数包含同篇或同页的小问；已见整页后，同页其他题不会再作为首次独立练习推荐。旧学习记录保留，暂停中的题仍按原题版本继续；已有开始请求可继续重放。


## 记述的原稿、反馈与修改稿

打开工作台“记述修改”，选择题目并写下自己的想法。导入后优先列出 2017 年第 1、2 回的完整官方记述题，每套都保留作答说明及两个备选主题。完整原页加载并校验成功后才能保存新版本；页面字符数仅供写作参考，不模拟官方答题纸计数或评分。另有四项标明来源的原创辅助练习，其中两项为完整记述，另外两项为写前准备与审题核对。打开题目本身不创建草稿，点击“保存原稿”后才保存。空稿或未完成稿也可保留，不代表完成作答。

回到 Codex 请它核对是否回应题目，引用你这版稿子的原句，最多提出两处内容、结构或语言上的修改/澄清要求。它应先保留你的意思，不擅自换立场、补经历或整篇改写；讲过的反馈由工具实际保存后，工作台才会显示。

按反馈自己修改，点击“保存修改稿”追加新版本。原稿、每一版文字及针对各版本的反馈都保留；前后对照只展示实际文字，不自动判断进步或换算 EJU 官方分数。新稿不沿用旧稿的“当前反馈”，历史版本可单独回看。

另一会话先保存新稿时，本页输入不会覆盖它。先比较最新稿，再明确采用最新版本为修改基础；确认后再次保存。结果不明时保留页面并核对同一次保存回执，不重复创建版本。刷新或关闭前，未保存输入会提醒；只有已保存的内容保证可恢复。


“最近修改与反馈”按真实保存或反馈活动排列。点击“继续这篇”才打开稿子；刷新列表不替换未保存的输入。新聊天接续摘要包含最近三篇的短摘录、版本数与当前反馈状态，随后按你选的稿子读取全文。较早稿子可从全部草稿继续查找。空稿不算完成；旧版反馈不会当作新稿反馈。

题目来源变化或原页缺失时，历史稿件和反馈仍可读，但会标明不能继续保存；不会把新的题面冒充旧来源。题面读取和作文接续摘要不改变掌握度、复习到期时间或普通选择题的未见统计。
