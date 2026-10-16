# CouchWorld 片头标注与自动识别

独立于 Roku App 的命令行工具与数据仓库。当前支持按日／周／月／总榜或单剧链接自动采样，通过跨集音频比对寻找片头候选，再用 OCR 核对集数卡片，并生成图文报告。确认后的时间可写入全剧默认标注，供 CouchWorld App 读取。

**当前自动化范围：取样、音频检测、集数 OCR、结果汇总及可用结果写入 API 标注。** 默认检查 `api/v1/intros/olevod/{ID}.json`，存在就跳过；`--force` 会重新分析并覆盖可用的全剧默认标注，保留单集例外。`--report-only` 仅输出报告。图文报告生成仍是独立命令，脚本不自动提交推送；GitHub Actions 周期任务尚未启用。

| 脚本 | 职责 |
| --- | --- |
| `scripts/validate_ranked_intros.py` | 主入口：选择连续剧、跳过已有标注、获取样本、调用音频与 OCR、输出 `results.json` 并写入可用 API 标注 |
| `scripts/analyze_intro.py` | Chromaprint 指纹与 NumPy 跨集音频比对 |
| `scripts/episode_cards.py` | 在音频候选附近核对“第几集”卡片 |
| `scripts/recognize_text.swift` | macOS Apple Vision 本地 OCR 桥接 |
| `scripts/build_intro_report.py` | 从结果和本地样本生成内嵌截图的 HTML 报告 |
| `scripts/apply_intro_results.py` | 将已有检测结果补写到 API，无需重新下载或执行 OCR |
| `scripts/intro.py` | 写入或更新正式标注 JSON，默认应用整部剧 |

## 环境准备

在仓库根目录执行示例命令。手工标注只需 Python 3.8+ 标准库；完整自动分析使用 **Python 3.10+、NumPy 2.x、FFmpeg 和 Chromaprint 的 `fpcalc`**。建议使用独立 Python 环境：

```sh
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements-analysis.txt
```

FFmpeg 和 `fpcalc` 需要单独安装并放入 PATH；`--fpcalc` 也可指定可执行文件路径。macOS OCR 还需 Swift 编译器和 Apple Vision；Linux OCR 需 Tesseract 及中文、英文语言数据，详见下文。Chromaprint 实测版本为 1.6.2。

## 自动检测命令行

入口为 `scripts/validate_ranked_intros.py`。在仓库根目录、已安装 `requirements-analysis.txt` 的 Python 环境中运行；另需 FFmpeg、Chromaprint（`fpcalc`），OCR 依赖见下文。缺省自动查找 PATH 中的 `fpcalc`，本机也可复用已下载的 macOS ARM64 版本，其他路径用 `--fpcalc` 指定。

```sh
# 在线获取连续剧周榜，检查前 10 名
python3 scripts/validate_ranked_intros.py

# 日榜前 20 名；也支持 week、month、all
python3 scripts/validate_ranked_intros.py --period day --limit 20
python3 scripts/validate_ranked_intros.py --period month --limit 20
python3 scripts/validate_ranked_intros.py --period all --limit 20

# 只处理一个连续剧；也支持播放页链接或 --url 参数
python3 scripts/validate_ranked_intros.py 'https://www.olevod.com/details-2-85528.html'

# 已有 API 标注也重新分析并更新默认值
python3 scripts/validate_ranked_intros.py 'https://www.olevod.com/details-2-85528.html' --force

# 生成本轮报告
python3 scripts/build_intro_report.py \
  --input work/ranked-validation/results.json \
  --output reports/latest-intro-report.html
```

默认在获取详情、下载和分析之前检查仓库的 `api/v1/intros/olevod/{ID}.json`，只要文件存在就跳过，包括单集例外或禁用标注文件。不会把 `work/` 中的分析报告当作正式标注。`--markers-dir` 可指定检查和写入的标注目录。`--force` 覆盖跳过规则，重新分析后用可用结果覆盖该目录中的全剧默认标注；仍可复用来源一致的媒体缓存，不强制重新下载。无法取得可用结果时保留旧标注。

