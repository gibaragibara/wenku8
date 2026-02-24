import requests
from bs4 import BeautifulSoup
import csv
import time
import random
from urllib.parse import urljoin
import sys
import argparse
from requests.adapters import HTTPAdapter
from requests.packages.urllib3.util.retry import Retry
import re
import json
import os
import pandas as pd
import sys

BASE_URL = 'https://www.wenku8.net/modules/article/reviewslist.php'
params = { 'keyword': '8691', 'charset': 'utf-8', 'page': 1 }
# 'requests' | 'playwright' | 'steel'
_scraper = 'steel'
user_agents = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/109.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/109.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/108.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/108.0.0.0 Safari/537.36',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/108.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.1 Safari/605.1.15',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 13_1) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.1 Safari/605.1.15',
]
HEADERS = { 
    # 'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3',
    'User-Agent': random.choice(user_agents),
    'Referer': 'https://www.wenku8.net/',
}
DOMAIN = 'https://www.wenku8.net'
JSDELIVR_CDN = 'https://gcore.jsdelivr.net/gh/mojimoon/wenku8@gh-pages/'
OUT_DIR = 'out'
PUBLIC_DIR = 'docs'
COOKIE_FILE = os.path.join(os.path.dirname(__file__), 'COOKIE')
POST_LIST_FILE = os.path.join(OUT_DIR, 'post_list.csv')
TXT_LIST_FILE = os.path.join(OUT_DIR, 'txt_list.csv')
DL_FILE = os.path.join(OUT_DIR, 'dl.txt')
MERGED_CSV = os.path.join(OUT_DIR, 'merged.csv')
EPUB_HTML = os.path.join(PUBLIC_DIR, 'epub.html')
MERGED_HTML = os.path.join(PUBLIC_DIR, 'index.html')
DOWNLOAD_DIR = os.path.join(OUT_DIR, 'downloads')
_prefix = ''

retry_strategy = Retry(
    total=5,
    status_forcelist=[500, 502, 503, 504],
    backoff_factor=2
)
session = requests.Session()
adapter = HTTPAdapter(max_retries=retry_strategy)
session.mount('http://', adapter)
session.mount('https://', adapter)
session.headers.update(HEADERS)
COOKIE_ENV_KEYS = ('WENKU_COOKIES', 'COOKIE')

def parse_cookie_line(line: str):
    line = line.strip()
    if not line:
        return {}
    cookie_dict = {}
    for part in line.split(';'):
        part = part.strip()
        if not part or '=' not in part:
            continue
        k, v = part.split('=', 1)
        cookie_dict[k.strip()] = v.strip()
    return cookie_dict

def load_cookie_dict(filepath: str):
    line = ''
    if os.path.exists(filepath):
        with open(filepath, 'r', encoding='utf-8') as f:
            # 只取第一行，整行都是 "k1=v1; k2=v2; ..."
            line = f.readline().strip()
    if not line:
        for key in COOKIE_ENV_KEYS:
            env_line = os.getenv(key, '').strip()
            if env_line:
                line = env_line
                break
    return parse_cookie_line(line)

COOKIE_DICT = load_cookie_dict(COOKIE_FILE)
if COOKIE_DICT:
    jar = requests.utils.cookiejar_from_dict(COOKIE_DICT)
    session.cookies.update(jar)

browser = None
playwright_ctx_cookie_dict = None
steel_dict = None
playwright_driver = None

def get_playwright_driver():
    from playwright.sync_api import sync_playwright
    global playwright_driver
    if playwright_driver is None:
        playwright_driver = sync_playwright().start()
    return playwright_driver

def shutdown_playwright_driver():
    global playwright_driver
    if playwright_driver is not None:
        try:
            playwright_driver.stop()
        except Exception:
            pass
        playwright_driver = None

def init_playwright():
    global browser, playwright_ctx_cookie_dict
    if browser is None:
        playwright = get_playwright_driver()
        browser = playwright.chromium.launch(
            headless=True,
            args=['--no-sandbox', '--disable-setuid-sandbox']
        )
        # 预解析 COOKIE，供后面 new_context 使用
        playwright_ctx_cookie_dict = dict(COOKIE_DICT)
    return browser

def init_steel():
    from steel import Steel
    from dotenv import dotenv_values
    global browser, playwright_ctx_cookie_dict, steel_dict
    steel_api_key = os.getenv('STEEL_API_KEY', '').strip()
    if not steel_api_key:
        steel_api_key = dotenv_values().get('STEEL_API_KEY', '').strip()
    if not steel_api_key:
        raise RuntimeError('[ERROR] STEEL_API_KEY is empty. Set env STEEL_API_KEY or provide .env in /app.')
    client = Steel(steel_api_key=steel_api_key)
    steel_session = client.sessions.create(api_timeout=20000)
    print(f'[INFO] Running Steel session: {steel_session.id}')
    steel_dict = {
        'api_key': steel_api_key,
        'session_id': steel_session.id,
        'client': client
    }

    if browser is None:
        playwright = get_playwright_driver()
        browser = playwright.chromium.connect_over_cdp(
            f'wss://connect.steel.dev?apiKey={steel_api_key}&sessionId={steel_session.id}'
        )

        playwright_ctx_cookie_dict = dict(COOKIE_DICT)
    return browser

