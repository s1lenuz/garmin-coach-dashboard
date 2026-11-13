"""一次性把项目推上 GitHub 并启用 Pages。

为什么不用 `git push`：本机 github.com 被网络策略拦截，但 api.github.com 可直连。
因此这里走 GitHub REST 的 Git Data API（blob → tree → commit → ref）在远端直接创建
初始 commit，全程只访问 api.github.com；之后每日更新由 GitHub Actions 自己 push。

安全：严格白名单上传，`.env`、`data/`、`__pycache__`、密钥、健康明细一律不入库。
用法：
    python scripts/publish_github.py --dry-run     # 只列出将上传的文件
    python scripts/publish_github.py               # 创建仓库 + 初始提交 + Pages + Secrets
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = "https://api.github.com"
OWNER = os.environ.get("GITHUB_OWNER", "s1lenuz")
REPO = os.environ.get("GITHUB_REPO", "garmin-coach-dashboard")
FULL = f"{OWNER}/{REPO}"

# 本地路径 -> 远端路径。仓库根 README 用 GitHub 版；本地桌面版说明另存一份。
ROOT_MAP = {
    # 仓库首页 README 是 GitHub Pages 版；桌面本地版说明另存一份
    "README.md": "README.md",
    "LOCAL_DASHBOARD.md": "LOCAL_DASHBOARD.md",
    ".gitignore": ".gitignore",
    ".env.example": ".env.example",
    "requirements.txt": "requirements.txt",
    "GARMIN_API_RESEARCH.html": "docs/RESEARCH.html",
    "config.py": "config.py",
    "garmin_client.py": "garmin_client.py",
    "normalizer.py": "normalizer.py",
    "analyzer.py": "analyzer.py",
    "llm_interpreter.py": "llm_interpreter.py",
    "mock_data.py": "mock_data.py",
    "build_demo.py": "build_demo.py",
    "server.py": "server.py",
    "launcher.py": "launcher.py",
    "create_shortcut.py": "create_shortcut.py",
    "make_icon.py": "make_icon.py",
    "start_dashboard.vbs": "start_dashboard.vbs",
    "stop_dashboard.vbs": "stop_dashboard.vbs",
}
DIRS = {
    "web": "web",
    "docs": "docs",
    "scripts": "scripts",
    "inbox": "inbox",
    ".github": ".github",
}
SKIP_NAMES = {".env", ".env.tmp", "server.pid", "__pycache__"}
SKIP_SUFFIX = {".pyc", ".pyo", ".tmp"}
BINARY_SUFFIX = {".ico", ".png", ".jpg", ".jpeg"}

# 绝不入库的路径片段（含真实健康数据 / token 缓存）
FORBIDDEN_PARTS = {"__pycache__"}


def log(msg: str) -> None:
    print(f"[publish] {msg}", flush=True)


def gh_token() -> str:
    env = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if env:
        return env.strip()
    out = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, check=True)
    return out.stdout.strip()


TOKEN = ""


def api(method: str, path: str, payload=None, expect=(200, 201, 204)) -> dict:
    url = path if path.startswith("http") else API + path
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "garmin-coach-dashboard-publisher/1.0",
        "Content-Type": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            raw = resp.read().decode("utf-8", "replace")
            if resp.status not in expect:
                raise RuntimeError(f"{method} {path} -> HTTP {resp.status}")
            return json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")[:300]
        raise RuntimeError(f"{method} {path} -> HTTP {exc.code}: {body}") from None


def collect_files() -> dict[str, Path]:
    files: dict[str, Path] = {}
    for src, dst in ROOT_MAP.items():
        p = ROOT / src
        if p.exists():
            files[dst] = p
    for src_dir, dst_dir in DIRS.items():
        base = ROOT / src_dir
        if not base.is_dir():
            continue
        for p in sorted(base.rglob("*")):
            if not p.is_file():
                continue
            rel = p.relative_to(ROOT).as_posix()
            if p.name in SKIP_NAMES or p.suffix.lower() in SKIP_SUFFIX:
                continue
            if any(part in FORBIDDEN_PARTS for part in p.parts):
                continue
            if p.name.startswith("_") and p.suffix == ".py":
                continue
            files[rel] = p
    return dict(sorted(files.items()))


def safety_check(files: dict[str, Path]) -> None:
    """最后一道闸门：任何看起来像凭证 / 本机健康明细的路径都直接拒绝。"""
    bad = [r for r in files if r == ".env" or r.startswith("data/") or r.endswith(".env")]
    if bad:
        raise RuntimeError(f"拒绝上传敏感路径：{bad}")
    for remote, local in files.items():
        if local.suffix.lower() in BINARY_SUFFIX:
            continue
        text = local.read_text(encoding="utf-8", errors="replace")
        if "GARMIN_PERSONAL_API_KEY=" in text and "****" not in text:
            # .env.example / README 里出现的是空值或说明文字，这里只拦截「等号后跟着真实值」
            for line in text.splitlines():
                s = line.strip()
                if s.startswith("GARMIN_PERSONAL_API_KEY=") and len(s.split("=", 1)[1].strip()) > 20:
                    raise RuntimeError(f"{remote} 疑似含真实 Key，拒绝上传")
                if s.startswith("DEEPSEEK_API_KEY=") and len(s.split("=", 1)[1].strip()) > 20:
                    raise RuntimeError(f"{remote} 疑似含真实 Key，拒绝上传")


def create_repo() -> bool:
    """返回是否为新建仓库。"""
    try:
        api("GET", f"/repos/{FULL}")
        log(f"仓库 {FULL} 已存在，跳过创建")
        return False
    except RuntimeError:
        pass
    api("POST", "/user/repos", {
        "name": REPO,
        "description": "Garmin 健康大屏：每日 06:47 自动同步睡眠 / HRV / 卡路里，DeepSeek 解读 + 教练周计划，GitHub Pages 托管",
        "homepage": f"https://{OWNER}.github.io/{REPO}/",
        "private": False,
        "auto_init": False,
        "has_issues": True,
        "has_wiki": False,
        "has_projects": False,
    })
    log(f"已创建公开仓库 {FULL}")
    return True


def seed_if_empty() -> tuple[str, str]:
    """空仓库无法直接调用 Git Data API（409 Git Repository is empty）。

    先用 contents API 造一个占位 commit 作为种子，再返回 (commit_sha, 分支名)。
    已有 ref 的仓库直接返回当前 HEAD。
    """
    info = api("GET", f"/repos/{FULL}")
    branch = info.get("default_branch") or "main"
    try:
        ref = api("GET", f"/repos/{FULL}/git/ref/heads/{branch}")
        log(f"检测到已有分支 {branch}，跳过种子提交")
        return ref["object"]["sha"], branch
    except RuntimeError:
        pass
    seed = base64.b64encode(
        "# Garmin 健康大屏\n\n初始化占位文件，稍后由 publish_github.py 覆盖为完整项目。\n".encode("utf-8")
    ).decode("ascii")
    obj = api("PUT", f"/repos/{FULL}/contents/README.md", {
        "message": "chore: initialize repository",
        "content": seed,
    })
    sha = obj["commit"]["sha"]
    branch = api("GET", f"/repos/{FULL}").get("default_branch") or "main"
    log(f"已创建种子提交 {sha[:8]}（分支 {branch}）")
    return sha, branch


def initial_commit(files: dict[str, Path]) -> str:
    # 幂等：若项目文件已在远端，直接跳过，避免重复刷历史
    try:
        api("GET", f"/repos/{FULL}/contents/docs/index.html")
        log("远端已有项目文件，跳过初始提交")
        return ""
    except RuntimeError:
        pass

    parent_sha, branch = seed_if_empty()
    tree = []
    for i, (remote, local) in enumerate(files.items(), 1):
        raw = local.read_bytes()
        blob = api("POST", f"/repos/{FULL}/git/blobs", {
            "content": base64.b64encode(raw).decode("ascii"),
            "encoding": "base64",
        })
        tree.append({"path": remote, "mode": "100644", "type": "blob", "sha": blob["sha"]})
        if i % 10 == 0 or i == len(files):
            log(f"上传文件 {i}/{len(files)}")
    # 分批累加建树：单次提交 54 个 entry 在新建仓库上偶发 404，小批 + base_tree 更稳
    tree_sha = ""
    for start in range(0, len(tree), 15):
        batch = tree[start:start + 15]
        payload = {"tree": batch}
        if tree_sha:
            payload["base_tree"] = tree_sha
        try:
            obj = api("POST", f"/repos/{FULL}/git/trees", payload)
        except RuntimeError as exc:
            preview = [e["path"] for e in batch]
            raise RuntimeError(f"建树失败（第 {start // 15 + 1} 批，路径 {preview}）：{exc}") from None
        tree_sha = obj["sha"]
    log(f"建树完成：{len(tree)} 个条目，{tree_sha[:8]}")

    commit = api("POST", f"/repos/{FULL}/git/commits", {
        "message": "feat: Garmin 健康大屏 · 每日快照 + DeepSeek 解读 + 教练周计划",
        "tree": tree_sha,
        "parents": [parent_sha],
    })
    if branch == "main":
        api("PATCH", f"/repos/{FULL}/git/refs/heads/main", {"sha": commit["sha"]})
    else:
        api("POST", f"/repos/{FULL}/git/refs", {"ref": "refs/heads/main", "sha": commit["sha"]})
    api("PATCH", f"/repos/{FULL}", {"default_branch": "main"})
    log(f"初始提交完成：{commit['sha'][:8]} · {len(files)} 个文件")
    return commit["sha"]


def enable_pages() -> str:
    try:
        obj = api("GET", f"/repos/{FULL}/pages")
        log(f"Pages 已启用：{obj.get('html_url')}")
        return obj.get("html_url", "")
    except RuntimeError:
        pass
    try:
        obj = api("POST", f"/repos/{FULL}/pages", {"source": {"branch": "main", "path": "/docs"}})
        url = obj.get("html_url", f"https://{OWNER}.github.io/{REPO}/")
        log(f"Pages 已启用（main /docs）：{url}")
        return url
    except RuntimeError as exc:
        log(f"Pages 自动启用失败：{exc}")
        log("请在 Settings → Pages → Build and deployment 手动选择 Deploy from a branch: main /docs")
        return ""


def set_secrets_from_env() -> list[str]:
    """把本机 .env 里已有的 Key 写入仓库 Actions Secrets（不打印明文）。"""
    env_path = ROOT / ".env"
    values: dict[str, str] = {}
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if not s or s.startswith("#") or "=" not in s:
                continue
            k, v = s.split("=", 1)
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if k in ("GARMIN_PERSONAL_API_KEY", "DEEPSEEK_API_KEY") and v:
                values[k] = v
    if not values:
        log("本机 .env 未发现可用 Key，跳过 Secrets 设置（稍后请在仓库 Settings 手动添加）")
        return []

    # 用 --env-file 批量写入，密钥不出现在命令行参数里
    import tempfile
    fd, tmp = tempfile.mkstemp(prefix="gh-secrets-", suffix=".env", text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            for k, v in values.items():
                f.write(f"{k}={v}\n")
        r = subprocess.run(
            ["gh", "secret", "set", "--env-file", tmp, "--repo", FULL],
            capture_output=True, text=True,
        )
        if r.returncode == 0:
            log(f"已写入 Actions Secrets：{', '.join(sorted(values))}")
            return sorted(values)
        log(f"批量写入失败（{r.stderr.strip()[:120]}），改用逐个写入")
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass

    done = []
    for k, v in values.items():
        r = subprocess.run(
            ["gh", "secret", "set", k, "--repo", FULL, "--body", v],
            capture_output=True, text=True,
        )
        if r.returncode == 0:
            done.append(k)
        else:
            log(f"Secret {k} 写入失败：{r.stderr.strip()[:120]}")
    log(f"已写入 Actions Secrets：{', '.join(done) or '无'}")
    return done


def set_variables() -> None:
    """非敏感运行参数用 Repository Variables，方便网页直接改。"""
    pairs = [("GARMIN_REGION", os.environ.get("SITE_REGION", "global"))]
    for name, value in pairs:
        path = f"/repos/{FULL}/actions/variables"
        try:
            api("POST", path, {"name": name, "value": value})
            log(f"Variable {name}={value}")
        except RuntimeError:
            try:
                api("PATCH", f"{path}/{name}", {"name": name, "value": value})
                log(f"Variable {name} 已更新")
            except RuntimeError as exc:
                log(f"Variable {name} 未设置（workflow 会使用默认值）：{str(exc)[:100]}")


def dispatch() -> None:
    payload = {"ref": "main", "inputs": {"demo": "false"}}
    for attempt in range(4):
        try:
            api("POST", f"/repos/{FULL}/actions/workflows/daily.yml/dispatches", payload, expect=(204,))
            log("已触发首次 daily workflow")
            return
        except RuntimeError as exc:
            if attempt == 3:
                log(f"自动触发失败（{str(exc)[:120]}）；请在 Actions 页手动 Run workflow")
                return
            time.sleep(3)


def main() -> int:
    global TOKEN
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-secrets", action="store_true", help="不读取本机 .env，也不写入 Secrets")
    ap.add_argument("--no-dispatch", action="store_true")
    ap.add_argument("--no-initial", action="store_true",
                    help="跳过初始提交（内容已用 git push 上传时），只做 Pages / Secrets / 触发")
    args = ap.parse_args()

    files = collect_files()
    safety_check(files)
    log(f"待上传 {len(files)} 个文件：")
    for remote in files:
        log("  " + remote)
    if args.dry_run:
        log("dry-run 结束，未访问网络")
        return 0

    TOKEN = gh_token()
    me = api("GET", "/user")
    log(f"已登录 GitHub：{me['login']}")

    create_repo()
    if not args.no_initial:
        initial_commit(files)
    else:
        log("按 --no-initial 跳过初始提交（假定已通过 git push 上传）")
    enable_pages()
    if not args.no_secrets:
        set_secrets_from_env()
    set_variables()
    if not args.no_dispatch:
        dispatch()
    log(f"完成：https://github.com/{FULL}  ·  https://{OWNER}.github.io/{REPO}/")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"[publish] 失败：{exc}", file=sys.stderr)
        raise SystemExit(1)
