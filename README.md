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
    - 输出：`public/index.html`, `public/epub.html`

运行 `main.py` 时会在生成页面后自动下载本次更新条目里的蓝奏“合集”压缩包（`.zip/.7z/.rar`）：

```bash
python main.py playwright
```

下载目录默认是 `out/downloads`，文件会自动重命名为“书名 + 扩展名”。

此外，GitHub Actions 会每天自动运行 `main.py`，将 `public/` 目录提交到 `gh-pages` 分支并部署到 GitHub Pages。

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

运行模式：

- 容器常驻运行
- 每 `RUN_INTERVAL_SECONDS` 秒执行一次：`txt.py` + `main.py $SCRAPER`
- 有新内容时自动下载蓝奏“合集”压缩包并重命名
- 首次初始化（无 `out/post_list.csv`）仅尝试下载最新 1 条用于测试，不会扫历史库存

静态页面会持续更新到 `docs/` 目录，可直接交给 Caddy/Nginx 等服务托管。

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