def reset_browser_state(release_steel: bool = True):
    global browser, steel_dict
    try:
        if browser is not None:
            browser.close()
    except Exception:
        pass
    finally:
        browser = None

    if release_steel and steel_dict:
        try:
            steel_dict['client'].sessions.release(steel_dict['session_id'])
        except Exception:
            pass
        steel_dict = None

def exit_steel():
    reset_browser_state(release_steel=True)

def scrape_page_playwright(url: str):
    global browser, playwright_ctx_cookie_dict
    last_err = None
    for attempt in range(3):
        try:
            if browser is None:
                browser = (init_steel() if _scraper == 'steel' else init_playwright())
            # 每次新建 context，并注入 cookie
            with browser.new_context() as context:
                if playwright_ctx_cookie_dict:
                    cookies = [
                        {
                            "name": k,
                            "value": v,
                            "domain": "www.wenku8.net",
                            "path": "/",
                            # 可按需设置 "httpOnly" / "secure" / "sameSite"
                        }
                        for k, v in playwright_ctx_cookie_dict.items()
                    ]
                    context.add_cookies(cookies)
                page = context.new_page()
                page.goto(url, wait_until='networkidle', timeout=45000)
                if "/login.php" in page.url:
                    raise ValueError(f"[ERROR] Playwright 模式被重定向到登录页，可能需要更新 COOKIE 文件: {page.url}")
                html_content = page.content()
                page.close()
                return html_content
        except ValueError:
            raise
        except Exception as e:
            last_err = e
            # Steel 会话偶发断连时重建会话后重试
            if _scraper == 'steel':
                print(f'[WARN] steel page fetch failed (attempt {attempt + 1}/3): {e}')
                reset_browser_state(release_steel=True)
            if attempt < 2:
                time.sleep(1.5 + attempt)
                continue
    raise last_err

def scrape_page_requests(url: str):
    resp = session.get(url, timeout=10, allow_redirects=True)
    final_url = resp.url
    if '/login.php' in final_url:
        raise ValueError(f"[ERROR] Requests 模式被重定向到登录页，可能需要更新 COOKIE 文件: {final_url}")
    resp.raise_for_status()
    resp.encoding = 'utf-8'
    # with open('debug.html', 'w', encoding='utf-8') as f:
    #     f.write(resp.text)
    return resp.text

def scrape_page(url: str):
    if _scraper == 'playwright' or _scraper == 'steel':
        return scrape_page_playwright(url)
    elif _scraper == 'requests':
        return scrape_page_requests(url)
    else:
        raise ValueError(f"Unknown _scraper: {_scraper}")

def build_url_with_params(base_url: str, params: dict):
    if not params:
        return base_url
    query_string = '&'.join(f"{key}={value}" for key, value in params.items())
    # print(f'[DEBUG] Built URL: {base_url}?{query_string}')
    return f"{base_url}?{query_string}"

# ========== Scraping ==========
last_page = 1
def get_latest_url(post_link: str):
    txt = scrape_page(post_link)

    # <a href="https://paste.gentoo.zip" target="_blank">https://paste.gentoo.zip</a>/EsX5Kx8V
    match = re.search(r'<a href="([^"]+)" target="_blank">([^<]+)</a>(/[^<]+)', txt)
    link = match.group(1) + match.group(3) if match else None
    if link is None:
        # <a href="https://0x0.st/8QWZ.txt" target="_blank">https://0x0.st/8QWZ.txt</a><br>
        match = re.search(r'https:\/\/[^"]+?\.txt(?=")', txt)
        if match:
            link = match.group(0)
        else:
            raise ValueError("[ERROR] Failed to find the latest URL")

    return link

def get_latest(url: str):
    txt = scrape_page(url)
    lines = txt.split('\n')
    flg = [False] * 4
    for i in range(len(lines)):
        if not flg[0] and lines[i].endswith('_杂志连载版'):
            lines[i] = lines[i].replace('_杂志连载版', '')
            flg[0] = True
        elif not flg[1] and lines[i].endswith('_SS'):
            lines[i] = lines[i].replace('_SS', '')
            flg[1] = True
        elif not flg[2] and lines[i].endswith('-Ordinary_days-'):
            lines[i] = lines[i].replace('-Ordinary_days-', ' 莉可丽丝 Ordinary days')
            flg[2] = True
        elif not flg[3] and lines[i].endswith('君若星辰'):
            lines[i] = lines[i].replace('君若星辰', '宛如星辰的你')
            flg[3] = True
    
    txt = '\n'.join(lines)
    # if the content has not changed, exit
    if os.path.exists(DL_FILE):
        with open(DL_FILE, 'r', encoding='utf-8') as f:
            old_txt = f.read()
        if old_txt == txt:
            print('[INFO] Exiting, no update found.')
            sys.exit(0)

    with open(DL_FILE, 'w', encoding='utf-8') as f:
        f.write(txt)

