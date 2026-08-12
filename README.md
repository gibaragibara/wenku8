# 轻小说文库 EPUB 下载

An automated crawler and static site generator for light novel ebooks from [轻小说文库](https://www.wenku8.net), featuring multiple download sources, daily updates, and GitHub Actions deployment with [Steel](https://steel.dev).

---

[![Scrape and Deploy](https://github.com/mojimoon/wenku8/actions/workflows/deploy.yml/badge.svg)](https://github.com/mojimoon/wenku8/actions/workflows/deploy.yml)

自动化从 [轻小说文库](https://www.wenku8.net) 获取 EPUB 格式电子书，并将结果整合为网页呈现：

- [mojimoon.github.io/wenku8](https://mojimoon.github.io/wenku8/index.html)：EPUB 源 + TXT 源
    - 内容全面，但条目数多，可能加载较慢
    - 特别感谢 [布客新知](https://github.com/ixinzhi) 整理 
- [mojimoon.github.io/wenku8/epub.html](https://mojimoon.github.io/wenku8/epub.html)：EPUB 源
    - 仅包含 EPUB 源，适合移动端浏览

## Star History

**如果您觉得这个项目有用，点个 Star 支持一下吧！Thanks! 😊**

[![Star History Chart](https://api.star-history.com/svg?repos=mojimoon/wenku8&type=Date)](https://www.star-history.com/#mojimoon/wenku8&Date)

## Usage

克隆仓库并安装依赖：

```bash
git clone https://github.com/mojimoon/wenku8
cd wenku8
pip install -r requirements.txt
```

有 3 种爬虫方式可选：

- `requests`：在使用境内 IP 时推荐使用
- `playwright`：在使用境外 IP 时必须使用，能绕过 Cloudflare 验证
- `steel`：在使用风控 IP（如 GitHub Actions 的服务器）时必须使用 [Steel](https://steel.dev) 平台提供的无头浏览器服务，需注册账号并获取 API Key

如需使用 `playwright` 或 `steel`，还需安装 Playwright 及其浏览器：

```bash
pip install pytest-playwright
playwright install
```

如需使用 `steel`，还需在项目根目录创建 `.env` 文件，内容如下：

```
STEEL_API_KEY=...
```

并填入从 [Steel 控制台](https://app.steel.dev/quickstart) 获取的 API Key。

---

此外，在 wenku8 某次更新后，还需要登录网站来访问论坛内容。可通过以下两种方式提供 Cookie（优先读取 `COOKIE` 文件，否则读取环境变量 `WENKU_COOKIES`）：

```
jieqiUserCharset=utf-8; jieqiVisitId=...; ...
```

- 文件方式：项目根目录创建 `COOKIE` 文件，第一行写入整行 Cookie
- 环境变量方式：设置 `WENKU_COOKIES` 为整行 Cookie

## Workflow

运行 `txt.py`：

- `incremental_scrape()` 获取最新的 TXT 源下载列表
    - 输出：`txt/*.csv`
    - 由于 GitHub API 限制最多显示 1,000 条数据，请检查是否有遗漏。如有，可以手动下载后运行 `filelist_to_csv.py` 进行转换。
- `merge_csv()` 合并、去重
    - 输出：`out/txt_list.csv`

运行 `main.py`：

- `scrape()` 获取最新的 EPUB 源下载列表
    - 输出：`out/dl.txt`, `out/post_list.csv`
- `merge()` 合并、去重并与 TXT 源进行匹配
    - 输出：`out/merged.csv`
- `create_html_merged(), create_html_epub()` 生成 HTML 文件
    - 输出：`docs/index.html`, `docs/epub.html`

运行 `main.py` 时会在生成页面后自动处理蓝奏源 EPUB：

- 首次部署时自动建立基线，不回补历史库存
- 首次部署不会把当前 `merged.csv` 里已有的上千条历史蓝奏记录全部下载一遍
- 之后每次有新条目进入 `merged.csv` 时，自动下载对应蓝奏资源
- 对于已经在基线里的旧 `dl_label`，如果 `dl_update`、卷号或备注发生变化，也会重新下载
- 优先下载简体“合集.zip/.7z/.rar”，没有合集时回退到单卷 EPUB
- 自动提取 EPUB 到本地目录，并过滤 `zht_` / “繁体”文件

如果启用 OneDrive 同步，还会同时启动一个后台守护进程：

- 按小说主标题把 `out/downloads/epubs/` 重新整理到 OneDrive 的 `wenku8` 子目录
- 上传成功并确认远端文件大小正确后，自动删除本地 EPUB 与压缩包
- 可选清理 OneDrive 根目录下历史误传的平铺 EPUB

可通过环境变量关闭该功能：

```bash
ENABLE_LANZOU_DOWNLOAD=false python main.py playwright
```

默认行为（开启下载）：

```bash
python main.py playwright
```

下载输出默认在 `out/downloads/`：

- `out/downloads/archives/`：蓝奏归档文件
- `out/downloads/epubs/`：提取后的简体 EPUB
- `out/downloads/state.json`：基线与已处理状态

换句话说：

- 第一次运行：只写入 `state.json` 基线，不下载历史库存
- 后续运行：只下载基线之后新增的蓝奏更新

此外，GitHub Actions 会每天自动运行抓取流程，并将 `docs/` 目录部署到 GitHub Pages。

## Docker (VPS)

项目已提供：

- `Dockerfile`
- `docker-compose.yml.example`
- `.env.example`

使用方式：

```bash
cp .env.example .env
# 编辑 .env，填写 WENKU_COOKIES（默认每 6 小时跑一次）
cp docker-compose.yml.example docker-compose.yml
# 编辑 docker-compose.yml，把镜像名改成你的 Docker Hub 仓库
docker compose --env-file .env up -d
```

如果要启用 OneDrive 自动上传，还需要在宿主机准备好 `rclone` 的配置文件，并挂载到容器：

```text
./rclone/rclone.conf -> /root/.config/rclone/rclone.conf
```

然后在 `.env` 里至少设置：

```bash
ENABLE_ONEDRIVE_UPLOAD=true
ONEDRIVE_REMOTE_TARGET=wenku8_od:轻小说/wenku8
ONEDRIVE_REMOTE_ROOT=wenku8_od:轻小说
ONEDRIVE_UPLOAD_INTERVAL_SECONDS=120
```

单核 VPS 建议保留默认资源与超时保护：

```bash
WENKU8_CPU_LIMIT=0.70
WENKU8_MEMORY_LIMIT=512m
WENKU8_PIDS_LIMIT=128
SCRAPE_TIMEOUT_SECONDS=1800
LANZOU_RUN_TIMEOUT_SECONDS=600
LANZOU_ENTRY_TIMEOUT_SECONDS=360
LANZOU_MAX_DOWNLOAD_BYTES=2147483648
LANZOU_NAV_TIMEOUT_MS=90000
LANZOU_NAV_RETRIES=3
LANZOU_ITEM_RETRIES=2
```

蓝奏文件使用 Python 流式写盘，不会通过 Playwright 在 Node 内存中缓存整个响应。下载器在独立进程中运行，达到硬超时后会连同 Chromium 子进程一起终止；Compose 的 CPU、内存和 PID 边界可避免异常页面拖垮宿主机。

运行模式：

- 容器常驻运行
- 每 `RUN_INTERVAL_SECONDS` 秒执行一次：`txt.py` + `main.py $SCRAPER`
- `ENABLE_LANZOU_DOWNLOAD=true` 时，有新内容会自动下载并提取蓝奏 EPUB
- `ENABLE_ONEDRIVE_UPLOAD=true` 时，容器会额外启动 OneDrive 上传/清理守护进程
- `ENABLE_LANZOU_DOWNLOAD=false` 时，只抓取与生成页面，不执行蓝奏下载
- 首次初始化时只建立下载基线，不会扫历史库存
- OneDrive 上传成功后，会自动删除本地 `out/downloads/epubs/` 与 `out/downloads/archives/` 中对应文件
- 如需手动补抓历史条目，再单独使用 `lanzou_epub_downloader/` 并显式开启 `--include-existing`

静态页面会持续更新到 `docs/` 目录，可直接交给 Caddy/Nginx 等服务托管。

## 独立蓝奏 EPUB 下载器

仓库中额外提供了一个独立项目：

```text
lanzou_epub_downloader/
```

用途：

- 只消费主项目产出的 `out/merged.csv` 与 `out/dl.txt`
- 首次部署时只建立基线，不回补历史库存
- 优先下载简体蓝奏合集；没有合集时回退到单卷 EPUB
- 自动过滤 `zht_` 和“繁体”命名文件

它适合这种部署方式：

- 主 `wenku8` 容器负责抓列表与生成页面
- 独立蓝奏下载器负责把蓝奏源 EPUB 真正落到 VPS 本地目录

详细说明见：

- [lanzou_epub_downloader/README.md](lanzou_epub_downloader/README.md)

## GitHub DockerHub CI

新增工作流 `.github/workflows/dockerhub.yml`：

- 每次 push / PR 先做 Python 编译检查（`py_compile`）
- push 到 `main` 且编译通过后，自动构建并推送 Docker 镜像到 Docker Hub

需要在仓库 Secrets 中配置：

- `DOCKERHUB_USERNAME`
- `DOCKERHUB_TOKEN`

## Remarks

为加快访问速度，HTML、CSS、JS 文件均已压缩（源代码在 `source` 目录下），且使用 jsDeliver CDN 加速。  

> 可参考本人博客中 [加快 GitHub Pages 国内访问速度](https://mojimoon.github.io/blog/2025/speedup-github-page/) 一文。

## License

[MIT License](LICENSE)