`--limit 20` 表示选中榜单前 20 名，再跳过其中已有标注的剧，不向后递补。服务返回不足 N 部时按实际数量处理并提示。默认 `--period week`、`--limit 10`，单剧模式不能混用榜单参数。输出 `work/ranked-validation/results.json` 包含实际选中数、跳过数和每部处理状态，排行榜快照保存为同目录的 `ranking-snapshot.json`。`--work-dir` 可指定其他工作目录。

可继续用 `--snapshot 文件` 代替在线抓榜；新快照记录 `period`，旧版无周期字段的快照按周榜处理，不能拿周榜快照冒充日榜。完全离线时加 `--analyze-only`，并提供快照或单剧链接及包含详情、样本的工作目录。默认跳过规则仍生效，需要重分析已标注剧时同时加 `--force`。

主入口默认把可用候选写入 API 的 `defaultIntro`，覆盖整部剧，包括第 1 集。写入条件为完整三集共同音频证据、从开头附近开始匹配、候选时间差不超过 3 秒且没有 OCR 错集数冲突；统一取较早跳转点。OCR 未检出或集数卡片时间不同，不单独否定音频结果。片头前有未匹配内容等情况保留复核状态，不覆盖已有文件。

`--report-only` 禁止写入标注；`--force --report-only` 可重分析已有标注而不覆盖。每部结果中的 `marker_write` 以及汇总 `marker_write_summary` 明确记录写入、值相同、跳过、待复核或错误。发布仍需提交推送。已有运行结果可以直接补写：

```sh
python3 scripts/apply_intro_results.py \
  --input work/ranked-validation/results.json --force
```

手工指定时间或单集例外仍使用 `scripts/intro.py`。

## 当前识别策略

后续排行榜验证默认取高清第 **2、3、4 集**，每集前 180 秒。第 1 集可能有特殊编排，不参与共同片头推断；若缺少其中一集，不用第 1 集或其他集补位，仍要求三个样本形成完整共同证据。**取样范围与应用范围分开：确认后的结果默认写入整部剧的 `defaultIntro`，包括第 1 集和后续集数；明确指定单集时才写入 `episodes`，已有单集例外优先并保留。**这是基于样本向全剧应用的默认策略，不表示逐集验证过。历史报告保留原取样范围。