def parse_page(page_num: int, latest_post_link: str = None):
    params['page'] = page_num
    url = build_url_with_params(BASE_URL, params)
    txt = ''
    soup = None
    table = None
    for attempt in range(3):
        txt = scrape_page(url)
        soup = BeautifulSoup(txt, 'html.parser')
        tables = soup.find_all('table', class_='grid')
        if len(tables) >= 2:
            table = tables[1]
            break
        if attempt < 2:
            wait_s = 2 + attempt * 2
            print(f'[WARN] parse_page({page_num}) failed on attempt {attempt + 1}, retry in {wait_s}s')
            time.sleep(wait_s)
    if table is None:
        os.makedirs(OUT_DIR, exist_ok=True)
        debug_html = os.path.join(OUT_DIR, f'debug_reviewslist_page_{page_num}.html')
        with open(debug_html, 'w', encoding='utf-8') as f:
            f.write(txt)
        page_title = ''
        if soup and soup.title and soup.title.string:
            page_title = soup.title.string.strip()
        txt_l = txt.lower()
        if 'cloudflare' in txt_l or 'just a moment' in txt_l or '验证' in txt:
            raise RuntimeError(f'[ERROR] blocked by anti-bot page, debug saved: {debug_html}, title={page_title}')
        if '登录' in txt or 'login' in txt_l:
            raise RuntimeError(f'[ERROR] cookie may be invalid/expired, debug saved: {debug_html}, title={page_title}')
        raise RuntimeError(f'[ERROR] unexpected page structure, debug saved: {debug_html}, title={page_title}')

    rows = table.find_all('tr')[1:]  # skip header row

    flg = [False] * 2
    entries = []
    for (i, tr) in enumerate(rows):
        cols = tr.find_all('td')
        if len(cols) < 2:
            continue
        a_post = cols[0].find('a')
        raw_title = a_post.text.strip()
        if not raw_title.endswith(' epub'):
            continue
        post_title = raw_title[:-5] if raw_title.endswith(' epub') else raw_title
        post_link = a_post['href'] if a_post['href'].startswith('http') else urljoin(DOMAIN, a_post['href'])

        # 检查是否解析到已存在的最新帖子
        if latest_post_link is not None and post_link == latest_post_link:
            return entries, True  # 返回当前已收集的entries，并标记停止

        a_novel = cols[1].find('a')
        novel_title = a_novel.text.strip()
        novel_link = urljoin(DOMAIN, a_novel['href'])
        if not flg[0] and novel_link.endswith('/2751.htm'):
            novel_title = '我们不可能成为恋人！绝对不行。（※似乎可行？）(我怎么可能成为你的恋人，不行不行！)'
            flg[0] = True
        if not flg[1] and novel_link.endswith('/3828.htm'):
            novel_title = 'Tier1姐妹 有名四姐妹没我就活不下去'
            flg[1] = True

        post_title = '"' + post_title + '"'
        novel_title = '"' + novel_title + '"'
        entries.append([post_title, post_link, novel_title, novel_link])

        if page_num == 1 and i == 0:
            get_latest(get_latest_url(post_link))

    if page_num == 1:
        last = soup.find('a', class_='last')
        global last_page
        last_page = int(last.text) if last else 1
    return entries, False

def scrape():
    # 获取POST_LIST_FILE中第一个post_link
    latest_post_link = None
    has_history = False
    try:
        with open(POST_LIST_FILE, 'r', encoding='utf-8') as f:
            next(f, None)  # skip header
            first_line = next(f, '').strip()
            if first_line:
                latest_post_link = first_line.split(',')[1]
                has_history = True
    except FileNotFoundError:
        has_history = False

    all_entries = []
    stop = False

    try:
        # 先爬第一页
        print('[INFO] scrape (1)')
        entries, found = parse_page(1, latest_post_link)
        all_entries.extend(entries)
        stop = found

        # 继续爬剩余页数，直到遇到已存在帖子
        page = 2
        while not stop and page <= last_page:
            print(f'[INFO] scrape ({page}/{last_page})')
            entries, found = parse_page(page, latest_post_link)
            all_entries.extend(entries)
            stop = found
            if stop:
                break
            page += 1
            time.sleep(random.uniform(1, 3))
    finally:
        if _scraper == 'steel':
            exit_steel() # close Steel session
        else:
            reset_browser_state(release_steel=False)
        shutdown_playwright_driver()

    # 新内容在前，拼接后写入
    # with open(POST_LIST_FILE, 'w', encoding='utf-8', newline='') as f:
    #     f.write('post_title,post_link,novel_title,novel_link\n')
    #     for entry in all_entries:
    #         f.write(','.join(entry) + '\n')
    if not has_history:
        with open(POST_LIST_FILE, 'w', encoding='utf-8', newline='') as f:
            f.write('post_title,post_link,novel_title,novel_link\n')
            for entry in all_entries:
                f.write(','.join(entry) + '\n')
    else:
        with open(POST_LIST_FILE, 'r+', encoding='utf-8', newline='') as f:
            # insert between header and first line
            lines = f.readlines()
            lines = lines[:1] + [','.join(entry) + '\n' for entry in all_entries] + lines[1:]
            f.seek(0)
            f.writelines(lines)
    return all_entries, has_history

# ========== Data Processing ==========
def purify(text: str) -> str: # 只保留中文、英文和数字
    text = re.sub(r'[^\u4e00-\u9fa5a-zA-Z0-9]', '', text)
    return text

CN_NUM = { '零': 0, '一': 1, '二': 2, '三': 3, '四': 4, '五': 5, '六': 6, '七': 7, '八': 8, '九': 9, '十': 10 }

