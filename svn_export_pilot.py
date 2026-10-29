import hashlib
import json
import subprocess
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path, PurePosixPath
from urllib.parse import quote


SVN = Path(r"C:\Program Files\TortoiseSVN\bin\svn.exe")
WORKCOPY = Path(r"D:\SVN虚拟助教")
STUDENT = "211706315_黄发平"
AUTHOR = "mayy"
MAX_FILES = 2
OUTPUT_ROOT = Path(r"D:\svn_agent\data\svn_exports")


def run_svn(*args, cwd=WORKCOPY):
    result = subprocess.run(
        [str(SVN), *map(str, args), "--non-interactive"],
        cwd=str(cwd),
        capture_output=True,
        timeout=60,
    )

    if result.returncode != 0:
        error = result.stderr.decode("gbk", errors="replace")
        raise RuntimeError(error.strip())

    return result.stdout


def main():
    # 从工作副本读取仓库根地址和当前基准版本。
    info = ET.fromstring(run_svn("info", "--xml", "."))
    entry = info.find("entry")
    repository_root = entry.findtext("repository/root").rstrip("/")
    snapshot = int(entry.attrib["revision"])

    query_url = (
        repository_root + "/" + quote(STUDENT, safe="")
        + f"@{snapshot}"
    )
    revision_range = f"{snapshot}:1"

    print("查询教师历史记录……")
    raw_log = run_svn(
        "log", "--xml", "--verbose",
        "-r", revision_range, query_url,
    )
    history = ET.fromstring(raw_log)

    # 日志可能包含其他学生的路径，因此再次限定目录。
    prefix = f"/{STUDENT}/论文/"
    candidates = []
    seen = set()

    for commit in history.findall("logentry"):
        if commit.findtext("author") != AUTHOR:
            continue

        revision = int(commit.attrib["revision"])

        for changed in commit.findall("paths/path"):
            path = changed.text or ""
            suffix = PurePosixPath(path).suffix.lower()
            key = (revision, path)

            if (
                not path.startswith(prefix)
                or changed.get("kind") != "file"
                or changed.get("action") not in {"A", "M", "R"}
                or suffix not in {".doc", ".docx"}
                or key in seen
            ):
                continue

            seen.add(key)
            candidates.append({
                "revision": revision,
                "author": AUTHOR,
                "commit_time": commit.findtext("date"),
                "repository_path": path,
                "action": changed.get("action"),
                "copyfrom_path": changed.get("copyfrom-path"),
                "copyfrom_revision": changed.get("copyfrom-rev"),
            })

    candidates.sort(
        key=lambda item: (
            -item["revision"], item["repository_path"]
        )
    )
    selected = candidates[:MAX_FILES]

    if not selected:
        raise RuntimeError("没有找到符合条件的教师论文版本。")

    # 每次使用新目录，保留以往实验结果。
    batch_name = "P001_" + datetime.now().strftime(
        "%Y%m%d_%H%M%S_%f"
    )
    output_dir = OUTPUT_ROOT / batch_name
    output_dir.mkdir(parents=True, exist_ok=False)
    (output_dir / "svn_log.xml").write_bytes(raw_log)

    files = []

    for index, item in enumerate(selected, start=1):
        revision = item["revision"]
        path = item["repository_path"]
        suffix = PurePosixPath(path).suffix.lower()

        source_url = (
            repository_root + quote(path, safe="/")
            + f"@{revision}"
        )
        filename = (
            f"P001_r{revision}_{AUTHOR}_{index:02d}{suffix}"
        )
        destination = output_dir / filename

        record = {
            **item,
            "source_url": source_url,
            "exported_path": str(destination),
            "comments_checked": False,
        }

        try:
            # URL 和 -r 同时固定版本，目标使用英文文件名。
            run_svn(
                "export", "--quiet",
                "-r", revision, source_url, filename,
                cwd=output_dir,
            )

            content = destination.read_bytes()
            record.update({
                "status": "exported",
                "size_bytes": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
            })
            print("已导出：", filename)

        except (
            RuntimeError, OSError, subprocess.TimeoutExpired
        ) as error:
            record.update({
                "status": "failed",
                "error": str(error),
            })
            print("导出失败：", filename)

        files.append(record)

    manifest = {
        "sample_id": "P001",
        "created_at": datetime.now().astimezone().isoformat(),
        "query_url": query_url,
        "snapshot_revision": snapshot,
        "revision_range": revision_range,
        "teacher_author": AUTHOR,
        "path_prefix": prefix,
        "max_files": MAX_FILES,
        "candidate_count": len(candidates),
        "candidates": candidates,
        "files": files,
    }

    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    succeeded = sum(
        item["status"] == "exported" for item in files
    )
    print(f"导出成功：{succeeded}/{len(files)}")
    print("结果目录：", output_dir)
    print("来源清单：", manifest_path)

    if succeeded != len(files):
        raise RuntimeError("存在失败项，详情已保存到来源清单。")


if __name__ == "__main__":
    try:
        main()
    except (
        RuntimeError, OSError,
        subprocess.TimeoutExpired, ET.ParseError,
    ) as error:
        print("任务未完成：", error)
        raise SystemExit(1)