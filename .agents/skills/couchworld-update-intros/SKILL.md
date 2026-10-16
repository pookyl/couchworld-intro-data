---
name: couchworld-update-intros
description: 为 CouchWorld 按欧乐连续剧日榜、周榜、月榜、总榜、首页最热连续剧或单剧链接半手动更新片头标注，运行现有检测脚本、写入 API JSON、生成报告，并在用户授权发布时提交推送及验证 GitHub Pages。适用于“更新片头”“运行周榜前20名”“处理首页最热连续剧”“强制处理这个剧”等请求；不用于通用视频剪辑或自动创建周期任务。
---

# CouchWorld 片头更新

## 项目与执行边界

本 skill 随 `couchworld-intro-data` 仓库维护，位于 `.agents/skills/couchworld-update-intros/`；其所属 `.agents/` 的父目录就是项目根目录。远端为 `https://github.com/pookyl/couchworld-intro-data`。使用用户当前指定的 checkout，不依赖某台电脑的绝对路径，也不创建替代仓库。进入项目后先查看 `git status`、README 和相关脚本 `--help`，以当前实现为准，保留用户已有改动。

任务中的“更新／运行检测”包含按当前脚本规则写入本地 API 标注和生成报告。“只看报告／不改标注”使用 `--report-only`。Git 提交、推送按本次请求及已有会话授权执行；此 skill 本身不增加发布权限，也不要求对已获授权的动作再次确认。若只获本地运行授权，完成本地工作并说明尚未发布。请求“发布／推送／让 App 从线上拿到更新”时完成提交、推送和线上验证。不要自动创建定时任务。

“再处理几集看看／补查其他集数”同样默认写入可用结果；“看看”不表示仅报告，不要因此要求用户再确认写入。只有用户明确要求仅报告或不改标注时才使用 `--report-only`。补充检测需记录实际 `selected_episodes`，通过 `apply_intro_results.py` 沿用三集共同音频等证据检查后写入；不能因取样集数不是 2–4 就停在报告，也不能绕过证据检查强行写入。

## 参数映射与关键约定

- 主入口是 `scripts/validate_ranked_intros.py`；默认 `--period week --limit 10`。
- 日榜／周榜／月榜／总榜映射到 `day/week/month/all`；前 N 名映射到 `--limit N`。先选前 N 名再跳过已有标注，不向后递补。
- 首页最热连续剧映射到 `--home-hot`，对应首页「连续剧 → 热播」海报列表，默认全部 12 部，保留页面顺序。可用 `--limit N` 缩小范围，超过列表长度按实际数量处理；不能与 `--period` 或单剧链接混用。快照为 `home-hot-snapshot.json`，回放用 `--home-hot --snapshot 文件`，不能与榜单快照混用。
- 单剧使用详情链接或播放链接作为位置参数，也可用 `--url`；不混用榜单参数。播放链接中的集数不改变默认取样集数。
- 默认检查 **`api/v1/intros/olevod/{ID}.json`**：存在就不请求详情、不下载、不分析。`work/` 或 `reports/` 中的 JSON 不是跳过依据。
- 用户要求“强制／覆盖已有／全部重新识别”才加 `--force`：重新分析，并用可用结果覆盖已有全剧默认标注；可复用来源一致的媒体缓存。未产出可用结果时保留旧值。
- 默认取高清第 2–4 集、各前 180 秒。第 1 集只在取样时排除，结果默认应用 **整部剧，包括第 1 集**，单集例外仍优先保留。
- 跳转候选取每集共同音频区间较早的末端，向下取整，不额外回退 3 秒。允许保留片头尾部或紧接片头的广告，不为了去掉广告或黑屏将候选后移。历史报告和标注不自动加 3 秒，需重新检测才应用新策略。
- OCR 是辅助证据，不是所有剧都必须通过的硬门槛；沿用 `apply_intro_results.py` 的写入条件，不临时降低条件去消除待复核项。OCR 错集数、开头未匹配内容、缺少共同音频、明显时间差等应如实保留原因。`--force` 不绕过证据检查。

## 执行与报告