def chinese_to_arabic(cn: str) -> int:
    if cn == '十':
        return 10
    elif cn.startswith('十'):
        return 10 + CN_NUM.get(cn[1], 0)
    elif cn.endswith('十'):
        return CN_NUM.get(cn[0], 0) * 10
    elif '十' in cn:
        parts = cn.split('十')
        return CN_NUM.get(parts[0], 0) * 10 + CN_NUM.get(parts[1], 0)
    else:
        return CN_NUM.get(cn, 0)

def replace_chinese_numerals(s: str) -> str:
    match = re.search(r'第([一二三四五六七八九十零]{1,3})卷', s)
    if match:
        cn_num = match.group(1)
        arabic_num = chinese_to_arabic(cn_num)
        s = s.replace(cn_num, f' {arabic_num} ')
    match = re.search(r'第 (\S+) 卷', s)
    if match:
        s = s.replace('第 ', '')
        s = s.replace(' 卷', '')
    return s

IGNORED_TITLES = ['时间', '少女', '再见宣言', '强袭魔女', '秋之回忆', '秋之回忆2', '魔王', '青梅竹马', '弹珠汽水']

def merge():
    df_post = pd.read_csv(POST_LIST_FILE, encoding='utf-8')
    df_post.drop_duplicates(subset=['novel_title'], keep='first', inplace=True)
    df_post.reset_index(drop=True, inplace=True)
    df_post['volume'] = df_post['post_title'].apply(replace_chinese_numerals)
    # df_post['post_main'] = df_post['novel_title'].apply(lambda x: x[:x.rfind('(')] if x[-1] == ')' else x)
    df_post['post_alt'] = df_post['novel_title'].apply(lambda x: x[x.rfind('(')+1:-1] if x[-1] == ')' else "")
    df_post['post_pure'] = df_post['novel_title'].apply(purify)
    df_post['post_alt_pure'] = df_post['post_alt'].apply(purify)
    df_post.drop(columns=['post_title'], inplace=True)

    df_post['dl_label'] = ""
    df_post['dl_pwd'] = ""
    df_post['dl_update'] = ""
    df_post['dl_remark'] = ""
    df_post['txt_matched'] = False

    # merge dl to post
    with open(DL_FILE, 'r', encoding='utf-8') as f:
        global _prefix
        _ = f.readlines()
        # <html><head><meta name="color-scheme" content="light dark"></head><body><pre style="word-wrap: break-word; white-space: pre-wrap;"> 网址前缀：wenku8.lanzov.com/
        _prefix = _[0].split('：')[-1].strip()
        # print(f"[DEBUG] DL prefix: {_prefix}")
        lines = _[2:]
        for line in lines:
            parts = line.strip().split()
            if len(parts) < 4:
                continue
            mask = df_post['post_pure'].str.match(purify(parts[-1]))
            if mask.any():
                df_post.loc[mask, 'dl_update'] = parts[0]
                df_post.loc[mask, 'dl_label'] = parts[1]
                df_post.loc[mask, 'dl_pwd'] = parts[2]
                if len(parts) > 4:
                    if parts[3][:2] == '更新' or parts[3][:2] == '补全':
                        df_post.loc[mask, 'dl_remark'] = parts[3][2:]
            #     if mask.sum() > 1:
            #         print(f'[WARN] {mask.sum()} entries matched for {parts[3]}')
            # else:
            #     print(f'[WARN] Failed to match {parts[3]}')
    
    # merge post to txt
    df_txt = pd.read_csv(TXT_LIST_FILE, encoding='utf-8')
    df_txt['txt_pure'] = df_txt['title'].apply(purify) # 4
    df_txt['volume'] = '' # 5
    df_txt['dl_label'] = '' # 6
    df_txt['dl_pwd'] = '' # 7
    df_txt['dl_update'] = None # 8
    df_txt['dl_remark'] = '' # 9
    df_txt['novel_title'] = '' # 10
    df_txt['novel_link'] = '' # 11
    for i in range(len(df_txt)):
        _title = df_txt.iloc[i, 0]
        if _title in IGNORED_TITLES:
            continue
        mask = df_post['post_pure'].str.match(df_txt.iloc[i, 4]) & (df_post['txt_matched'] == False)
        match = None
        if mask.any():
            match = mask[mask].index[0]
            # if mask.sum() > 1:
            #     print(f'[WARN] {mask.sum()} entries matched for {_title}')
            #     for j in range(len(df_post)):
            #         if mask[j]:
            #             print(f'    {df_post.iloc[j]["novel_title"]}')
        else:
            mask = df_post['post_alt_pure'].str.match(df_txt.iloc[i, 4]) & (df_post['txt_matched'] == False)
            if mask.any():
                match = mask[mask].index[0]
                # if mask.sum() > 1:
                #     print(f'[WARN] {mask.sum()} entries matched for {_title}')
                #     for j in range(len(df_post)):
                #         if mask[j]:
                #             print(f'    {df_post.iloc[j]["novel_title"]}')
        if match is not None:
            df_txt.iloc[i, 5] = df_post.iloc[match]['volume']
            df_txt.iloc[i, 6] = df_post.iloc[match]['dl_label']
            df_txt.iloc[i, 7] = df_post.iloc[match]['dl_pwd']
            df_txt.iloc[i, 8] = df_post.iloc[match]['dl_update']
            df_txt.iloc[i, 9] = df_post.iloc[match]['dl_remark']
            df_txt.iloc[i, 10] = df_post.iloc[match]['novel_title']
            df_txt.iloc[i, 11] = df_post.iloc[match]['novel_link']
            df_post.iloc[match, -1] = True
    
    _mask = df_post['txt_matched'] == False
    for y in df_post[_mask].itertuples():
        if y.dl_label == "":
            continue
        df_txt.loc[len(df_txt)] = ["", "", None, "", "", y.volume, y.dl_label, y.dl_pwd, y.dl_update, y.dl_remark, y.novel_title, y.novel_link]
    
    df_txt['title'] = df_txt.apply(lambda x: x['novel_title'] if x['novel_title'] else x['title'], axis=1)
    df_txt['update'] = df_txt.apply(lambda x: x['dl_update'] if x['dl_update'] else x['date'], axis=1)
    df_txt['main'] = df_txt['title'].apply(lambda x: x[:x.rfind('(')] if x[-1] == ')' else x)
    df_txt['alt'] = df_txt['title'].apply(lambda x: x[x.rfind('(')+1:-1] if x[-1] == ')' else "")
    df_txt.drop(columns=['title', 'date', 'txt_pure', 'novel_title'], inplace=True)
    df_txt.sort_values(by=['update'], ascending=False, inplace=True)
    df_txt.to_csv(MERGED_CSV, index=False, encoding='utf-8-sig')

