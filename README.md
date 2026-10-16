# CouchWorld 片头标注数据工具

独立于 Roku App 的命令行工具与标注数据仓库。使用 Python 3.8+ 标准库，无需安装第三方依赖。

在本目录运行下列命令；默认输出也始终位于本仓库。

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

验证命令：`python3 -m unittest discover -s tests -v`。


## 发布与数据约定

每部连续剧一个 `api/v1/intros/olevod/{ID}.json`。公开仓库为 [pookyl/couchworld-intro-data](https://github.com/pookyl/couchworld-intro-data)，GitHub Pages 从 `main` 分支根目录发布。提交并推送标注后，等待 Pages 部署完成即可访问。

数据 URL：`https://pookyl.github.io/couchworld-intro-data/api/v1/intros/olevod/{ID}.json`。

当前用户提供的全剧默认标注：84001 跳到 110 秒，85232 跳到 114 秒。只发布经过核实的时间，前面的示例命令不代表真实标注。

若未来改用数据库，保留相同 URL 路径、JSON 字段与优先级。没有文件时 HTTP 404 表示没有自定义标注；网络错误不是标注删除。公开仓库中只存标注，不放 Token、Cookie、账号配置或视频文件。