音频检测使用 [Chromaprint](https://github.com/acoustid/chromaprint) 生成指纹，匹配算法由本项目独立实现。三集两两比对后，要求三组都有至少 20 秒的完整重复区间，且每集的配对区间互相重叠。触及 180 秒取样末端的匹配不能作为完整片头。

每集取共同区间较早的末端，向下取整后再回退 **3 秒**，作为复核候选。保留少量片头或紧接片头的广告均可接受，不为去掉广告继续后移。指纹时间不是逐帧边界；OCR 仅补充确认，不能自动识别所有广告、剧情或特殊编排。证据不足时保留明确状态，不产生正式标注。

若只想比对已有音频，可直接调用底层脚本：

```sh
python3 scripts/analyze_intro.py ep2.wav ep3.wav ep4.wav \
  --fpcalc /path/to/fpcalc --sample-seconds 180 \
  --output work/intro-analysis.json
```

## 自动识别集数卡片（OCR）

排行榜验证现在默认启用 `scripts/episode_cards.py`：在每集音频候选前 8 秒至后 20 秒内逐秒抽帧，范围限定在已下载的前 180 秒。识别完整的“第二集”“第02集”等中文或数字标签，核对实际集数，并要求连续两帧命中；三集都确认且卡片首次连续出现时间相差不超过 5 秒，才标记为 `confirmed`。OCR 置信值低于 0.5 的标签不采纳；该值是引擎分数，不是准确率。

这一步只补充确认依据，不向后移动音频候选，不改变全剧默认应用策略，也不自动发布。错集数会报告 `conflict`，未识别到或依赖不可用分别记录，不能把 OCR 运行失败当作“没有卡片”。卡片可能落在扫描范围外，短于两秒的卡片也可能无法确认。无完整音频候选时暂不进行全窗口 OCR 搜索。报告会列出每集识别文字、首次连续命中时间及冲突文字。

macOS 自动使用本机 [Apple Vision](https://developer.apple.com/documentation/vision/vnrecognizetextrequest)，需要 Swift 编译器；Linux 使用 [Tesseract](https://tesseract-ocr.github.io/tessdoc/Command-Line-Usage.html)，需安装 `tesseract-ocr`、`tesseract-ocr-chi-sim`、`tesseract-ocr-eng` 和 FFmpeg。两种引擎都在本地运行；Linux 后端尚未在真实视频上验证。系统沙箱若阻止 Vision 访问本地识别服务，需在允许系统 OCR 的环境中运行。`--ocr-backend` 可指定 `vision` 或 `tesseract`，`--skip-ocr` 可跳过排行榜流程的 OCR。

已有报告可单独补充 OCR，不需要重新下载视频：

```sh
python3 scripts/episode_cards.py \
  --input work/top10-validation/85566-episodes-2-4.json \
  --work-dir work/top10-validation \
  --output work/top10-validation/85566-ocr.json
```

输出中的 `episode_card_review` 保存识别证据。再用 `scripts/build_intro_report.py` 渲染排行榜结果，即可显示 OCR 结论；`scripts/intro.py` 的正式标注格式保持不变。

## 写入正式标注

使用 Python 3 的 `scripts/intro.py`，输入欧乐连续剧任一集的链接，以及**跳过片头后开始播放的位置**。默认保存整部剧的标注，`--episode` 才只标注链接里的这一集。时间支持秒数、`MM:SS`、`HH:MM:SS`，秒可带小数；默认片头从 0 秒开始。以下时间仅演示用法，不是已验证的节目片头。

```sh
# 整部剧默认跳到 01:30；片名可选，不参与匹配
python3 scripts/intro.py 'https://www.olevod.com/player/vod/2-84001-1.html' 01:30 --title '兰香如故'

# 第 1 集有开场剧情：片头从 02:00 开始，跳到 03:30
python3 scripts/intro.py 'https://www.olevod.com/player/vod/2-84001-1.html' 03:30 --intro-start 02:00 --episode

# 第 8 集不跳过，覆盖整部剧默认值
python3 scripts/intro.py 'https://www.olevod.com/player/vod/2-84001-8.html' --disable --episode

# 直接写入另外一个本地标注仓库的发布根目录
python3 scripts/intro.py 'https://www.olevod.com/player/vod/2-84001-1.html' 90 --output-dir ../couchworld-data
```

默认在本工具仓库根目录生成 `api/v1/intros/olevod/84001.json`（不依赖命令执行目录）；指定 `--output-dir` 时，在该目录下生成同样的 `api/v1/intros/olevod/{剧集ID}.json` 路径。工具离线解析链接，不请求欧乐／GitHub，不需要 Token，不自动提交或发布标注。

生成格式示例：

```json
{
  "schemaVersion": 1,
  "provider": "olevod",
  "vodId": 84001,
  "title": "兰香如故",
  "defaultIntro": { "start": 0, "end": 90 },
  "episodes": {
    "1": { "start": 120, "end": 210 },
    "8": { "disabled": true }
  }
}
```

`start` 为片头开始，`end` 为跳转目标，单位均为秒。约定单集标注优先于整部剧默认值，再回退到网站有效标注；`disabled: true` 明确禁用该层级的跳过，`defaultIntro: null` 表示没有整部剧默认标注。单独使用 `--disable` 会禁用整部剧默认跳过，但仍保留优先级更高的单集例外。删除某个单集例外可编辑 JSON、移除对应集数条目。

重复运行会更新相同剧集／集数的标注，保留其他集数、整部剧默认值及额外元数据。无效时间、损坏 JSON、重复键、版本或剧集 ID 不一致时拒绝写入；成功写入采用同目录临时文件替换。修改现有整部剧默认值不会清除单集例外。

本项目负责生成与维护标注 JSON；CouchWorld App 只消费发布后的数据，不包含本工具。App 的 HTTPS 读取、缓存及播放器接入由 App 项目独立实现。

## 验证结果

```sh
python3 -m unittest discover -s tests -v
```

当前包含 **43 项测试**，安装 NumPy 后全部执行；没有 NumPy 时会跳过依赖它的音频与主入口测试。覆盖时间解析、标注合并、音频匹配、保守边界、集数解析与连续帧确认、CLI 参数、榜单排序、已有标注跳过、`--force`、离线快照、不同规模报告、API 自动写入、强制覆盖及单集例外保留。

2026-10-15 已验证四种在线榜单接口均可返回 20 条数据；单剧已有标注时在请求详情前跳过，强制离线重分析《美人余》仍得到 1:47。真实视频 OCR 使用 macOS Vision；Linux Tesseract 后端尚未实测。测试使用模拟数据验证 CLI 控制流程，不会批量下载视频。

以下为本机实验记录。生成的 HTML／JSON 报告保存在本地 `reports/`，不纳入版本控制；需要报告时按上面的命令重新生成。

| 实验 | 样本与结论 |
| --- | --- |
| 早期音频实验 | 两部剧第 1–3 集，保留早期取样和分析证据 |
| 前十榜单原报告／JSON | 当日周榜、第 1–3 集；历史取样范围不代表当前默认规则 |
| 接受广告标准的修订报告／JSON | 同一批样本，7 部可接受候选、3 部证据不足 |
| 美人余补充报告／JSON | 第 2–4 集候选均为 1:47；OCR 在 2:05、1:59、1:54 找到正确集数，标记时间不一致 |
| 蒸蒸日上补充报告／JSON | 第 2–4 集候选均为 1:28；三集 OCR 均在 1:31–1:32 核对正确集数 |

历史报告记录各次实验当时的行为；当前默认取样为第 2–4 集，确认后应用全剧。补充报告已经加入自动 OCR 结果，不能把历史十部榜单报告理解为按新规则全部重跑过。

## 数据与发布

每部连续剧一个 `api/v1/intros/olevod/{ID}.json`。仓库中现有默认标注：

| ID | 剧名 | 跳转点 | 来源 |
| --- | --- | --- | --- |
| 51882 | 一念关山 | 1:44（104 秒） | 三集检测结果，默认应用全剧 |
| 83912 | 交锋 | 1:30（90 秒） | 三集检测结果，默认应用全剧 |
| 83925 | 死有对证【粤语】 | 0:56（56 秒） | 三集检测结果，默认应用全剧 |
| 84001 | 兰香如故 | 1:50（110 秒） | 原人工标注；本轮待复核，保留原值 |
| 84094 | 一瓯春 | 1:46（106 秒） | 三集检测结果，默认应用全剧 |
| 85101 | 无可替代 | 1:36（96 秒） | 三集检测结果，默认应用全剧 |
| 85141 | 余红旧事 | 0:56（56 秒） | 三集检测结果，默认应用全剧 |
| 85232 | 魅影神捕 | 1:41（101 秒） | 三集检测结果，默认应用全剧 |
| 85411 | 喜剧之王 | 1:45（105 秒） | 三集检测结果，默认应用全剧 |
| 85528 | 美人余 | 1:47（107 秒） | 三集检测结果，默认应用全剧 |
| 85545 | 伟大的长征 | 1:38（98 秒） | 三集检测结果，默认应用全剧 |
| 85566 | 蒸蒸日上 | 1:28（88 秒） | 三集检测结果，默认应用全剧 |
| 85646 | 玉簟秋 | 1:54（114 秒） | 三集检测结果，默认应用全剧 |

公开仓库为 [pookyl/couchworld-intro-data](https://github.com/pookyl/couchworld-intro-data)，[GitHub Pages](https://pookyl.github.io/couchworld-intro-data/) 从 `main` 分支根目录发布。**本地写入或提交不等于线上更新**；提交推送后还需等待 Pages 部署完成。

数据 URL：`https://pookyl.github.io/couchworld-intro-data/api/v1/intros/olevod/{ID}.json`。没有文件时 HTTP 404 表示没有自定义标注；网络错误不是标注删除。若未来改用数据库，保留相同 URL 路径、字段和单集例外优先级。

仓库保存代码、正式标注、测试和文档。生成的报告及内嵌截图保存在被 Git 忽略的 `reports/`；视频、音频、原始取流地址、下载日志与 OCR 编译缓存保存在被 Git 忽略的 `work/`。不提交 Token、Cookie 或账号配置。首次克隆不包含这两个目录中的本地产物，需先取样才能生成报告或离线重跑。网络取样最多 4 路并发，每次最多 120 秒、失败最多重试一次。