# ========== HTML Generation ==========
starme = '<iframe style="margin-left: 2px; margin-bottom:-5px;" frameborder="0" scrolling="0" width="81px" height="20px" src="https://ghbtns.com/github-btn.html?user=mojimoon&repo=wenku8&type=star&count=true" ></iframe>'
def create_table_merged(df):
    rows = []
    for _, row in df.iterrows():
        _l, _m, _a, _txt, _dll, _u, _at, _v, _r = row['novel_link'], row['main'], row['alt'], row['download_url'], row['dl_label'], row['update'], row['author'], row['volume'], row['dl_remark']
        novel_link = None if pd.isna(_l) else _l
        title_html = f'<a href="{novel_link}" target="_blank">{_m}</a>' if novel_link else _m
        alt_html = '' if pd.isna(_a) else f"<span class='at'>{_a}</span>"
        txt_dl = '' if pd.isna(_txt) else f"<a href='{_txt}' target='_blank'>下载</a> <a href='https://ghfast.top/{_txt}' target='_blank'>镜像</a>"
        volume = '' if pd.isna(_v) else f'({_v})'
        remark = '' if pd.isna(_r) else f" <span class='bt'>{_r}</span>"
        lz_dl = '' if pd.isna(_dll) else f"<a href='https://{_prefix}/{_dll}' target='_blank'>{volume}</a>{remark}"
        date = '' if pd.isna(_u) else _u
        author = '' if pd.isna(_at) else _at
        lz_pwd = '' if pd.isna(_dll) else row['dl_pwd']
        rows.append(
            f"<tr><td>{title_html}{alt_html}</td>"
            f"<td class='au'>{author}</td><td>{lz_dl}</td><td>{lz_pwd}</td>"
            f"<td class='dl'>{txt_dl}</td><td class='yd'>{date}</td></tr>"
        )
    return ''.join(rows)

def create_html_merged():
    df = pd.read_csv(MERGED_CSV, encoding='utf-8-sig')
    table = create_table_merged(df)
    today = time.strftime('%Y-%m-%d', time.localtime())
    html = (
        '<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8">'
        '<meta name="viewport"content="width=device-width,initial-scale=1.0">'
        '<meta name="keywords"content="轻小说,sf轻小说,dmzj轻小说,日本轻小说,动漫小说,轻小说电子书,轻小说EPUB下载">'
        '<meta name="description"content="轻小说文库 EPUB 下载，支持搜索关键字、跳转至源站和蓝奏云下载，已进行移动端适配。">'
        '<meta name="author"content="mojimoon"><title>轻小说文库 EPUB 下载+</title>'
        f'<link rel="stylesheet"href="{JSDELIVR_CDN}style.css"></head><body>'
        '<h1 onclick="window.location.reload()">轻小说文库 EPUB 下载+</h1>'
        f'<h4>({today}) <a href="https://github.com/mojimoon">mojimoon</a>/<a href="https://github.com/mojimoon/wenku8">wenku8</a> {starme}</h4>'
        '<span>所有内容均收集于网络，仅供学习交流使用。'
        '特别感谢 <a href="https://www.wenku8.net/modules/article/reviewslist.php?keyword=8691&charset=utf-8">酷儿加冰</a> 和 <a href="https://github.com/ixinzhi">布客新知</a> 整理。</span>'
        '<span class="at">最新为 Calibre 生成 EPUB，括号内为最新卷数；年更为纯文本 EPUB。</span>'
        '<div class="right-controls"><a href="./epub.html">'
        '<button class="btn"id="gotoButton">切换到仅 EPUB 源，加载更快</button></a>'
        '<button class="btn"id="themeToggle">主题</button>'
        '<button class="btn"id="clearInput">清除</button></div>'
        '<div class="search-bar"><input type="text"id="searchInput"placeholder="搜索标题或作者">'
        '<button class="btn"id="randomButton">随机</button></div>'
        '<table><thead><tr><th>标题</th><th>作者</th><th>最新</th><th>密码</th><th>年更</th><th>更新</th></tr>'
        '</thead><tbody id="novelTableBody">'
        f'{table}</tbody></table><script src="{JSDELIVR_CDN}script_merged.js"></script>'
        '</body></html>'
    )
    with open(MERGED_HTML, 'w', encoding='utf-8') as f:
        f.write(html)

