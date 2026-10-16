#!/usr/bin/env python3
"""Generate per-series intro JSON for static HTTPS hosting (no network required)."""

import argparse
import json
import math
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import urlsplit


def parse_time(value):
    """Accept seconds, MM:SS or HH:MM:SS, including fractional seconds."""
    if not re.fullmatch(r"[0-9]+(?::[0-9]+){0,2}(?:\.[0-9]+)?", value):
        raise argparse.ArgumentTypeError("时间请用秒数、MM:SS 或 HH:MM:SS，例如 90、01:30、00:01:30.5")
    parts = value.split(":")
    numbers = [float(part) for part in parts]
    if len(parts) > 1 and numbers[-1] >= 60:
        raise argparse.ArgumentTypeError("冒号后的秒数必须小于 60")
    if len(parts) == 3 and numbers[1] >= 60:
        raise argparse.ArgumentTypeError("HH:MM:SS 的分钟数必须小于 60")
    seconds = 0
    for number in numbers:
        seconds = seconds * 60 + number
    if not math.isfinite(seconds):
        raise argparse.ArgumentTypeError("时间必须是有限数值")
    return int(seconds) if seconds.is_integer() else seconds


def parse_episode_link(value):
    try:
        url = urlsplit(value)
        valid = (url.scheme == "https" and url.hostname in {"olevod.com", "www.olevod.com"}
                 and url.username is None and url.password is None and url.port in {None, 443})
    except ValueError:
        valid = False
    if not valid:
        raise argparse.ArgumentTypeError("请提供欧乐 HTTPS 播放链接，例如 https://www.olevod.com/player/vod/2-84001-1.html")
    match = re.fullmatch(r"/player/vod/2-([1-9][0-9]*)-([1-9][0-9]*)\.html", url.path)
    if not match:
        raise argparse.ArgumentTypeError("需要连续剧某一集的播放链接：/player/vod/2-{剧集ID}-{集数}.html")
    return tuple(int(number) for number in match.groups())


def validate_marker(marker, label):
    if not isinstance(marker, dict):
        raise ValueError(f"{label} 必须是标注对象")
    if marker.get("disabled") is True:
        if "start" in marker or "end" in marker:
            raise ValueError(f"{label} 禁用标注不能同时包含时间")
        return
    if "disabled" in marker:
        raise ValueError(f"{label}.disabled 只接受 true；启用标注请使用 start/end")
    start, end = marker.get("start"), marker.get("end")
    if any(type(value) not in (int, float) or not math.isfinite(value) for value in (start, end)):
        raise ValueError(f"{label} 的 start/end 必须是有限秒数")
    if start < 0 or end <= start:
        raise ValueError(f"{label} 必须满足 0 <= start < end")


def validate_document(document, vod_id):
    if not isinstance(document, dict):
        raise ValueError("现有文件必须是 JSON 对象")
    if type(document.get("schemaVersion")) is not int or document["schemaVersion"] != 1:
        raise ValueError("现有文件 schemaVersion 必须为 1")
    if document.get("provider") != "olevod" or type(document.get("vodId")) is not int or document["vodId"] != vod_id:
        raise ValueError("现有文件的 provider/vodId 与输入链接不一致")
    if document.get("defaultIntro") is not None:
        validate_marker(document["defaultIntro"], "defaultIntro")
    episodes = document.get("episodes")
    if not isinstance(episodes, dict):
        raise ValueError("现有文件 episodes 必须是对象")
    for index, marker in episodes.items():
        if not re.fullmatch(r"[1-9][0-9]*", index):
            raise ValueError(f"现有文件集数无效：{index}")
        validate_marker(marker, f"episodes.{index}")


def reject_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"现有 JSON 有重复键：{key}")
        result[key] = value
    return result


def save_marker(root, vod_id, episode, marker, series=True, title=None):
    target = root / "api" / "v1" / "intros" / "olevod" / f"{vod_id}.json"
    if target.exists():
        document = json.loads(target.read_text(encoding="utf-8"), object_pairs_hook=reject_duplicate_keys)
        validate_document(document, vod_id)
    else:
        document = {"schemaVersion": 1, "provider": "olevod", "vodId": vod_id,
                    "defaultIntro": None, "episodes": {}}
    if title is not None:
        document["title"] = title
    if series:
        document["defaultIntro"] = marker
    else:
        document["episodes"][str(episode)] = marker
    validate_document(document, vod_id)
    document["episodes"] = dict(sorted(document["episodes"].items(), key=lambda item: int(item[0])))
    content = json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    # Validate and serialize before touching the destination. Replace atomically so
    # an interrupted write cannot leave a partial published JSON document.
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=target.parent,
                                         prefix=f".{vod_id}-", suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(content)
        os.replace(temporary, target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return target, document


def main(argv=None):
    parser = argparse.ArgumentParser(description="根据欧乐单集链接生成／更新片头标注 JSON；只写本地文件，无需网络或 Token。")
    parser.add_argument("link", type=parse_episode_link, help="连续剧某一集的 HTTPS 播放链接")
    parser.add_argument("time", nargs="?", type=parse_time, help="跳过片头后的播放位置（正片开始时间），例如 90 或 01:30")
    parser.add_argument("--intro-start", type=parse_time, default=0, help="片头开始时间，默认 0；用于片头前有剧情的情况")
    parser.add_argument("--episode", action="store_true", help="仅标注链接中的这一集；默认设置整部剧并保留单集例外")
    parser.add_argument("--disable", action="store_true", help="明确禁用跳过，不填写时间；默认禁用整部剧，可与 --episode 合用")
    parser.add_argument("--title", help="可选片名，仅供人工阅读，不参与匹配")
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).resolve().parents[1], help="发布根目录，默认本工具仓库根目录；自动创建 api/v1/intros/olevod/{ID}.json")
    args = parser.parse_args(argv)
    if args.disable:
        if args.time is not None or args.intro_start != 0:
            parser.error("--disable 不能与时间标注一起使用")
        marker = {"disabled": True}
    else:
        if args.time is None:
            parser.error("请提供正片开始时间，或使用 --disable")
        marker = {"start": args.intro_start, "end": args.time}
    vod_id, episode = args.link
    try:
        validate_marker(marker, "新标注")
        target, document = save_marker(args.output_dir, vod_id, episode, marker, not args.episode, args.title)
    except (ValueError, OSError, OverflowError) as error:
        parser.exit(1, f"错误：{error}\n")
    scope = "整部剧默认值" if not args.episode else f"第 {episode} 集"
    detail = "禁用跳过" if args.disable else f"片头 {args.intro_start}～{args.time} 秒；跳转到 {args.time} 秒"
    print(f"已保存：{target.resolve()}\n剧集 {vod_id}／{scope}：{detail}")
    if not args.episode and document["episodes"]:
        print(f"保留 {len(document['episodes'])} 个单集例外，单集例外优先于整部剧默认值。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