选择能导入 NumPy 的 Python 3.10+ 环境，优先项目 `.venv` 或已有环境。Codex 提供依赖运行时时可通过可用的运行时发现工具定位，不硬编码个人缓存路径。FFmpeg、Chromaprint `fpcalc` 和 OCR 依赖按 README 检查；缺少依赖时说明具体问题，不用跳过测试或 OCR 来假装完整成功。

为本轮使用独立的 `work/` 子目录，报告放 `reports/`。日期和时间使用 America/Los_Angeles；可用 `--previous-dir` 指向已有完整样本目录，由脚本核对来源后复用。不要仅凭文件名复制旧结果充当本次重分析。

示例（`python3` 代表选定的可用 Python；工作目录名按本轮任务确定）：

```sh
python3 scripts/validate_ranked_intros.py \
  --period week --limit 20 --force \
  --work-dir work/weekly-run \
  --previous-dir work/weekly-top20-force

python3 scripts/build_intro_report.py \
  --input work/weekly-run/results.json \
  --output reports/weekly-run.html

# 首页最热连续剧，默认跳过已有标注
python3 scripts/validate_ranked_intros.py --home-hot --work-dir work/home-hot
python3 scripts/build_intro_report.py \
  --input work/home-hot/results.json --output reports/home-hot.html
```

例子中的 `--force` 仅在用户要求强制时使用，`--previous-dir` 仅在该目录存在且适合复用时使用。单剧替换为：

```sh
python3 scripts/validate_ranked_intros.py \
  'https://www.olevod.com/details-2-85528.html' \
  --work-dir work/single-run
```

等待实际进程完成，长任务报告有意义的进度。处理完成后读取本轮 `results.json`：核对 `selected_count`、`skipped_count`、样本完成状态、`conservative_review`、`episode_card_review`、`marker_write_summary` 和每部 `marker_write`。音频候选数、OCR 确认数、实际写入数是不同概念；写入错误不能当作待复核或成功。

若已有完整检测结果、只需补写 API，可运行以下命令而不重复下载与 OCR。`--force` 同样只按用户覆盖意图使用：

```sh
python3 scripts/apply_intro_results.py --input work/weekly-run/results.json --force
```

补写会更新输入报告里的写入状态；随后重新生成 HTML。不要为了刷新状态反复补写而把本次“新增／更新”统计改成“值相同”。

打开或提供本地 HTML 报告；若通过浏览器展示，只将 `reports/` 用本地回环地址提供服务，复用经确认可访问的现有服务或新建服务，不硬编码某次会话的端口。截图来自真实媒体样本；不要生成替代画面。报告包含所有待复核原因，不把候选描述成已确认的全剧精确边界。

## 提交、发布与验证

常规更新的版本控制产物是 `api/v1/intros/olevod/*.json`。`tests/` 属于代码，应该跟踪；`reports/`、`work/`、`.venv/`、`__pycache__/` 是本地产物，保持忽略。普通数据更新不顺手修改检测算法、阈值或其他 App 项目。

提交前检查本次差异，使用 `scripts/intro.py` 的 `validate_document` 验证变更标注，确认单集例外未丢失。仅暂存本次相关文件，避免包含用户的无关改动。若修改了脚本，运行相关测试；纯数据更新不必重新下载所有视频。

获得发布授权时：核对远端和目标分支，提交本次变更并正常推送，不强推。若远端前进或出现冲突，保留本地成果并按实际情况处理，不能覆盖无关远端更改。推送后检查对应提交的 GitHub Pages 构建状态，并获取本次变更的线上 JSON 与本地逐项比较：

`https://pookyl.github.io/couchworld-intro-data/api/v1/intros/olevod/{ID}.json`

线上读取可加本次提交标识作为查询参数避免旧缓存。部署失败时停止重复发布并报告错误；构建仍进行时做有限等待并更新进度，不重推相同提交触发无穷重试。只有构建成功且实际 JSON 匹配，才说其他 App 可以获取新数据；否则区分本地已写入、已提交、已推送、尚待部署验证。

最终用中文简洁交付：实际范围和 force 状态，处理／跳过数、写入／相同值／待复核／错误数，报告入口，以及是否提交、推送和线上验证。不要把全部已存在的标注计作本次新增。