def create_table_epub(df):
    rows = []
    for _, row in df.iterrows():
        _l, _m, _a, _dll, _at, _v, _r = row['novel_link'], row['main'], row['alt'], row['dl_label'], row['author'], row['volume'], row['dl_remark']
        novel_link = None if pd.isna(_l) else _l
        title_html = f'<a href="{novel_link}" target="_blank">{_m}</a>' if novel_link else _m
        alt_html = '' if pd.isna(_a) else f"<span class='at'>{_a}</span>"
        volume = '' if pd.isna(_v) else f'({_v})'
        remark = '' if pd.isna(_r) else f" <span class='bt'>{_r}</span>"
        lz_dl = '' if pd.isna(_dll) else f"<a href='https://{_prefix}/{_dll}' target='_blank'>{volume}</a>{remark}"
        author = '' if pd.isna(_at) else _at
        rows.append(
            f"<tr><td>{title_html}{alt_html}</td>"
            f"<td class='au'>{author}</td><td>{lz_dl}</td><td>{row['dl_pwd']}</td>"
            f"<td class='yd'>{row['update']}</td></tr>"
        )
    return ''.join(rows)

def create_html_epub():
    df = pd.read_csv(MERGED_CSV, encoding='utf-8-sig')
    df = df[df['dl_label'].notna()]
    table = create_table_epub(df)
    today = time.strftime('%Y-%m-%d', time.localtime())
    html = (
        '<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8">'
        '<meta name="viewport"content="width=device-width,initial-scale=1.0">'
        '<meta name="keywords"content="轻小说,sf轻小说,dmzj轻小说,日本轻小说,动漫小说,轻小说电子书,轻小说EPUB下载">'
        '<meta name="description"content="轻小说文库 EPUB 下载，支持搜索关键字、跳转至源站和蓝奏云下载，已进行移动端适配。">'
        '<meta name="author"content="mojimoon"><title>轻小说文库 EPUB 下载</title>'
        f'<link rel="stylesheet"href="{JSDELIVR_CDN}style.css"></head><body>'
        '<h1 onclick="window.location.reload()">轻小说文库 EPUB 下载</h1>'
        f'<h4>({today}) <a href="https://github.com/mojimoon">mojimoon</a>/<a href="https://github.com/mojimoon/wenku8">wenku8</a> {starme}</h4>'
        '<span>所有内容均收集于网络，仅供学习交流使用。'
        '特别感谢 <a href="https://www.wenku8.net/modules/article/reviewslist.php?keyword=8691&charset=utf-8">酷儿加冰</a> 整理。括号内为最新卷数。</span>'
        '<div class="right-controls"><a href="./index.html">'
        '<button class="btn"id="gotoButton">切换到 EPUB/TXT 源，内容更全</button></a>'
        '<button class="btn"id="themeToggle">主题</button>'
        '<button class="btn"id="clearInput">清除</button></div>'
        '<div class="search-bar"><input type="text"id="searchInput"placeholder="搜索标题或作者">'
        '<button class="btn"id="randomButton">随机</button></div>'
        '<table><thead><tr><th>标题</th><th>作者</th><th>蓝奏</th><th>密码</th><th>更新</th></tr>'
        '</thead><tbody id="novelTableBody">'
        f'{table}</tbody></table><script src="{JSDELIVR_CDN}script_merged.js"></script>'
        '</body></html>'
    )
    with open(EPUB_HTML, 'w', encoding='utf-8') as f:
        f.write(html)

def safe_filename(name: str) -> str:
    safe = re.sub(r'[\\/:*?"<>|]+', '_', name).strip()
    return safe[:120] if safe else 'untitled'

def unique_path(base_dir: str, filename: str) -> str:
    root, ext = os.path.splitext(filename)
    out = os.path.join(base_dir, filename)
    i = 1
    while os.path.exists(out):
        out = os.path.join(base_dir, f'{root}_{i}{ext}')
        i += 1
    return out

def first_locator(scope, selectors):
    for sel in selectors:
        loc = scope.locator(sel)
        if loc.count() > 0:
            return loc.first
    return None

def all_scopes(page):
    scopes = [page]
    for fr in page.frames:
        if fr != page.main_frame:
            scopes.append(fr)
    return scopes

def first_locator_any_scope(page, selectors):
    for scope in all_scopes(page):
        loc = first_locator(scope, selectors)
        if loc is not None:
            return loc
    return None

def timeout_left_ms(deadline_ts: float, min_ms: int = 1) -> int:
    left = int((deadline_ts - time.monotonic()) * 1000)
    return left if left > min_ms else min_ms

