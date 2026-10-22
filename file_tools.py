"""File tool schemas and local review/approval workflow."""
import difflib
import json
import shlex
from pathlib import Path, PurePosixPath


def tool(name, description, **properties):
    return {"type": "function", "name": name, "description": description,
            "parameters": {"type": "object", "properties": properties,
                           "required": list(properties), "additionalProperties": False}}


STRING = {"type": "string"}
TOOLS = [
    tool("file_roots", "List configured remote Linux file roots. All file paths must stay inside these roots."),
    tool("list_directory", "List up to 200 remote directory entries; symlinks cannot be followed.", path=STRING),
    tool("read_file", "Read UTF-8 text (file <=64 KiB), with SHA256 for later edits. Lines are 1-based.",
         path=STRING, start_line={"type": "integer", "minimum": 1}, line_count={"type": "integer", "minimum": 1, "maximum": 200}),
    tool("search_files", "Search literal text recursively in small UTF-8 files. Bounded scan; no regex or shell.", path=STRING, query=STRING),
    tool("touch_file", "Create an empty file, or update timestamps without changing existing content. Requires human approval. Parent directory must exist.", path=STRING),
    tool("write_file", "Create or replace UTF-8 text after human review. Read existing file first; pass its SHA256, or MISSING for a new file. Backups follow user configuration.",
         path=STRING, content=STRING, expected_sha256=STRING),
    tool("replace_text", "Replace exactly one occurrence of old_text in an existing file, preserving other content. Read first and provide its SHA256. Requires human approval.",
         path=STRING, old_text=STRING, new_text=STRING, expected_sha256=STRING),
    tool("make_directory", "Create one directory under an existing parent after human approval.", path=STRING),
]


def safe_display(text):
    # Prevent remote text from controlling the terminal or hiding the review prompt.
    return "".join(c if c in "\n\t" or (c.isprintable() and c != "\x1b") else ascii(c)[1:-1] for c in text)


def run(name, args, roots, remote, audit, task_id, target, *, sudo=False, backup=False):
    if name == "file_roots":
        return {"roots": roots, "scope": "remote Linux", "max_file_bytes": 65536, "sudo": sudo, "backup": backup}
    if not roots:
        raise ValueError("请先在 .env 设置 OPS_FILE_ROOTS，多个远程目录用逗号分隔")

    def request(op, **values):
        source = Path(__file__).with_name("remote_files.py").read_text(encoding="utf-8")
        wrapper = "import json,sys; p=json.load(sys.stdin); ns={'__name__':'ops_helper'}; exec(compile(p['source'],'ops_helper','exec'),ns); print(json.dumps(ns['execute'](p['request']),ensure_ascii=False))"
        request_data = {"op": op, "roots": roots, "backup": backup, **values}
        command = ("sudo -n /usr/bin/python3 -I -c " if sudo else "python3 -I -c ") + shlex.quote(wrapper)
        payload = json.dumps({"source": source, "request": request_data}, ensure_ascii=False)
        result = json.loads(remote(command, input_text=payload, output_limit=600000))
        if result["exit_code"]:
            raise RuntimeError(result["output"])
        return json.loads(result["output"])

    path = args["path"]
    if not isinstance(path, str) or not path.startswith("/") or any(ord(c) < 32 for c in path):
        raise ValueError("请使用远程 Linux 绝对路径")
    target_path = PurePosixPath(path)
    allowed = [PurePosixPath(root) for root in roots]
    if any(not root.is_absolute() or str(root) == "/" or ".." in root.parts for root in allowed):
        raise ValueError("OPS_FILE_ROOTS 必须是具体的 Linux 绝对目录，不能为根目录或包含 ..")
    if ".." in target_path.parts or not any(target_path == root or root in target_path.parents for root in allowed):
        raise ValueError("路径超出 OPS_FILE_ROOTS 授权范围")
    if name in {"list_directory", "read_file", "search_files"}:
        return request({"list_directory": "list", "read_file": "read", "search_files": "search"}[name], **args)
    if name == "touch_file":
        snapshot = request("touch_snapshot", path=path)
        action = "更新时间戳（保留内容）" if snapshot["exists"] else "创建空文件"
        print(safe_display(f"目标：{target}\n{action}：{path}"))
        change = {"path": path, "expected_version": snapshot["version"]}
        op = "touch"
    elif name == "make_directory":
        snapshot = request("snapshot", path=path)
        if snapshot["exists"]:
            raise ValueError("Path already exists")
        print(safe_display(f"目标：{target}\n创建目录：{path}"))
        change = {"path": path}
        op = "mkdir"
    else:
        snapshot = request("snapshot", path=path)
        if snapshot["sha256"] != args["expected_sha256"]:
            raise ValueError("文件已变化，请重新读取后再编辑")
        old = snapshot["content"]
        if name == "replace_text":
            if not snapshot["exists"] or not args["old_text"] or old.count(args["old_text"]) != 1:
                raise ValueError("old_text 必须在文件中恰好出现一次")
            content = old.replace(args["old_text"], args["new_text"], 1)
        else:
            content = args["content"]
        if len(content.encode("utf-8")) > 65536 or "\0" in content:
            raise ValueError("文件必须是 64 KiB 以内的 UTF-8 文本")
        if snapshot["exists"] and content == old:
            return {"changed": False, "message": "Content already matches"}
        diff = "".join(difflib.unified_diff(old.splitlines(keepends=True), content.splitlines(keepends=True), fromfile=path + " (before)", tofile=path + " (after)"))
        print(safe_display(f"目标：{target}\n文件：{path}\n{diff}\n修改前 {len(old.encode('utf-8'))} 字节；修改后 {len(content.encode('utf-8'))} 字节"))
        op = "write"
        change = {"path": path, "content": content, "expected_sha256": snapshot["sha256"]}
    print(f"执行身份：{'sudo/root' if sudo else 'SSH 账号'}；文件内容备份：{'开启' if backup else '关闭'}")
    try:
        approved = input("输入 apply 或 approve 确认以上操作（不区分大小写）；其他输入取消：").strip().casefold() in {"apply", "approve"}
    except EOFError:
        approved = False
    audit(task_id, "file_approval", {"path": path, "operation": op, "approved": approved, "sudo": sudo, "backup": backup})
    if not approved:
        print("操作已取消，未修改文件。请在审批提示出现时输入 apply 或 approve。")
        return {"approved": False, "changed": False, "message": "Approval cancelled or unavailable; no change made."}
    print("审批通过，正在执行…", flush=True)
    result = request(op, **change)
    return {**result, "approved": True}