def fill_lanzou_password(page, pwd: str):
    if not pwd:
        print('[WARN] Empty lanzou password for this entry, skip password fill.')
        return
    for _ in range(3):
        pwd_loc = first_locator_any_scope(page, [
            '#pwd',
            'input[name="pwd"]',
            'input[type="password"]',
            'input[id*="pwd"]',
            'input[placeholder*="密码"]',
            'input:not([type="hidden"])',
        ])
        if pwd_loc is not None:
            try:
                pwd_loc.fill(pwd)
            except Exception:
                pass
            submit_loc = first_locator_any_scope(page, [
                '#sub',
                'button:has-text("确定")',
                'button:has-text("提取")',
                'input[type="submit"]',
                'input[type="button"]',
                'text=确定',
                'text=提取',
            ])
            if submit_loc is not None:
                try:
                    submit_loc.click(force=True)
                except Exception:
                    try:
                        pwd_loc.press('Enter')
                    except Exception:
                        pass
            else:
                try:
                    pwd_loc.press('Enter')
                except Exception:
                    pass
            page.wait_for_timeout(1500)
            print('[INFO] Lanzou password submitted.')
            return
        page.wait_for_timeout(800)
    print('[WARN] Lanzou password input not found, continue without submit.')

def click_and_follow(page, node, timeout_ms: int):
    context = page.context
    known = set(context.pages)
    load_timeout = max(3000, min(timeout_ms, 15000))
    try:
        with context.expect_page(timeout=min(timeout_ms, 5000)) as new_page_info:
            node.click(force=True)
        new_page = new_page_info.value
        new_page.wait_for_load_state('domcontentloaded', timeout=load_timeout)
        return new_page
    except Exception:
        try:
            node.click(force=True)
        except Exception:
            return page
        page.wait_for_timeout(800)
        for p in context.pages:
            if p not in known:
                p.wait_for_load_state('domcontentloaded', timeout=load_timeout)
                return p
        return page

def try_click_download(page, node, timeout_ms: int):
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    try:
        with page.expect_download(timeout=timeout_ms) as dl_info:
            node.click(force=True)
        return dl_info.value
    except PlaywrightTimeoutError:
        return None
    except Exception:
        return None

def select_bundle_file_page(page, timeout_ms: int):
    quick_bundle = first_locator_any_scope(page, [
        'a:has-text("合集.zip")',
        'a:has-text("合集.7z")',
        'a:has-text("合集.rar")',
        'a:has-text("合集")',
    ])
    if quick_bundle is not None:
        return click_and_follow(page, quick_bundle, timeout_ms)

    for scope in all_scopes(page):
        anchors = scope.locator('a')
        count = min(anchors.count(), 120)
        bundle_candidates = []
        for i in range(count):
            node = anchors.nth(i)
            try:
                txt = node.inner_text(timeout=250).strip()
            except Exception:
                continue
            txt_l = txt.lower()
            if '合集' not in txt:
                continue
            if not any(ext in txt_l for ext in ['.zip', '.7z', '.rar']):
                continue
            bundle_candidates.append(node)
        if bundle_candidates:
            return click_and_follow(page, bundle_candidates[0], timeout_ms)
    return None

def open_normal_download_page(page, timeout_ms: int):
    normal_loc = first_locator_any_scope(page, [
        'a:has-text("普通下载")',
        'button:has-text("普通下载")',
        'input[value="普通下载"]',
        'a#tourl',
        'text=普通下载',
    ])
    if normal_loc is None:
        return page, None
    direct_download = try_click_download(page, normal_loc, timeout_ms=min(timeout_ms, 12000))
    if direct_download is not None:
        return page, direct_download
    return click_and_follow(page, normal_loc, timeout_ms), None

def resolve_verify_and_download(page, timeout_ms: int, depth: int = 0):
    if depth > 2:
        return None
    verify_loc = first_locator_any_scope(page, [
        'button:has-text("验证并下载")',
        'a:has-text("验证并下载")',
        'input[value="验证并下载"]',
        'text=验证并下载',
    ])
    if verify_loc is not None:
        try:
            verify_loc.click(force=True)
            page.wait_for_timeout(2200)
        except Exception:
            pass

    for _ in range(4):
        for sel in [
            'button:has-text("即刻下载")',
            'button:has-text("立即下载")',
            'a:has-text("即刻下载")',
            'a:has-text("立即下载")',
            'button:has-text("普通下载")',
            'a:has-text("普通下载")',
            'a#tourl',
            'button:has-text("下载")',
            'a:has-text("下载")',
            'a[href*="down"]',
            'a[href*="file"]',
            'text=即刻下载',
            'text=立即下载',
        ]:
            for scope in all_scopes(page):
                loc = scope.locator(sel)
                if loc.count() == 0:
                    continue
                node = loc.first
                try:
                    if hasattr(node, 'is_enabled') and (not node.is_enabled()):
                        continue
                except Exception:
                    pass
                download = try_click_download(page, node, timeout_ms=min(timeout_ms, 15000))
                if download is not None:
                    return download
                try:
                    nxt = click_and_follow(page, node, timeout_ms=min(timeout_ms, 10000))
                    if nxt != page:
                        nested = resolve_verify_and_download(nxt, timeout_ms, depth + 1)
                        if nested is not None:
                            return nested
                except Exception:
                    continue
        page.wait_for_timeout(1500)
    return None

def download_one_lanzou(page, url: str, pwd: str, download_dir: str, title: str, timeout_ms: int):
    deadline_ts = time.monotonic() + (max(timeout_ms, 10000) / 1000.0)
    page.set_default_timeout(min(timeout_ms, 15000))
    page.goto(url, wait_until='domcontentloaded', timeout=min(timeout_left_ms(deadline_ts), 45000))
    print('[INFO] Lanzou page opened.')
    fill_lanzou_password(page, pwd)
    bundle_page = select_bundle_file_page(page, min(timeout_left_ms(deadline_ts), 20000))
    if bundle_page is None and pwd:
        # 密码页/iframe 异步加载时再尝试一次
        fill_lanzou_password(page, pwd)
        bundle_page = select_bundle_file_page(page, min(timeout_left_ms(deadline_ts), 20000))
    if bundle_page is None:
        return None, 'no_bundle'
    print('[INFO] Lanzou bundle page found.')
    if timeout_left_ms(deadline_ts) <= 1:
        return None, 'timeout'
    normal_page, download = open_normal_download_page(bundle_page, min(timeout_left_ms(deadline_ts), 20000))
    if download is None:
        download = resolve_verify_and_download(normal_page, min(timeout_left_ms(deadline_ts), 25000))
    if download is None:
        if timeout_left_ms(deadline_ts) <= 1:
            return None, 'timeout'
        return None, 'no_download'
    suggested = download.suggested_filename or 'bundle.zip'
    ext = os.path.splitext(suggested)[1].strip()
    if not ext:
        ext = '.zip'
    target_name = f'{safe_filename(title)}{ext}'
    target = unique_path(download_dir, target_name)
    download.save_as(target)
    return target, 'ok'

def download_lanzou_files(new_entries, download_dir: str, limit: int = 0, timeout_ms: int = 30000, headless: bool = True):
    if not os.path.exists(MERGED_CSV):
        print(f'[WARN] MERGED_CSV not found: {MERGED_CSV}')
        return
    prefix = _prefix.strip().replace('https://', '').replace('http://', '').rstrip('/')
    if not prefix:
        print('[WARN] Empty lanzou prefix, skip downloading.')
        return

    df = pd.read_csv(MERGED_CSV, encoding='utf-8-sig')
    if new_entries:
        links = {entry[3] for entry in new_entries if len(entry) > 3}
        if links:
            df = df[df['novel_link'].isin(links)]
    df = df[df['dl_label'].notna()].copy()
    if df.empty:
        print('[INFO] No lanzou entries to download.')
        return
    df['dl_label'] = df['dl_label'].astype(str).str.strip()
    df = df[df['dl_label'] != '']
    if limit > 0:
        df = df.head(limit)
    if df.empty:
        print('[INFO] No lanzou entries to download after filtering.')
        return

    os.makedirs(download_dir, exist_ok=True)
    try:
        from playwright.sync_api import sync_playwright
    except Exception as e:
        print(f'[ERROR] Playwright is required for lanzou auto download: {e}')
        return

    ok_cnt = 0
    fail_cnt = 0
    skip_cnt = 0
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=headless,
            args=['--no-sandbox', '--disable-setuid-sandbox']
        )
        context = browser.new_context(accept_downloads=True)
        for row in df.itertuples():
            label = str(row.dl_label).strip()
            pwd = '' if pd.isna(row.dl_pwd) else str(row.dl_pwd).strip()
            title = row.main if (hasattr(row, 'main') and isinstance(row.main, str) and row.main) else f'{label}'
            url = f'https://{prefix}/{label}'
            print(f'[INFO] downloading: {title} ({url})')
            page = context.new_page()
            try:
                out, status = download_one_lanzou(page, url, pwd, download_dir, title, timeout_ms)
                if status == 'ok' and out:
                    ok_cnt += 1
                    print(f'[INFO] saved: {out}')
                elif status == 'no_bundle':
                    skip_cnt += 1
                    print(f'[INFO] skip (no 合集 file): {url}')
                elif status == 'timeout':
                    fail_cnt += 1
                    print(f'[WARN] lanzou download timeout: {url}')
                else:
                    fail_cnt += 1
                    print(f'[WARN] no downloadable link found: {url}')
            except Exception as e:
                fail_cnt += 1
                print(f'[WARN] failed to download {url}: {e}')
            finally:
                try:
                    page.close()
                except Exception:
                    pass
        context.close()
        browser.close()

    print(f'[INFO] lanzou download done, success={ok_cnt}, skipped={skip_cnt}, failed={fail_cnt}, dir={download_dir}')

def parse_args():
    parser = argparse.ArgumentParser(description='wenku8 scraper and site generator')
    parser.add_argument(
        'scraper',
        nargs='?',
        default='steel',
        choices=['requests', 'playwright', 'steel'],
        help='scraper backend'
    )
    return parser.parse_args()

def main():
    if not os.path.exists(OUT_DIR):
        os.mkdir(OUT_DIR)
    if not os.path.exists(PUBLIC_DIR):
        os.mkdir(PUBLIC_DIR)
    
    new_entries, has_history = scrape()
    merge()
    create_html_merged()
    create_html_epub()
    # 首次初始化（无历史 post_list）时，仅尝试最新 1 条用于验证下载链路。
    if has_history:
        download_lanzou_files(
            new_entries,
            DOWNLOAD_DIR,
            limit=0,
            timeout_ms=90000,
            headless=True,
        )
    else:
        print('[INFO] First bootstrap run detected, only download the latest one for smoke test.')
        download_lanzou_files(
            new_entries[:1],
            DOWNLOAD_DIR,
            limit=1,
            timeout_ms=90000,
            headless=True,
        )

if __name__ == '__main__':
    args = parse_args()
    _scraper = args.scraper
    print(f'[INFO] Using scraper: {_scraper}')
    main()
